#!/usr/bin/env python3
"""
fetch_wyscout.py — download a whole league from Wyscout into one CSV.

Set COMP_SCOPE and TIMEFRAME below, run it, get a CSV.

    python fetch_wyscout.py

First run opens a browser and asks you to log in. After that it runs headless
with no browser at all.
"""

import json
import re
import sys
import time
import unicodedata
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

# ============================================================
# SET THESE
# ============================================================

COMP_SCOPE = "Germany. 2. Bundesliga"   # "<Country>. <League>", as Wyscout writes it
TIMEFRAME = "2025/2026"                 # season name, or "current_season" / "last_season"

OUT_DIR = Path("out")                   # where the CSV goes

# Do several leagues in one run: fill this in and COMP_SCOPE is ignored.
BATCH = [
    # "Germany. 2. Bundesliga",
    # "Germany. 3. Liga",
    # "Portugal. Liga 3",
]

# ============================================================
# constants read out of Wyscout's app-bundle.js — do not guess at these
# ============================================================

EXPORT_URL = "https://searchapi.wyscout.com/api/v1/search/export.xlsx"
RESULTS_URL = "https://searchapi.wyscout.com/api/v1/search/results.json"
COMPS_URL = "https://searchapi.wyscout.com/api/v1/competitions/advanced_search.json"

PLATFORM_URL = "https://wyscout.hudl.com/app/?"

TEMPLATE = Path("template.json")        # the captured export request
PROFILE = Path(".browser_profile")      # keeps you logged in between runs

PAGE_SIZE = 500                         # what the server gives per call
MAX_PAGES = 200                         # runaway guard
PAUSE = 1.0                             # seconds between calls
TIMEOUT = 300


def say(msg="", n=0):
    print("  " * n + str(msg), flush=True)


def slug(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w]+", "_", s).strip("_")


# ============================================================
# step 1 — get a template (browser, once)
# ============================================================

def capture_template():
    """Open a browser, let you log in, click Export once, keep the request.

    We capture rather than construct the request because the `columns` block in
    it is built from your display preset inside the page. Wyscout's column
    catalogue has 51 definitions that the server expands into your 115 output
    columns, so rebuilding that block by hand would not reproduce your export.
    Copying the real one does, exactly.
    """
    from playwright.sync_api import sync_playwright

    say("No template.json yet — opening a browser to capture one.")
    PROFILE.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, accept_downloads=True,
            viewport={"width": 1600, "height": 950},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PLATFORM_URL, wait_until="domcontentloaded", timeout=90_000)

        say("")
        say("-" * 60)
        say("In the browser window:")
        say("  1. log in if it asks")
        say("  2. go to Advanced Search")
        say("  3. pick the column layout you want (this is what gets saved)")
        say("  4. do NOT click Export — this script clicks it")
        say("-" * 60)
        input("press ENTER when Advanced Search is on screen > ")

        captured = {}

        def on_request(req):
            if "export.xlsx" in req.url and req.method == "POST":
                captured["url"] = req.url
                captured["body"] = req.post_data
                captured["headers"] = {
                    k: v for k, v in req.headers.items()
                    if k.lower() in ("content-type", "accept", "origin", "referer", "user-agent")
                }

        page.on("request", on_request)

        say("clicking Export to Excel...")
        try:
            page.get_by_text("Export to Excel", exact=False).first.click(timeout=15_000)
        except Exception:
            say("! couldn't find the Export button — click it yourself now.")

        # >500 results shows a confirm popup first; click through it.
        for _ in range(30):
            if captured:
                break
            try:
                page.get_by_text("Export to Excel", exact=False).nth(1).click(timeout=1000)
            except Exception:
                pass
            page.wait_for_timeout(500)

        ctx.close()

    if not captured:
        say("Nothing captured. Re-run and click Export to Excel yourself when asked.")
        sys.exit(1)

    url, _, qs = captured["url"].partition("?")
    TEMPLATE.write_text(json.dumps({
        "url": url,
        "query": dict(p.split("=", 1) for p in qs.split("&") if "=" in p),
        "headers": captured["headers"],
        "body": json.loads(captured["body"]),
    }, indent=2), encoding="utf-8")

    say(f"saved {TEMPLATE}")
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def load_template():
    if TEMPLATE.exists():
        return json.loads(TEMPLATE.read_text(encoding="utf-8"))
    return capture_template()


# ============================================================
# step 2 — find the competition id and season id
# ============================================================

def get_competitions(t, session):
    q = {**t["query"], "lang": t["body"].get("language", "en"),
         "women_mode": "false", "withSeasons": "t"}
    r = session.get(COMPS_URL, params=q, headers=t["headers"], timeout=TIMEOUT)
    check(r, t)
    return flatten(r.json())


def flatten(data, area=None, out=None):
    """Pull {area, name, id, seasons} out of the competitions tree."""
    out = [] if out is None else out
    if isinstance(data, list):
        for x in data:
            flatten(x, area, out)
        return out
    if not isinstance(data, dict):
        return out

    name = data.get("name") or data.get("label")
    cid = data.get("id") or data.get("wyId")
    grouping = isinstance(data.get("competitions"), list)
    if grouping and name:
        area = name                      # this node is the country
    if name and cid is not None and not grouping:
        out.append({
            "area": area,
            "name": name,
            "id": cid,
            "seasons": {
                str(s.get("name")): s.get("id")
                for s in (data.get("seasons") or []) if isinstance(s, dict)
            },
        })
    for k, v in data.items():
        # season entries look identical to competitions (id + name); descending
        # into them would invent a league called "2025/2026".
        if k not in ("seasons", "area", "rounds", "groups") and isinstance(v, (list, dict)):
            flatten(v, area, out)
    return out


def resolve(scope, timeframe, comps):
    """'Germany. 2. Bundesliga' -> (competition id, season id)."""
    area, _, league = scope.partition(". ")      # first ". " only; "2. Bundesliga" survives
    area, league = area.strip(), league.strip()

    hit = next((c for c in comps
                if c["name"].lower() == league.lower()
                and area.lower() in str(c["area"] or "").lower()), None)
    if not hit:
        hit = next((c for c in comps if c["name"].lower() == league.lower()), None)
    if not hit:
        near = [f"{c['area']}. {c['name']}" for c in comps
                if league.lower()[:6] in c["name"].lower()][:8]
        say(f"! no competition called {scope!r}")
        if near:
            say("did you mean:", 1)
            for n in near:
                say(n, 2)
        return None, None

    season = hit["seasons"].get(timeframe)
    if season is None and timeframe not in ("current_season", "last_season", "last_5", "default"):
        say(f"! {hit['name']} has no season {timeframe!r}. available: "
            f"{', '.join(sorted(hit['seasons'])) or 'none listed'}")
        return None, None

    return hit["id"], (season if season is not None else timeframe)


# ============================================================
# step 3 — download
# ============================================================

def build_body(t, comp_id, season):
    """Point the captured request at a different league and season.

    `competition` is the Competitions-scope field and holds a comma-separated
    list of competition ids (the UI's "Top 5 EU leagues" option is literally
    "7,8,9,13,16"). `youth_stats` is derived from it in the page, so we set it
    the same way the page does.
    """
    b = deepcopy(t["body"])
    s = b.setdefault("search", {})
    s["competition"] = str(comp_id)
    s["youth_stats"] = "false"
    s["time_frame"] = str(season)
    return b


def check(r, t):
    if r.status_code in (401, 403):
        say("token expired — recapturing.")
        TEMPLATE.unlink(missing_ok=True)
        raise Expired()
    if r.status_code == 429:
        sys.exit("rate limited (429). raise PAUSE and rerun.")
    if r.status_code >= 400:
        sys.exit(f"HTTP {r.status_code}: {r.text[:300]}")


class Expired(Exception):
    pass


def expected_total(t, session, body):
    """Ask the API how many players match, so we can verify we got them all."""
    try:
        q = {**t["query"], **{k: str(v) for k, v in body["search"].items()
                              if isinstance(v, (str, int, float))}}
        r = session.get(RESULTS_URL, params=q, headers=t["headers"], timeout=TIMEOUT)
        if r.status_code >= 400:
            return None
        return find_total(r.json())
    except Exception:
        return None


def find_total(node):
    if isinstance(node, dict):
        if isinstance(node.get("total_items"), int):
            return node["total_items"]
        for v in node.values():
            got = find_total(v)
            if got is not None:
                return got
    elif isinstance(node, list):
        for v in node:
            got = find_total(v)
            if got is not None:
                return got
    return None


def download(t, session, body, label):
    frames, total = [], 0
    for page in range(MAX_PAGES):
        b = deepcopy(body)
        b["count"] = PAGE_SIZE
        b["page"] = page
        r = session.post(t["url"], params=t["query"],
                         headers={**t["headers"], "Content-Type": "application/json"},
                         data=json.dumps(b), timeout=TIMEOUT)
        check(r, t)
        if r.content[:2] != b"PK":
            sys.exit(f"expected an xlsx, got {r.headers.get('content-type')}: {r.text[:200]}")
        df = pd.read_excel(BytesIO(r.content), engine="openpyxl")
        if df.empty:
            break
        frames.append(df)
        total += len(df)
        say(f"page {page}: {len(df)} rows (total {total})", 1)
        if len(df) < PAGE_SIZE:
            break
        time.sleep(PAUSE)
    else:
        say(f"! stopped at MAX_PAGES={MAX_PAGES}", 1)

    if not frames:
        return pd.DataFrame()

    cols = list(frames[0].columns)
    for f in frames[1:]:
        if list(f.columns) != cols:
            say("! a page came back with different columns", 1)
            break
    # No dedup. Pages are disjoint slices of one ordered list, so duplicates
    # can't happen — and your data has three different players called
    # "M. Schulz" at Preußen Münster, which a dedup on name+team would delete.
    return pd.concat([f.reindex(columns=cols) for f in frames], ignore_index=True)


# ============================================================
# main
# ============================================================

def one(t, session, scope, comps):
    comp_id, season = resolve(scope, TIMEFRAME, comps)
    if comp_id is None:
        return False

    say(f"{scope}  (competition={comp_id}, time_frame={season})")
    body = build_body(t, comp_id, season)

    want = expected_total(t, session, body)
    df = download(t, session, body, scope)
    if df.empty:
        say("nothing came back", 1)
        return False

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{slug(scope)}__{slug(TIMEFRAME)}.csv"
    df.to_csv(dest, index=False, encoding="utf-8-sig")
    say(f"-> {dest}  ({len(df)} rows, {len(df.columns)} cols)", 1)

    if want is not None and want != len(df):
        say(f"! API says {want} players but we saved {len(df)}", 1)
    elif want is not None:
        say(f"row count matches the API's total ({want})", 1)
    return True


def main():
    t = load_template()
    session = requests.Session()

    try:
        comps = get_competitions(t, session)
    except Expired:
        t = load_template()
        comps = get_competitions(t, session)

    say(f"{len(comps)} competitions visible to your account")
    say()

    targets = BATCH or [COMP_SCOPE]
    ok = 0
    for scope in targets:
        try:
            ok += one(t, session, scope, comps)
        except Expired:
            t = load_template()
            ok += one(t, session, scope, comps)
        say()

    say(f"{ok}/{len(targets)} done")


if __name__ == "__main__":
    main()

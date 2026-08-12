#!/usr/bin/env python3
"""
fetch_wyscout.py — download a whole league from Wyscout into one CSV.

Set COMP_SCOPE and TIMEFRAME below, run it, get a CSV.

    python fetch_wyscout.py

First run opens a browser and asks you to log in. After that it runs headless
with no browser at all.
"""

import json
import os
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

HERE = Path(__file__).resolve().parent

# Where the CSVs go. Relative paths are resolved against THIS FILE's folder, not
# whatever directory you happen to run python from — so `python fetch\fetch_wyscout.py`
# from the repo root still writes into fetch\out, not the repo root.
OUT_DIR = HERE / "out"

# Where your login lives. Nothing secret is ever written inside the repo.
# Default: C:\Users\<you>\.wyscout   (or ~/.wyscout on mac/linux)
# Override with the WYSCOUT_HOME environment variable, e.g.
#     set WYSCOUT_HOME=C:\Users\hasht\Desktop\wyscout-auth
_WYSCOUT_HOME = os.environ.get("WYSCOUT_HOME")
AUTH_DIR = Path(_WYSCOUT_HOME or Path.home() / ".wyscout").expanduser().resolve()

# Filters inherited from the capture that get removed before downloading.
# These are slider positions (age defaults to 8-35 in Wyscout) that would
# silently narrow every league — your own sample has a 40-year-old in it.
# Set to [] to keep whatever was on screen during the capture instead.
DROP_FILTERS = ["age", "height", "weight"]

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

TEMPLATE = AUTH_DIR / "template.json"        # captured export request (holds the token)
PROFILE = AUTH_DIR / "browser_profile"       # Chromium profile (holds the cookies)
ENV_FILE = AUTH_DIR / ".env"                 # optional: your Wyscout email/password


def read_env_file():
    """Read AUTH_DIR/.env  ->  {KEY: value}. Missing file is fine.

    Lives next to the token, outside the repo, so there is one folder to protect
    and one folder to delete. Real environment variables win over this file.
    """
    values = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip("'\"")
    return values


def secret(name):
    """Environment variable first, then AUTH_DIR/.env."""
    return os.environ.get(name) or read_env_file().get(name)

PAGE_SIZE = 500                         # what the server gives per call
MAX_PAGES = 200                         # runaway guard
PAUSE = 1.0                             # seconds between calls
DRY_RUN = False                         # set by --dry-run
TIMEOUT = 300


def say(msg="", n=0):
    print("  " * n + str(msg), flush=True)


def slug(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w]+", "_", s).strip("_")


# ============================================================
# step 1 — get a template (browser, once)
# ============================================================

def needs_login(page):
    """True if the platform is showing a login form rather than the app shell."""
    try:
        if page.locator("input[type='password'], #login_password").first.is_visible(timeout=3000):
            return True
    except Exception:
        pass
    try:
        # `ae` is the platform shell's global; if it's there, we're inside the app.
        return not page.evaluate("typeof ae !== 'undefined' && !!ae.getCmp")
    except Exception:
        return True


def capture_template():
    """Open a browser, let you log in, click Export once, keep the request.

    We capture rather than construct the request because the `columns` block in
    it is built from your display preset inside the page. Wyscout's column
    catalogue has 51 definitions that the server expands into your 115 output
    columns, so rebuilding that block by hand would not reproduce your export.
    Copying the real one does, exactly.
    """
    from playwright.sync_api import sync_playwright

    say(f"No {TEMPLATE.name} yet — opening a browser to capture one.")
    PROFILE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, accept_downloads=True,
            viewport={"width": 1600, "height": 950},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PLATFORM_URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(5000)

        # --- log in, only if the saved session has gone stale ---
        if needs_login(page):
            email = secret("WYSCOUT_EMAIL")
            password = secret("WYSCOUT_PASSWORD")
            if email and password:
                say(f"logging in as {email}...")
                try:
                    page.fill("input[type='email'], input[name='username'], #login_username", email)
                    page.fill("input[type='password'], #login_password", password)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(10_000)
                except Exception as e:
                    say(f"! auto login failed ({e}) — do it by hand in the window")
                    input("press ENTER once you're logged in > ")
            else:
                say("")
                say("-" * 58)
                say("Log in in the browser window (only needed when the saved")
                say("session expires — weeks, not runs).")
                say(f"To skip this, put your credentials in {ENV_FILE}")
                say("-" * 58)
                input("press ENTER once you're logged in > ")
            page.wait_for_timeout(3000)

        # --- open Advanced Search without touching the UI ---
        # The platform shell registers it as an app whose button runs
        #   ae.getCmp('app').showAdvancedSearchPopUp(...)
        # and loads it in an iframe from https://wyscout-apps.hudl.com/advanced-search/
        # with the access token on the query string. We call that directly, read
        # the iframe's URL, then load the same URL as a normal page — which
        # avoids frame juggling entirely.
        say("opening Advanced Search...")
        for js in ("ae.getCmp('app').showAdvancedSearchPopUp()",
                   "ae.app().loadApp({appName:'advanced_search',track:false})"):
            try:
                page.evaluate(js)
                break
            except Exception:
                continue

        src = None
        for _ in range(40):
            page.wait_for_timeout(500)
            src = page.evaluate(
                """() => {
                    const f = [...document.querySelectorAll('iframe')]
                        .map(i => i.src).find(s => s && s.includes('advanced-search'));
                    return f || null;
                }"""
            )
            if src:
                break

        if src:
            say("got the Advanced Search URL, loading it directly")
            page.goto(src, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(8000)
        else:
            say("! couldn't open it automatically — open Advanced Search yourself")
            input("press ENTER when it's on screen > ")

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

        # --- select ALL columns before capturing ---
        # The captured `columns` block is whatever preset is on screen. The
        # default "General" preset is only 16 columns; we want all 115. Open the
        # column editor (the grid icon next to DISPLAY), tick "All columns",
        # Apply. If any step misses, we fall back to asking you to do it by hand.
        say("selecting all columns...")
        picked = False
        try:
            # open the column editor — icon sits right of the DISPLAY dropdown
            for sel in ("[class*='columns'] [class*='edit']",
                        "button[class*='columnsEditor']",
                        "[data-qa*='columns']"):
                try:
                    page.locator(sel).first.click(timeout=2000)
                    break
                except Exception:
                    continue
            page.wait_for_timeout(1500)
            # tick "All columns"
            page.get_by_text("All columns", exact=True).first.click(timeout=4000)
            page.wait_for_timeout(500)
            # apply
            page.get_by_role("button", name=re.compile(r"^\s*apply\s*$", re.I)) \
                .first.click(timeout=4000)
            page.wait_for_timeout(2500)
            picked = True
            say("all columns selected")
        except Exception:
            pass

        if not picked:
            say("")
            say("-" * 58)
            say("Couldn't tick 'All columns' automatically.")
            say("In the browser: click the columns icon next to DISPLAY,")
            say("tick 'All columns' (bottom-left), click APPLY.")
            say("-" * 58)
            input("press ENTER once all columns show in the table > ")

        say("clicking Export to Excel...")
        try:
            page.get_by_text("Export to Excel", exact=False).first.click(timeout=15_000)
        except Exception:
            say("! couldn't find the Export button — click it yourself now.")

        # Whenever the result set is >= 500 Wyscout interrupts with:
        #   "You are trying to export a large set of data. Only first 500
        #    records will be exported."   [Download anyway] [Cancel]
        # The export POST does not fire until that is confirmed.
        for _ in range(60):
            if captured:
                break
            for label in ("Download anyway", "Download", "Export", "OK", "Confirm"):
                try:
                    page.get_by_role("button", name=re.compile(rf"^\s*{label}\s*$", re.I)) \
                        .first.click(timeout=600)
                    say(f"confirmed the warning ('{label}')")
                    break
                except Exception:
                    continue
            page.wait_for_timeout(500)

        ctx.close()

    if not captured:
        say("")
        say("Nothing captured — the export request never fired.")
        say("Usually this means a dialog was in the way. Re-run, and when the")
        say("browser opens click 'Export to Excel' then 'Download anyway' yourself;")
        say("the script watches the network and will pick it up either way.")
        sys.exit(1)

    url, _, qs = captured["url"].partition("?")
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
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
    for key in DROP_FILTERS:
        s.pop(key, None)
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

    body = build_body(t, comp_id, season)

    # Show the filters actually going out, before a single row is downloaded.
    say(scope)
    dropped = [k for k in DROP_FILTERS if k in t["body"].get("search", {})]
    say("filters being sent:", 1)
    for k, v in sorted(body["search"].items()):
        mark = "  <- from COMP_SCOPE" if k == "competition" else \
               "  <- from TIMEFRAME" if k == "time_frame" else ""
        say(f"{k} = {json.dumps(v, ensure_ascii=False)}{mark}", 2)

    if dropped:
        say(f"dropped from the capture: {', '.join(dropped)}  (see DROP_FILTERS)", 2)

    if DRY_RUN:
        say("dry run — stopping before download", 1)
        return False

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


def where():
    """Print every path this script will touch, and where each came from."""
    src = f"WYSCOUT_HOME={_WYSCOUT_HOME}" if _WYSCOUT_HOME else "default (WYSCOUT_HOME is not set)"
    say(f"login folder : {AUTH_DIR}")
    say(f"               from {src}", 1)
    say(f"  token      : {TEMPLATE}   {'exists' if TEMPLATE.exists() else 'not created yet'}")
    say(f"  cookies    : {PROFILE}   {'exists' if PROFILE.exists() else 'not created yet'}")
    say(f"  login      : {ENV_FILE}   {'exists' if ENV_FILE.exists() else 'not created (optional)'}")
    have = "yes" if (secret("WYSCOUT_EMAIL") and secret("WYSCOUT_PASSWORD")) else "no"
    say(f"               auto-login credentials found: {have}", 1)
    say()
    say(f"CSVs         : {OUT_DIR}")
    say(f"               always beside this script, whatever folder you run from", 1)
    say()
    say(f"script       : {Path(__file__).resolve()}")
    say(f"run from     : {Path.cwd()}")

    if not _WYSCOUT_HOME:
        say()
        say("To move the login somewhere else:")
        say('  setx WYSCOUT_HOME "C:\\path\\you\\want"', 1)
        say("then OPEN A NEW TERMINAL — setx does not affect the one it's typed in.", 1)


def main():
    global DRY_RUN
    DRY_RUN = "--dry-run" in sys.argv

    if "--where" in sys.argv:
        where()
        return

    say(f"login stored in : {AUTH_DIR}"
        f"{'' if _WYSCOUT_HOME else '   (default — WYSCOUT_HOME not set)'}")
    say(f"CSVs written to : {OUT_DIR}")
    say()

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

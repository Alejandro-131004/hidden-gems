#!/usr/bin/env python3
"""
generate.py — build a self-contained scouting dashboard from a Wyscout export.

Reads a player CSV/XLSX, keeps the attackers, treats the top-N by market value as
the "AI recommendations", computes the metrics, and writes ONE self-contained
`dashboard.html` — no server, no installs to view, opens in any browser.

The page has, in order:
  • a filter bar (number of recommendations, age range, value range, sort)
  • the top-3 podium, then the ranked list of the rest
  • a "context" section reproducing the imgs_demo plot types (except comparison):
        age-vs-value scatter, correlation heatmap, value-tier distributions,
        physical-vs-technical, and a pitch position map
  • a head-to-head section: search and pick up to 5 players, compared on a radar
        (defaults to the top 3), across the important attacker metrics

Version 2 (the report) is the same page's Print → Save as PDF: one button prints
the current filtered state, the other resets to the default state and prints —
each section starts on its own page. No extra dependency; the browser makes the PDF.

Run:
    python generate.py                     # uses ../data/sample.xlsx, writes ./dashboard.html
    python generate.py --data path.csv --out out.html

Auto-deploy to Netlify (needs `pip install requests`):
    Put two plain-text files ONE LEVEL ABOVE the repo (kept out of git):
        netlify.txt   -> your Netlify personal access token (nfp_...)
        site_id.txt   -> the Netlify site id to deploy to
    Then every run deploys automatically:
        python generate.py                 # builds AND deploys to that site
        python generate.py --no-deploy     # build only, skip the deploy
    Environment variables NETLIFY_AUTH_TOKEN / NETLIFY_SITE_ID override the files.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- config

ATTACK_CODES = {"CF", "LWF", "RWF", "LW", "RW", "LAMF", "RAMF", "AMF"}

# primary position code -> pitch (x,y), 0..100, attacking upward
PITCH_XY = {
    "AMF": (50, 66), "LAMF": (28, 64), "RAMF": (72, 64),
    "LW": (11, 72), "RW": (89, 72), "LWF": (20, 82), "RWF": (80, 82), "CF": (50, 86),
}

# the "important metrics" for attackers, given what the General export carries.
# key -> (label, how). Percentiled within the attacker pool for the radar.
RADAR_METRICS = [
    ("g90",     "Goals /90"),
    ("xg90",    "xG /90"),
    ("fin90",   "Finishing /90"),   # goals minus xG, per 90 (over/under-performance)
    ("minutes", "Minutes"),
    ("matches", "Matches"),
    ("youth",   "Youth"),           # inverted age
]
TECH_METRICS = [("g90", "Goals /90"), ("xg90", "xG /90"), ("fin90", "Finishing /90")]
PHYS_METRICS = [("height", "Height"), ("weight", "Weight")]
TIER_METRICS = [("g90", "Goals /90"), ("xg90", "xG /90"), ("minutes", "Minutes")]
CORR_METRICS = [("age", "Age"), ("minutes", "Minutes"), ("matches", "Matches"),
                ("goals", "Goals"), ("xg", "xG"), ("g90", "Goals /90"),
                ("xg90", "xG /90"), ("height", "Height"), ("weight", "Weight"),
                ("mv", "Market value")]


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return "".join(ch if ch.isalnum() else "_" for ch in s).strip("_").lower()


def load(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def build_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pos"] = df["Position"].astype("string").fillna("").map(lambda p: p.split(",")[0].strip())
    att = df[df["pos"].isin(ATTACK_CODES)].copy().reset_index(drop=True)

    num = lambda c: pd.to_numeric(att.get(c), errors="coerce")
    att["age"] = num("Age")
    att["minutes"] = num("Minutes played").fillna(0)
    att["matches"] = num("Matches played").fillna(0)
    att["goals"] = num("Goals").fillna(0)
    att["xg"] = num("xG").fillna(0)
    att["height"] = num("Height")
    att["weight"] = num("Weight")
    att["mv"] = num("Market value")

    p90 = (att["minutes"] / 90).replace(0, np.nan)
    att["g90"] = (att["goals"] / p90).fillna(0)
    att["xg90"] = (att["xg"] / p90).fillna(0)
    att["fin90"] = ((att["goals"] - att["xg"]) / p90).fillna(0)
    att["youth"] = -att["age"]   # higher = younger; percentiled later

    att["id"] = [f"{slug(n)}_{i}" for i, n in enumerate(att["Player"])]
    return att


def box_stats(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return {"min": 0, "q1": 0, "med": 0, "q3": 0, "max": 0}
    q = np.quantile(s, [0, .25, .5, .75, 1])
    return {"min": float(q[0]), "q1": float(q[1]), "med": float(q[2]),
            "q3": float(q[3]), "max": float(q[4])}


def compute(att: pd.DataFrame) -> dict:
    # radar percentiles within the attacker pool (0..100)
    pct = {}
    for key, _ in RADAR_METRICS:
        pct[key] = (att[key].rank(pct=True) * 100).round(1)

    players = []
    for i, r in att.iterrows():
        players.append({
            "id": r["id"], "name": r["Player"], "team": r["Team"], "pos": r["pos"],
            "age": None if pd.isna(r["age"]) else int(r["age"]),
            "minutes": int(r["minutes"]), "matches": int(r["matches"]),
            "goals": int(r["goals"]), "xg": round(float(r["xg"]), 2),
            "g90": round(float(r["g90"]), 2), "xg90": round(float(r["xg90"]), 2),
            "fin90": round(float(r["fin90"]), 2),
            "height": None if pd.isna(r["height"]) else int(r["height"]),
            "weight": None if pd.isna(r["weight"]) else int(r["weight"]),
            "mv": None if pd.isna(r["mv"]) else float(r["mv"]),
            "radar": [float(pct[k].iloc[i]) for k, _ in RADAR_METRICS],
        })

    # correlation matrix over the attacker pool
    cm = att[[k for k, _ in CORR_METRICS]].apply(pd.to_numeric, errors="coerce")
    corr = cm.corr().round(2).fillna(0).values.tolist()

    # value tiers (quartiles of market value among priced attackers)
    priced = att[att["mv"] > 0].copy()
    tiers = {}
    tier_names = ["Low", "Lower-mid", "Upper-mid", "Elite"]
    if len(priced) >= 8:
        priced["tier"] = pd.qcut(priced["mv"], 4, labels=tier_names, duplicates="drop")
        for key, _ in TIER_METRICS:
            tiers[key] = {t: box_stats(priced[priced["tier"] == t][key]) for t in tier_names}

    # physical vs technical: top vs bottom value quartile
    phystech = {}
    if len(priced) >= 8:
        hi_cut, lo_cut = priced["mv"].quantile(.75), priced["mv"].quantile(.25)
        hi, lo = priced[priced["mv"] >= hi_cut], priced[priced["mv"] <= lo_cut]
        for key, lab in (PHYS_METRICS + TECH_METRICS):
            phystech[key] = {"label": lab, "kind": "phys" if key in dict(PHYS_METRICS) else "tech",
                             "high": box_stats(hi[key]), "low": box_stats(lo[key])}

    scatter = [{"id": r["id"], "name": r["Player"], "age": None if pd.isna(r["age"]) else int(r["age"]),
                "mv": None if pd.isna(r["mv"]) else float(r["mv"])} for _, r in att.iterrows()]

    return {
        "players": players,
        "meta": {
            "nAttackers": len(att),
            "radar": [{"key": k, "label": l} for k, l in RADAR_METRICS],
            "tierMetrics": [{"key": k, "label": l} for k, l in TIER_METRICS],
            "tierNames": tier_names,
            "corrLabels": [l for _, l in CORR_METRICS],
            "pitch": PITCH_XY,
        },
        "corr": corr,
        "tiers": tiers,
        "phystech": phystech,
        "scatter": scatter,
    }


# ---------------------------------------------------------------- HTML

def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return HTML_TEMPLATE.replace("/*__DATA__*/", payload)


# ---------------------------------------------------------------- deploy

def deploy_netlify(html_path: Path, token: str, site_id: str | None = None,
                   site_name: str | None = None) -> dict:
    """Deploy the HTML to Netlify via the file-digest API.

    We declare the file by its SHA1 and PUT it to the path `/index.html`, so
    Netlify serves it with the correct `text/html` content type (a raw zip
    upload can end up served as text/plain — the "raw HTML" bug this fixes).

    Needs a Netlify personal access token — never hard-code it. With no site_id
    a new site is created.
    """
    import hashlib
    import requests  # local import so the generator runs without it when not deploying

    base = "https://api.netlify.com/api/v1"
    hdr = {"Authorization": f"Bearer {token}"}

    if not site_id:
        body = {"name": site_name} if site_name else {}
        r = requests.post(f"{base}/sites", headers=hdr, json=body, timeout=60)
        r.raise_for_status()
        site_id = r.json()["id"]

    payload = Path(html_path).read_bytes()
    sha1 = hashlib.sha1(payload).hexdigest()

    # 1) declare the deploy: map the served path -> file digest
    r = requests.post(f"{base}/sites/{site_id}/deploys",
                      headers={**hdr, "Content-Type": "application/json"},
                      json={"files": {"/index.html": sha1}}, timeout=60)
    r.raise_for_status()
    dep = r.json()
    dep_id = dep["id"]

    # 2) upload the file bytes if Netlify says it needs them (served type comes
    #    from the .html path, giving text/html)
    if sha1 in (dep.get("required") or []):
        u = requests.put(f"{base}/deploys/{dep_id}/files/index.html",
                         headers={**hdr, "Content-Type": "application/octet-stream"},
                         data=payload, timeout=180)
        u.raise_for_status()

    return {
        "site_id": site_id,
        "url": dep.get("ssl_url") or dep.get("url"),               # site's live URL
        "deploy_url": dep.get("deploy_ssl_url") or dep.get("deploy_url"),  # this build's permalink
    }


def _read_secret(path: Path) -> str | None:
    """Read a one-line secret file; return None if missing or empty."""
    try:
        val = path.read_text(encoding="utf-8").strip()
        return val or None
    except OSError:
        return None


def resolve_secrets(script_dir: Path):
    """Token + site id: environment variables win, else the two files one level
    above the repo. `script_dir` is the folder holding this script (…/project),
    so the repo root is its parent and the secrets sit in the repo's parent."""
    secrets_dir = script_dir.parent.parent          # project/ -> repo -> one above repo
    token = os.environ.get("NETLIFY_AUTH_TOKEN") or _read_secret(secrets_dir / "netlify.txt")
    site_id = os.environ.get("NETLIFY_SITE_ID") or _read_secret(secrets_dir / "site_id.txt")
    return token, site_id, secrets_dir


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scouting Recommendations</title>
<style>
:root{
  --bg:#0f1216; --card:#171b21; --card2:#1d2229; --ink:#f2f4f7; --ink2:#aeb4bd; --muted:#727884;
  --line:#272d36; --grid:#232a33;
  --a1:#3b82f6; --a2:#f97316; --a3:#10b981; --a4:#e11d48; --a5:#a855f7;
  --gold:#f5c451; --silver:#c8ccd2; --bronze:#cd8a53;
  --good:#22c55e; --warn:#eab308;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:26px 22px 90px}
a{color:var(--a1)}
h1{font-size:24px;margin:0 0 3px;letter-spacing:-.02em}
h2{font-size:18px;margin:0 0 3px;letter-spacing:-.01em}
h3{font-size:12px;margin:0;color:var(--ink2);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.sub{color:var(--ink2);margin:0;font-size:13px}
header.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.tools{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;cursor:pointer;border-radius:8px;border:1px solid var(--line);background:var(--card2);color:var(--ink2);padding:8px 13px}
button:hover{color:var(--ink);border-color:var(--muted)}
button.primary{background:var(--a1);border-color:var(--a1);color:#fff}
button.primary:hover{filter:brightness(1.08)}
.bar{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end;background:var(--card);border:1px solid var(--line);
  border-radius:13px;padding:15px 18px;margin-top:18px}
.ctrl{display:flex;flex-direction:column;gap:5px}
.ctrl label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.ctrl .rowin{display:flex;align-items:center;gap:7px}
input[type=number],select{background:var(--card2);border:1px solid var(--line);color:var(--ink);border-radius:7px;padding:7px 9px;font:inherit;width:78px}
select{width:auto}
input[type=range]{accent-color:var(--a1)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile .v{font-size:26px;font-weight:650;letter-spacing:-.02em}
.tile .k{font-size:12px;color:var(--muted);margin-top:2px}
section{margin-top:30px}
.sec-h{display:flex;align-items:baseline;gap:12px;margin-bottom:12px;border-bottom:1px solid var(--line);padding-bottom:8px}
.sec-h .n{color:var(--muted);font-variant-numeric:tabular-nums}
/* podium */
.podium{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:14px;align-items:end;margin-top:6px}
.pod{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:14px;
  padding:18px 16px;text-align:center;position:relative;overflow:hidden}
.pod.first{transform:translateY(-10px)}
.pod .rk{position:absolute;top:10px;left:12px;font-size:12px;font-weight:700;color:var(--muted)}
.pod .medal{width:52px;height:52px;border-radius:50%;margin:4px auto 10px;display:flex;align-items:center;justify-content:center;
  font-size:19px;font-weight:700;color:#0f1216}
.pod.first .medal{background:var(--gold)} .pod.second .medal{background:var(--silver)} .pod.third .medal{background:var(--bronze)}
.pod .nm{font-size:16px;font-weight:650;letter-spacing:-.01em}
.pod .tm{color:var(--ink2);font-size:12.5px;margin-top:1px}
.pod .st{display:flex;justify-content:center;gap:16px;margin-top:12px}
.pod .st div{font-size:11px;color:var(--muted)} .pod .st b{display:block;font-size:15px;color:var(--ink);font-weight:650}
.pod .bar-under{height:6px;border-radius:4px;margin-top:14px}
.pod.first .bar-under{background:var(--gold)} .pod.second .bar-under{background:var(--silver)} .pod.third .bar-under{background:var(--bronze)}
/* table */
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
th{text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:0 10px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--grid);vertical-align:middle}
tbody tr:hover{background:#ffffff08}
td.num{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2)}
.nm{font-weight:600}.meta{color:var(--muted);font-size:11.5px}
.avatar{width:26px;height:26px;border-radius:50%;background:var(--card2);border:1px solid var(--line);display:inline-flex;
  align-items:center;justify-content:center;font-size:10.5px;font-weight:700;color:var(--ink2);margin-right:8px;vertical-align:middle}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.cards,.podium{grid-template-columns:1fr}}
.chart{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:16px 18px}
.chart h3{margin-bottom:2px}.chart .cap{font-size:12px;color:var(--muted);margin:2px 0 10px}
svg{display:block;width:100%;height:auto;overflow:visible}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 8px;font-size:12px;color:var(--ink2)}
.legend .it{display:flex;align-items:center;gap:6px}.sw{width:11px;height:11px;border-radius:3px}
/* compare */
.searchbox{position:relative;max-width:340px}
.searchbox input{width:100%;background:var(--card2);border:1px solid var(--line);color:var(--ink);border-radius:9px;padding:9px 12px;font:inherit}
.sugg{position:absolute;z-index:20;left:0;right:0;top:calc(100% + 4px);background:var(--card2);border:1px solid var(--line);
  border-radius:9px;max-height:240px;overflow:auto;box-shadow:0 10px 30px #0008}
.sugg div{padding:8px 12px;cursor:pointer;display:flex;justify-content:space-between;gap:10px}
.sugg div:hover{background:#ffffff10}.sugg .m{color:var(--muted);font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.chip{display:flex;align-items:center;gap:8px;background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:5px 6px 5px 11px;font-size:13px}
.chip b{font-weight:600}.chip .dot{width:9px;height:9px;border-radius:50%}
.chip button{border:none;background:transparent;color:var(--muted);padding:0 4px;font-size:15px;line-height:1}
.chip button:hover{color:var(--ink)}
.tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--card2);border:1px solid var(--line);
  border-radius:8px;padding:7px 10px;font-size:12px;z-index:99;box-shadow:0 8px 24px #000a;max-width:240px}
.tip b{display:block;margin-bottom:2px}.tip span{color:var(--ink2)}
.foot{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
.ax{font-size:10.5px;fill:var(--muted)}.axt{font-size:11px;fill:var(--ink2)}
@media print{
  :root{--bg:#fff;--card:#fff;--card2:#fff;--ink:#111;--ink2:#333;--muted:#666;--line:#ccc;--grid:#e5e5e5}
  body{background:#fff}.tools,.bar,.searchbox,.chip button,#themeBtn{display:none!important}
  .wrap{max-width:none;padding:0}
  section{break-inside:avoid}section.pagebreak{break-before:page}
  .chart,.pod,.tile{break-inside:avoid}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div>
    <h1>Scouting Recommendations</h1>
    <p class="sub" id="subtitle"></p>
  </div>
  <div class="tools">
    <button id="printCur" class="primary">Save PDF · current</button>
    <button id="printDef">Reset &amp; save PDF · original</button>
    <button id="themeBtn">Light</button>
  </div>
</header>

<div class="bar" id="filters">
  <div class="ctrl"><label>Recommendations (N)</label>
    <div class="rowin"><input type="range" id="nRange" min="3" max="30" value="15" style="width:150px">
      <span id="nVal" style="min-width:22px;font-weight:600">15</span></div></div>
  <div class="ctrl"><label>Age</label>
    <div class="rowin"><input type="number" id="ageMin" placeholder="min"> – <input type="number" id="ageMax" placeholder="max"></div></div>
  <div class="ctrl"><label>Market value (€M)</label>
    <div class="rowin"><input type="number" id="mvMin" placeholder="min" step="0.5"> – <input type="number" id="mvMax" placeholder="max" step="0.5"></div></div>
  <div class="ctrl"><label>Sort by</label>
    <select id="sortBy">
      <option value="mv">Market value</option>
      <option value="g90">Goals /90</option>
      <option value="xg90">xG /90</option>
      <option value="fin90">Finishing /90</option>
      <option value="minutes">Minutes</option>
      <option value="age">Age (youngest)</option>
    </select></div>
  <div class="ctrl"><label>&nbsp;</label><button id="resetBtn">Reset</button></div>
</div>

<div class="tiles" id="tiles"></div>

<section id="sec-podium">
  <div class="sec-h"><h2>Top recommendations</h2><span class="n" id="podN"></span></div>
  <div class="podium" id="podium"></div>
  <div style="margin-top:18px" id="listWrap"></div>
</section>

<section id="sec-context" class="pagebreak">
  <div class="sec-h"><h2>The recommendations in context</h2><span class="n">all recommended players vs the attacker population</span></div>
  <div class="cards">
    <div class="chart"><h3>Age vs market value</h3><p class="cap">Population in grey, your recommendations highlighted.</p><div id="c_scatter"></div></div>
    <div class="chart"><h3>Metric correlations</h3><p class="cap">How the recorded metrics move together across attackers.</p><div id="c_corr"></div></div>
    <div class="chart"><h3>Distribution across value tiers</h3><p class="cap">Median with inter-quartile box, attackers split into value quartiles.</p><div id="c_tiers"></div></div>
    <div class="chart"><h3>Physical vs technical</h3><p class="cap">Top-value quartile vs bottom-value quartile.</p><div id="c_phys"></div></div>
  </div>
  <div class="chart" style="margin-top:16px"><h3>Where they play</h3><p class="cap">Recommended players placed at their primary position.</p><div id="c_pitch" style="max-width:520px;margin:0 auto"></div></div>
</section>

<section id="sec-compare" class="pagebreak">
  <div class="sec-h"><h2>Head-to-head</h2><span class="n">pick up to 5 · defaults to the top 3</span></div>
  <div class="searchbox"><input id="search" placeholder="Search a recommended player…" autocomplete="off"><div class="sugg" id="sugg" style="display:none"></div></div>
  <div class="chips" id="chips"></div>
  <div class="cards" style="align-items:start">
    <div class="chart"><h3>Radar — percentile within attackers</h3><div class="legend" id="radarLegend"></div><div id="c_radar"></div></div>
    <div class="chart"><h3>Side by side</h3><div id="c_table"></div></div>
  </div>
</section>

<p class="foot" id="foot"></p>
</div>
<div class="tip" id="tip"></div>

<script>const DATA=/*__DATA__*/;</script>
<script>
const D=DATA, M=D.meta;
const $=id=>document.getElementById(id);
const NS='http://www.w3.org/2000/svg';
const el=(t,a={})=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
const S=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const initials=n=>n.split(/\s+/).map(w=>w[0]).slice(0,2).join('').toUpperCase();
const fmtMv=v=>v==null?'—':v>=1e6?(v/1e6).toFixed(1).replace(/\.0$/,'')+'M€':Math.round(v/1e3)+'k€';
const COLS=['--a1','--a2','--a3','--a4','--a5'].map(S);
const tip=$('tip');
const showTip=(e,h)=>{tip.innerHTML=h;tip.style.opacity=1;const r=tip.getBoundingClientRect();
  let x=e.clientX+14,y=e.clientY-10;if(x+r.width>innerWidth-8)x=e.clientX-r.width-14;
  if(y+r.height>innerHeight-8)y=innerHeight-r.height-8;tip.style.left=x+'px';tip.style.top=Math.max(8,y)+'px';};
const hideTip=()=>tip.style.opacity=0;

/* ---------- state ---------- */
const state={n:15,ageMin:null,ageMax:null,mvMin:null,mvMax:null,sort:'mv'};
let selected=[];   // ids for compare
const byId=Object.fromEntries(D.players.map(p=>[p.id,p]));

function recommended(){
  let a=D.players.filter(p=>{
    if(state.ageMin!=null&&(p.age==null||p.age<state.ageMin))return false;
    if(state.ageMax!=null&&(p.age==null||p.age>state.ageMax))return false;
    if(state.mvMin!=null&&(p.mv==null||p.mv<state.mvMin*1e6))return false;
    if(state.mvMax!=null&&(p.mv==null||p.mv>state.mvMax*1e6))return false;
    return true;
  });
  const key=state.sort;
  a.sort((x,y)=>{
    if(key==='age')return (x.age??999)-(y.age??999);
    const vx=key==='mv'?(x.mv??-1):x[key], vy=key==='mv'?(y.mv??-1):y[key];
    return vy-vx;
  });
  return a.slice(0,state.n);
}

/* ---------- tiles ---------- */
function tiles(recs){
  const priced=recs.filter(p=>p.mv!=null);
  const avgAge=recs.length?(recs.reduce((s,p)=>s+(p.age||0),0)/recs.length):0;
  const totMv=priced.reduce((s,p)=>s+p.mv,0);
  $('tiles').innerHTML=[
    [recs.length,'recommended attackers'],
    [M.nAttackers,'attackers in the pool'],
    [avgAge.toFixed(1),'average age'],
    [fmtMv(totMv),'combined market value'],
  ].map(([v,k])=>`<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');
}

/* ---------- podium + list ---------- */
function podium(recs){
  $('podN').textContent=`${recs.length} shown`;
  const top=recs.slice(0,3), order=[1,0,2], cls=['second','first','third'];
  $('podium').innerHTML=order.map((idx,i)=>{const p=top[idx];if(!p)return'<div></div>';
    return `<div class="pod ${cls[i]}"><div class="rk">#${idx+1}</div>
      <div class="medal">${initials(p.name)}</div>
      <div class="nm">${esc(p.name)}</div><div class="tm">${esc(p.team)} · ${esc(p.pos)}</div>
      <div class="st"><div><b>${fmtMv(p.mv)}</b>value</div><div><b>${p.age??'—'}</b>age</div><div><b>${p.g90}</b>G/90</div></div>
      <div class="bar-under"></div></div>`;}).join('');
  const rest=recs.slice(3);
  $('listWrap').innerHTML=rest.length?`<table><thead><tr>
    <th style="width:34px">#</th><th>Player</th><th>Team</th><th class="num">Age</th><th class="num">Min</th>
    <th class="num">G</th><th class="num">xG</th><th class="num">G/90</th><th class="num">Value</th></tr></thead><tbody>${
    rest.map((p,i)=>`<tr><td class="num" style="color:var(--muted)">${i+4}</td>
      <td><span class="avatar">${initials(p.name)}</span><span class="nm">${esc(p.name)}</span><div class="meta" style="margin-left:34px">${esc(p.pos)}</div></td>
      <td>${esc(p.team)}</td><td class="num">${p.age??'—'}</td><td class="num">${p.minutes.toLocaleString()}</td>
      <td class="num">${p.goals}</td><td class="num">${p.xg}</td><td class="num">${p.g90}</td>
      <td class="num">${fmtMv(p.mv)}</td></tr>`).join('')}</tbody></table>`
    :'<p class="sub">Only three (or fewer) recommendations at this setting.</p>';
}

/* ---------- generic axes ---------- */
function niceStep(lo,hi,n){const raw=(hi-lo)/n||1,mag=Math.pow(10,Math.floor(Math.log10(raw)));
  const st=[1,2,2.5,5,10].map(k=>k*mag).find(k=>k>=raw)||10*mag;
  return {lo:Math.floor(lo/st)*st,hi:Math.ceil(hi/st)*st,st};}

/* ---------- scatter age vs value ---------- */
function scatter(recIds){
  const pts=D.scatter.filter(p=>p.age!=null&&p.mv!=null);
  const W=560,H=330,P={t:14,r:16,b:44,l:58};
  const xs=pts.map(p=>p.age),ys=pts.map(p=>p.mv/1e6);
  const xN=niceStep(Math.min(...xs)-1,Math.max(...xs)+1,5),yN=niceStep(0,Math.max(...ys),5);
  const X=v=>P.l+(v-xN.lo)/(xN.hi-xN.lo)*(W-P.l-P.r),Y=v=>H-P.b-(v-yN.lo)/(yN.hi-yN.lo)*(H-P.t-P.b);
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`});
  for(let v=yN.lo;v<=yN.hi+1e-6;v+=yN.st){svg.appendChild(el('line',{x1:P.l,x2:W-P.r,y1:Y(v),y2:Y(v),stroke:S('--grid')}));
    const t=el('text',{x:P.l-8,y:Y(v)+4,class:'ax','text-anchor':'end'});t.textContent=v+'M';svg.appendChild(t);}
  for(let v=xN.lo;v<=xN.hi+1e-6;v+=xN.st){const t=el('text',{x:X(v),y:H-P.b+17,class:'ax','text-anchor':'middle'});t.textContent=v;svg.appendChild(t);}
  svg.appendChild(el('line',{x1:P.l,x2:W-P.r,y1:H-P.b,y2:H-P.b,stroke:S('--muted')}));
  const xl=el('text',{x:(P.l+W-P.r)/2,y:H-6,class:'axt','text-anchor':'middle'});xl.textContent='Age';svg.appendChild(xl);
  const yl=el('text',{transform:`translate(14,${(P.t+H-P.b)/2}) rotate(-90)`,class:'axt','text-anchor':'middle'});yl.textContent='Market value (€M)';svg.appendChild(yl);
  const rec=new Set(recIds);
  pts.forEach(p=>{const on=rec.has(p.id);
    const c=el('circle',{cx:X(p.age),cy:Y(p.mv/1e6),r:on?6:3.5,fill:on?S('--a1'):'none',stroke:on?S('--card'):S('--muted'),'stroke-width':on?2:1.3,style:'cursor:pointer'});
    c.addEventListener('mousemove',e=>showTip(e,`<b>${esc(p.name)}</b><span>${p.age} yrs · ${fmtMv(p.mv)}</span>`));
    c.addEventListener('mouseleave',hideTip);svg.appendChild(c);});
  $('c_scatter').replaceChildren(svg);
}

/* ---------- correlation heatmap ---------- */
function corr(){
  const L=M.corrLabels,mat=D.corr,N=L.length,cell=30,pad=96,W=pad+N*cell+10,H=pad+N*cell+10;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`});
  const col=v=>{const a=Math.abs(v);const c=v>=0?S('--a1'):S('--a4');return `color-mix(in srgb, ${c} ${Math.round(a*100)}%, var(--card))`;};
  for(let i=0;i<N;i++)for(let j=0;j<N;j++){const v=mat[i][j];
    const r=el('rect',{x:pad+j*cell,y:pad+i*cell,width:cell-2,height:cell-2,rx:3,fill:col(v),style:'cursor:pointer'});
    r.addEventListener('mousemove',e=>showTip(e,`<b>${esc(L[i])} × ${esc(L[j])}</b><span>r = ${v}</span>`));
    r.addEventListener('mouseleave',hideTip);svg.appendChild(r);
    if(Math.abs(v)>=.55){const t=el('text',{x:pad+j*cell+(cell-2)/2,y:pad+i*cell+(cell-2)/2+3,'text-anchor':'middle','font-size':9,fill:Math.abs(v)>.8?'#fff':S('--ink2')});t.textContent=v.toFixed(1);svg.appendChild(t);}}
  L.forEach((l,i)=>{const t=el('text',{x:pad-6,y:pad+i*cell+cell/2+2,class:'ax','text-anchor':'end'});t.textContent=l;svg.appendChild(t);
    const t2=el('text',{transform:`translate(${pad+i*cell+cell/2},${pad-6}) rotate(-45)`,class:'ax','text-anchor':'start'});t2.textContent=l;svg.appendChild(t2);});
  $('c_corr').replaceChildren(svg);
}

/* ---------- value-tier boxes ---------- */
function tiers(){
  const metrics=M.tierMetrics,names=M.tierNames,tcol=[S('--a4'),S('--a2'),S('--a1'),S('--a3')];
  if(!Object.keys(D.tiers).length){$('c_tiers').innerHTML='<p class="sub">Not enough priced players for tiers.</p>';return;}
  const W=560,rowH=90,H=metrics.length*rowH+30,P={l:96,r:16};
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`});
  metrics.forEach((mm,mi)=>{const y0=mi*rowH+16,data=D.tiers[mm.key];
    let lo=Infinity,hi=-Infinity;names.forEach(n=>{const b=data[n];if(b){lo=Math.min(lo,b.min);hi=Math.max(hi,b.max);}});
    if(!isFinite(lo)){lo=0;hi=1;} if(hi===lo)hi=lo+1;
    const X=v=>P.l+(v-lo)/(hi-lo)*(W-P.l-P.r);
    const t=el('text',{x:P.l-8,y:y0+rowH/2-6,class:'axt','text-anchor':'end'});t.textContent=mm.label;svg.appendChild(t);
    names.forEach((nn,ni)=>{const b=data[nn];if(!b)return;const yy=y0+8+ni*17;
      svg.appendChild(el('line',{x1:X(b.min),x2:X(b.max),y1:yy,y2:yy,stroke:S('--grid')}));
      const r=el('rect',{x:X(b.q1),y:yy-5,width:Math.max(1,X(b.q3)-X(b.q1)),height:10,rx:2,fill:tcol[ni],'fill-opacity':.55,stroke:tcol[ni],style:'cursor:pointer'});
      r.addEventListener('mousemove',e=>showTip(e,`<b>${nn} · ${esc(mm.label)}</b><span>median ${b.med.toFixed(2)} · IQR ${b.q1.toFixed(2)}–${b.q3.toFixed(2)}</span>`));
      r.addEventListener('mouseleave',hideTip);svg.appendChild(r);
      svg.appendChild(el('line',{x1:X(b.med),x2:X(b.med),y1:yy-6,y2:yy+6,stroke:tcol[ni],'stroke-width':2}));});
  });
  $('c_tiers').replaceChildren(svg);
  $('c_tiers').insertAdjacentHTML('beforeend',
    '<div class="legend" style="margin-top:10px">'+names.map((n,i)=>`<div class="it"><span class="sw" style="background:${tcol[i]}"></span>${n}</div>`).join('')+'</div>');
}

/* ---------- physical vs technical ---------- */
function phystech(){
  const keys=Object.keys(D.phystech);
  if(!keys.length){$('c_phys').innerHTML='<p class="sub">Not enough priced players.</p>';return;}
  const W=560,bw=Math.min(70,(W-80)/keys.length),gap=(W-80-bw*keys.length)/(keys.length+1),H=280,P={t:14,b:60,l:44};
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`});
  // normalise each metric to 0..1 by its own max for a comparable panel
  keys.forEach((k,ki)=>{const d=D.phystech[k];const mx=Math.max(d.high.max,d.low.max,1e-9);
    const x=40+gap+ki*(bw+gap);
    [['high',S('--a2'),0],['low',S('--a1'),1]].forEach(([g,c,side])=>{const b=d[g],hw=bw/2;
      const Y=v=>H-P.b-(v/mx)*(H-P.t-P.b);const xx=x+side*hw;
      svg.appendChild(el('line',{x1:xx+hw/2,x2:xx+hw/2,y1:Y(b.min),y2:Y(b.max),stroke:S('--grid')}));
      const r=el('rect',{x:xx+4,y:Y(b.q3),width:hw-8,height:Math.max(1,Y(b.q1)-Y(b.q3)),rx:2,fill:c,'fill-opacity':.5,stroke:c,style:'cursor:pointer'});
      r.addEventListener('mousemove',e=>showTip(e,`<b>${esc(d.label)} · ${g==='high'?'high value':'low value'}</b><span>median ${b.med.toFixed(1)}</span>`));
      r.addEventListener('mouseleave',hideTip);svg.appendChild(r);
      svg.appendChild(el('line',{x1:xx+4,x2:xx+hw-4,y1:Y(b.med),y2:Y(b.med),stroke:c,'stroke-width':2}));});
    const t=el('text',{x:x+bw/2,y:H-P.b+16,class:'ax','text-anchor':'middle'});t.textContent=d.label;svg.appendChild(t);
    const kd=el('text',{x:x+bw/2,y:H-P.b+30,class:'ax','text-anchor':'middle','font-style':'italic',fill:S('--muted')});kd.textContent=d.kind==='phys'?'physical':'technical';svg.appendChild(kd);
  });
  $('c_phys').replaceChildren(svg);
  $('c_phys').insertAdjacentHTML('beforeend',
    `<div class="legend" style="margin-top:8px"><div class="it"><span class="sw" style="background:${S('--a2')}"></span>Top-value quartile</div><div class="it"><span class="sw" style="background:${S('--a1')}"></span>Bottom-value quartile</div></div>`);
}

/* ---------- pitch positions ---------- */
function pitch(recs){
  const W=440,H=560,PX=x=>x/100*W,PY=y=>H-y/100*H;
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`});
  const g=S('--line');
  svg.appendChild(el('rect',{x:1,y:1,width:W-2,height:H-2,fill:'none',stroke:g,'stroke-width':1.5}));
  svg.appendChild(el('line',{x1:0,x2:W,y1:H/2,y2:H/2,stroke:g}));
  svg.appendChild(el('circle',{cx:W/2,cy:H/2,r:52,fill:'none',stroke:g}));
  svg.appendChild(el('rect',{x:W*.22,y:0,width:W*.56,height:H*.16,fill:'none',stroke:g}));
  // cluster players by primary code
  const buckets={};recs.forEach(p=>{(buckets[p.pos]=buckets[p.pos]||[]).push(p);});
  Object.entries(buckets).forEach(([code,list])=>{const xy=M.pitch[code]||[50,60];
    list.forEach((p,i)=>{const off=(i-(list.length-1)/2)*22;const cx=PX(xy[0])+off,cy=PY(xy[1]);
      const c=el('circle',{cx,cy,r:12,fill:S('--a1'),stroke:S('--card'),'stroke-width':2,style:'cursor:pointer'});
      c.addEventListener('mousemove',e=>showTip(e,`<b>${esc(p.name)}</b><span>${esc(code)} · ${esc(p.team)}</span>`));
      c.addEventListener('mouseleave',hideTip);svg.appendChild(c);
      const t=el('text',{x:cx,y:cy+3.5,'text-anchor':'middle','font-size':9,'font-weight':700,fill:'#fff'});t.textContent=initials(p.name);svg.appendChild(t);});
  });
  $('c_pitch').replaceChildren(svg);
}

/* ---------- radar ---------- */
function radar(players){
  const labels=M.radar.map(m=>m.label),N=labels.length,W=470,H=420,cx=W/2,cy=H/2+4,R=132;
  const ang=i=>-Math.PI/2+i*2*Math.PI/N,pt=(i,v)=>[cx+Math.cos(ang(i))*R*v/100,cy+Math.sin(ang(i))*R*v/100];
  const svg=el('svg',{viewBox:`0 0 ${W} ${H}`});
  [25,50,75,100].forEach(r=>svg.appendChild(el('polygon',{points:labels.map((_,i)=>pt(i,r).join(',')).join(' '),fill:'none',stroke:S('--grid')})));
  labels.forEach((l,i)=>{const[ex,ey]=pt(i,100);svg.appendChild(el('line',{x1:cx,y1:cy,x2:ex,y2:ey,stroke:S('--grid')}));
    const[lx,ly]=pt(i,118),a=ang(i);const t=el('text',{x:lx,y:ly+4,class:'ax','text-anchor':Math.abs(Math.cos(a))<.3?'middle':(Math.cos(a)>0?'start':'end')});t.textContent=l;svg.appendChild(t);});
  players.forEach((p,pi)=>{const c=COLS[pi%COLS.length],vals=p.radar;
    svg.appendChild(el('polygon',{points:vals.map((v,i)=>pt(i,v).join(',')).join(' '),fill:c,'fill-opacity':.12,stroke:c,'stroke-width':2,'stroke-linejoin':'round'}));
    vals.forEach((v,i)=>{const[px,py]=pt(i,v);const dot=el('circle',{cx:px,cy:py,r:4,fill:c,stroke:S('--card'),'stroke-width':1.5,style:'cursor:pointer'});
      dot.addEventListener('mousemove',e=>showTip(e,`<b>${esc(p.name)}</b><span>${esc(labels[i])}: ${Math.round(v)}th pct</span>`));
      dot.addEventListener('mouseleave',hideTip);svg.appendChild(dot);});});
  $('c_radar').replaceChildren(svg);
  $('radarLegend').innerHTML=players.map((p,i)=>`<div class="it"><span class="sw" style="background:${COLS[i%COLS.length]}"></span>${esc(p.name)}</div>`).join('');
  // side-by-side table
  const rows=[['Age','age'],['Minutes','minutes'],['Goals','goals'],['xG','xg'],['G/90','g90'],['xG/90','xg90'],['Finishing/90','fin90'],['Value',null]];
  $('c_table').innerHTML=`<table><thead><tr><th>Metric</th>${players.map((p,i)=>`<th class="num" style="color:${COLS[i%COLS.length]}">${esc(p.name.split(' ').slice(-1)[0])}</th>`).join('')}</tr></thead><tbody>${
    rows.map(([lab,k])=>`<tr><td>${lab}</td>${players.map(p=>`<td class="num">${k===null?fmtMv(p.mv):(p[k]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

/* ---------- compare: search + chips ---------- */
function renderChips(){
  $('chips').innerHTML=selected.map((id,i)=>{const p=byId[id];
    return `<div class="chip"><span class="dot" style="background:${COLS[i%COLS.length]}"></span><b>${esc(p.name)}</b><span class="meta">${esc(p.team)}</span><button data-id="${id}">×</button></div>`;}).join('');
  $('chips').querySelectorAll('button').forEach(b=>b.onclick=()=>{selected=selected.filter(x=>x!==b.dataset.id);syncCompare();});
}
function syncCompare(){renderChips();radar(selected.map(id=>byId[id]));}
function suggestions(q){
  const recs=recommended();const ql=q.trim().toLowerCase();
  const pool=recs.filter(p=>!selected.includes(p.id)&&(ql===''||p.name.toLowerCase().includes(ql)||p.team.toLowerCase().includes(ql)));
  const box=$('sugg');
  if(!pool.length||selected.length>=5){box.style.display='none';return;}
  box.innerHTML=pool.slice(0,8).map(p=>`<div data-id="${p.id}"><span>${esc(p.name)}</span><span class="m">${esc(p.team)} · ${esc(p.pos)}</span></div>`).join('');
  box.style.display='block';
  box.querySelectorAll('div[data-id]').forEach(d=>d.onclick=()=>{if(selected.length<5){selected.push(d.dataset.id);$('search').value='';box.style.display='none';syncCompare();}});
}
$('search').addEventListener('input',e=>suggestions(e.target.value));
$('search').addEventListener('focus',e=>suggestions(e.target.value));
document.addEventListener('click',e=>{if(!e.target.closest('.searchbox'))$('sugg').style.display='none';});

/* ---------- master render ---------- */
function render(){
  const recs=recommended();
  $('subtitle').textContent=`${M.nAttackers} attackers · top ${recs.length} by ${$('sortBy').selectedOptions[0].text.toLowerCase()} shown as recommendations`;
  tiles(recs);podium(recs);scatter(recs.map(p=>p.id));pitch(recs);
  // keep only still-valid selections; default to top 3 when empty
  const ids=new Set(recs.map(p=>p.id));
  selected=selected.filter(id=>ids.has(id));
  if(!selected.length)selected=recs.slice(0,3).map(p=>p.id);
  syncCompare();
}
function renderStatic(){corr();tiers();phystech();}

/* ---------- controls ---------- */
$('nRange').oninput=e=>{state.n=+e.target.value;$('nVal').textContent=state.n;render();};
$('sortBy').onchange=e=>{state.sort=e.target.value;render();};
['ageMin','ageMax','mvMin','mvMax'].forEach(id=>$(id).oninput=e=>{state[id]=e.target.value===''?null:+e.target.value;render();});
$('resetBtn').onclick=()=>{Object.assign(state,{n:15,ageMin:null,ageMax:null,mvMin:null,mvMax:null,sort:'mv'});
  $('nRange').value=15;$('nVal').textContent=15;$('sortBy').value='mv';['ageMin','ageMax','mvMin','mvMax'].forEach(id=>$(id).value='');selected=[];render();};
$('printCur').onclick=()=>window.print();
$('printDef').onclick=()=>{$('resetBtn').onclick();setTimeout(()=>window.print(),120);};

/* ---------- theme ---------- */
let light=false;
$('themeBtn').onclick=()=>{light=!light;document.documentElement.style.colorScheme=light?'light':'dark';
  document.documentElement.setAttribute('data-light',light);
  // simple light overrides
  const r=document.documentElement.style;
  if(light){r.setProperty('--bg','#f4f5f7');r.setProperty('--card','#fff');r.setProperty('--card2','#f0f1f4');
    r.setProperty('--ink','#14181f');r.setProperty('--ink2','#3c434e');r.setProperty('--muted','#6b7280');
    r.setProperty('--line','#e2e5ea');r.setProperty('--grid','#eceef2');$('themeBtn').textContent='Dark';}
  else{['--bg','--card','--card2','--ink','--ink2','--muted','--line','--grid'].forEach(v=>r.removeProperty(v));$('themeBtn').textContent='Light';}
  render();renderStatic();};

$('foot').textContent=`Demo dashboard · ${M.nAttackers} attackers from the sample · "AI recommendations" = top-N by the chosen sort. Not a valuation.`+(M.built?`  ·  Generated & deployed: ${M.built}`:'');
render();renderStatic();
</script>
</body>
</html>
"""


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(here.parent / "data" / "sample.xlsx"))
    ap.add_argument("--out", default=str(here / "dashboard.html"))
    ap.add_argument("--no-deploy", action="store_true", help="build only, do not deploy")
    args = ap.parse_args()

    df = load(Path(args.data))
    att = build_frame(df)
    data = compute(att)
    data["meta"]["built"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    html = render_html(data)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out}  ({len(att)} attackers, {len(html)//1024} KB)")

    if args.no_deploy:
        return

    token, site_id, secrets_dir = resolve_secrets(here)
    if not token or not site_id:
        missing = ", ".join(f for f, v in [("netlify.txt", token), ("site_id.txt", site_id)] if not v)
        print(f"! deploy skipped — missing {missing} in {secrets_dir}")
        return
    try:
        info = deploy_netlify(Path(args.out), token, site_id)
        print(f"deployed  → {info['url']}")
        print(f"this build → {info['deploy_url']}")
    except Exception as e:  # noqa: BLE001
        print(f"! deploy failed: {e}")


if __name__ == "__main__":
    main()

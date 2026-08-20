#!/usr/bin/env python3
"""make_league_map.py - build data/league_map.csv, the reference table that tells
the pipeline what each league folder actually is.

The league CSVs know nothing about their own competition beyond the folder name,
and the leagues are not equivalent: the 2. Bundesliga and the Andorran first
division both sit in data/2025-2026 with players putting up similar numbers at
completely different levels. This table is what lets the scoring separate them.

Columns written:
    league_code     short stable key (GER_2BUN) to use in code
    folder          the folder name under data/<season>
    country         FIFA 3-letter code
    league          competition name
    priority        Dribblify scouting priority: green/yellow/red - WHERE to look
    priority_rank   the same thing as a sortable number: 1 green, 2 yellow, 3 red
                    (the words sort alphabetically to green, red, yellow, which
                    silently puts the least important band in the middle)
    division        national tier (1 = top flight) - a fact about the competition
    strength        provisional 0-100 league level coefficient - HOW to compare
    strength_source how that number was obtained (never leave this unread)
    latest_season   most recent season present under data/
    n_teams         distinct clubs found in the CSV (derived)
    n_players       rows in the CSV (derived)
    groups          regional groups, where known - blank means unverified
    calendar        autumn-spring | calendar | apertura-clausura
    notes           anything the pipeline should not silently assume

PRIORITY IS NOT STRENGTH. The Dribblify map is a scouting-priority map: the
Campeonato de Portugal (4th tier) is green while the Serie B is yellow. Using
priority as a level coefficient would rank the Campeonato de Portugal above the
Serie B. That is why strength is its own column.

Run:
    python make_league_map.py            # writes data/league_map.csv
    python make_league_map.py --dry-run
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
OUT = DATA / "league_map.csv"

# --- Dribblify scouting priority, from make_league_folders.py -----------------
PRIORITY_RANK = {"green": 1, "yellow": 2, "red": 3}   # 1 is the most important

PRIORITY = {}
for _tier, _countries in [
    ("green", ["GER", "ARG", "BRA", "COL", "DEN", "ESP", "FRA", "GRE", "NED", "NOR", "POR", "SWE"]),
    ("yellow", ["AUT", "BEL", "BUL", "CZE", "CHI", "CHN", "CYP", "KOR", "CRO", "SCO", "SVK",
                "SVN", "USA", "HUN", "ISR", "ITA", "JPN", "MEX", "POL", "ROU", "SRB", "SUI",
                "TUR", "UKR", "URU"]),
    ("red", ["RSA", "ALB", "AND", "ALG", "ARM", "AZE", "BOL", "BIH", "CRC", "ECU", "EST",
             "FIN", "GEO", "LVA", "LTU", "MAR", "MNE", "PAR", "PER", "QAT", "TUN", "UZB", "VEN"]),
]:
    for _c in _countries:
        PRIORITY[_c] = _tier

# --- national tier per league folder number ----------------------------------
# A fact about the competition, not a judgement about its quality.
DIVISION = {
    1: 2, 2: 3, 3: 2, 4: 2, 5: 3, 6: 1, 7: 2, 8: 1, 9: 2, 10: 3, 11: 4, 12: 2,
    13: 3, 14: 3, 15: 1, 16: 2, 17: 1, 18: 2, 19: 1, 20: 2, 21: 3, 22: 4, 23: 1,
    24: 2, 25: 1, 26: 2, 27: 1, 28: 2, 29: 1, 30: 1, 31: 1, 32: 1, 33: 1, 34: 1,
    35: 1, 36: 2, 37: 1, 38: 2, 39: 1, 40: 1, 41: 1, 42: 2, 43: 1, 44: 1, 45: 2,
    46: 3, 47: 1, 48: 2, 49: 1, 50: 2, 51: 1, 52: 2, 53: 1, 54: 1, 55: 1, 56: 1,
    57: 2, 58: 1, 59: 1, 60: 1, 61: 1, 62: 1, 63: 1, 64: 1, 65: 1, 66: 1, 67: 1,
    68: 1, 69: 1, 70: 1, 71: 1, 72: 1, 73: 1, 74: 1, 75: 1, 76: 1, 77: 1, 78: 1,
    79: 1, 80: 1, 81: 1, 82: 1,
}

# --- UEFA men's association coefficients, 2026-27 seeding (2025 coefficients) --
# Source: en.wikipedia.org/wiki/2026-27_UEFA_Champions_League, association ranking.
# Countries absent here could not be read from a static source and fall back to
# the market-value route below - the strength_source column records which.
UEFA = {
    "ITA": 94.453, "GER": 86.331, "FRA": 73.093, "POR": 67.150, "NED": 62.266,
    "BEL": 56.850, "AUT": 44.100, "TUR": 43.900, "CZE": 39.687, "UKR": 39.312,
    "GRE": 36.450, "POL": 35.550, "SUI": 35.000, "SRB": 33.981, "DEN": 33.625,
    "SCO": 31.625, "ROU": 27.537, "ISR": 21.250, "BUL": 20.343, "HUN": 19.875,
    "SVK": 19.625, "NOR": 14.968, "CRO": 14.500, "SVN": 13.520, "CYP": 13.031,
    "FIN": 12.250, "SWE": 12.041, "BIH": 11.750, "ALB": 7.957, "GEO": 7.875,
    "MNE": 6.875, "ARM": 6.166,
}

# how much of a country's top-flight level a lower tier retains
DIVISION_FACTOR = {1: 1.00, 2: 0.55, 3: 0.32, 4: 0.18}

CALENDAR_YEAR = {"NOR", "SWE", "FIN", "EST", "LVA", "LTU", "USA", "JPN", "KOR", "CHN",
                 "BRA", "CHI", "COL", "URU", "PER", "ECU", "VEN", "BOL", "PAR", "CRC", "ARG"}
APERTURA = {"MEX"}

# regional groups, only where verified; blank means "check before assuming 1"
GROUPS = {10: 2, 11: 5, 46: 3}

FOLDER_RE = re.compile(r"^(\d+)\s*-\s*\[([A-Za-z]{2,3})\]\s*-\s*\((.+)\)$")


def code_for(country, league):
    """GER_2BUN style key: country plus a squashed league abbreviation.

    Digits are always kept, so J1 League and J2 League do not collapse onto the
    same key. Collisions are still reported by main() rather than silently kept.
    """
    words = re.findall(r"[A-Za-z0-9]+", league.upper())
    parts = []
    for w in words:
        digits = "".join(ch for ch in w if ch.isdigit())
        parts.append(w[:5] if len(words) == 1 else (w[0] + digits))
    return f"{country}_{''.join(parts)[:7]}"


def scan():
    rows = {}
    for season_dir in sorted(d for d in DATA.iterdir() if d.is_dir()):
        season = season_dir.name
        if not re.match(r"^\d{4}-\d{4}$", season):
            continue
        for folder in sorted(d for d in season_dir.iterdir() if d.is_dir()):
            m = FOLDER_RE.match(folder.name)
            if not m:
                continue
            num, country, league = int(m.group(1)), m.group(2).upper(), m.group(3)
            csvs = [p for p in folder.glob("*.csv")]
            n_teams = n_players = 0
            if csvs:
                df = pd.read_csv(csvs[0], low_memory=False)
                n_players = len(df)
                col = "Team within selected timeframe"
                n_teams = int(df[col].nunique()) if col in df.columns else 0
            prev = rows.get(num)
            if prev and prev["latest_season"] >= season and prev["n_players"]:
                continue
            rows[num] = {"num": num, "folder": folder.name, "country": country,
                         "league": league, "latest_season": season,
                         "n_teams": n_teams, "n_players": n_players}
    return [rows[k] for k in sorted(rows)]


def market_value_median(row):
    """Median market value of the priced players, used as a fallback level proxy."""
    folder = DATA / row["latest_season"] / row["folder"]
    csvs = list(folder.glob("*.csv"))
    if not csvs:
        return None, 0.0
    df = pd.read_csv(csvs[0], low_memory=False)
    mv = pd.to_numeric(df.get("Market value"), errors="coerce")
    priced = mv[mv > 0]
    if len(priced) < 20:
        return None, float(100 * (mv > 0).mean()) if len(mv) else 0.0
    return float(priced.median()), float(100 * (mv > 0).mean())


def main():
    dry = "--dry-run" in sys.argv
    rows = scan()
    if not rows:
        print(f"no league folders found under {DATA}")
        return 1

    for r in rows:
        r["priority"] = PRIORITY.get(r["country"], "")
        r["division"] = DIVISION.get(r["num"], "")
        r["mv_median"], r["mv_coverage"] = market_value_median(r)

    # --- provisional strength ------------------------------------------------
    # European route: UEFA association coefficient x how much of the top flight
    # the national tier retains.
    # Everything else: the league's median market value, mapped onto the European
    # scale by interpolating over the leagues that have BOTH. A plain linear
    # rescale does not work here - market values run much hotter than the UEFA
    # axis, which put Liga MX at 100 with the next league at 64. Interpolation
    # also clamps at the ends, so a league richer than every anchor lands at the
    # top anchor instead of running away with the scale.
    uefa_raw = {}
    for r in rows:
        c = UEFA.get(r["country"])
        if c is not None and r["division"] in DIVISION_FACTOR:
            uefa_raw[r["num"]] = c * DIVISION_FACTOR[r["division"]]

    anchors = sorted((r["mv_median"], uefa_raw[r["num"]]) for r in rows
                     if r["num"] in uefa_raw and r["mv_median"])
    anchor_mv = [a for a, _ in anchors]
    anchor_st = [b for _, b in anchors]
    # make the anchor curve monotone so interpolation cannot invert the order
    for i in range(1, len(anchor_st)):
        anchor_st[i] = max(anchor_st[i], anchor_st[i - 1])

    for r in rows:
        if r["num"] in uefa_raw:
            r["_raw"] = uefa_raw[r["num"]]
            r["strength_source"] = "provisional_uefa_x_division"
        elif r["mv_median"] and anchor_mv:
            r["_raw"] = float(np.interp(r["mv_median"], anchor_mv, anchor_st))
            r["strength_source"] = f"provisional_marketvalue_interp (coverage {r['mv_coverage']:.0f}%)"
        else:
            r["_raw"] = None
            r["strength_source"] = "MISSING - fill by hand"

    # rescale onto a shared 0-100 axis so the ordering stays readable
    top = max((r["_raw"] for r in rows if r["_raw"]), default=0) or 1
    for r in rows:
        r["strength"] = round(100 * r["_raw"] / top, 1) if r["_raw"] else ""

    out = pd.DataFrame([{
        "league_code": code_for(r["country"], r["league"]),
        "folder": r["folder"],
        "country": r["country"],
        "league": r["league"],
        "priority": r["priority"],
        "priority_rank": PRIORITY_RANK.get(r["priority"], ""),
        "division": r["division"],
        "strength": r["strength"],
        "strength_source": r["strength_source"],
        "latest_season": r["latest_season"],
        "n_teams": r["n_teams"],
        "n_players": r["n_players"],
        "groups": GROUPS.get(r["num"], ""),
        "calendar": ("apertura-clausura" if r["country"] in APERTURA
                     else "calendar" if r["country"] in CALENDAR_YEAR
                     else "autumn-spring"),
        "notes": "" if r["n_players"] else "no CSV exported yet",
    } for r in rows])

    dupes = out.league_code[out.league_code.duplicated()].tolist()
    if dupes:
        print(f"WARNING duplicated league_code: {dupes}")

    if dry:
        print(out.to_string(index=False))
    else:
        out.to_csv(OUT, index=False, encoding="utf-8-sig")
        print(f"wrote {OUT}  ({len(out)} leagues)")
    src = out.strength_source.str.split(" ").str[0].value_counts()
    print("\nstrength_source:")
    for k, v in src.items():
        print(f"  {k:34s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

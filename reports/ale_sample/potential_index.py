#!/usr/bin/env python3
"""potential_index.py - builds the data behind potential_dashboard.html.

Reads `dashboard_template.html`, fills in the `__DATA__` placeholder with a
JSON blob built from `devkit/scoring.py`'s `build()`, and writes the result
to `potential_dashboard.html`. The dashboard itself needs nothing installed
to view - open the .html file in a browser. This script is what needs
Python, pandas and scoring.py, and only at generation time.

This replaces an earlier, undocumented version of this file that produced
the dashboard currently checked in. That version used a different, simpler
scoring method (z-scores, age folded in at a fixed 25% weight, 5 hand-picked
leagues of similar level) and its script was lost - it was never committed.
This version regenerates the same dashboard shape from the current pipeline
in devkit/scoring.py instead: percentiled within league and role, market
value never enters the score, and all 75 available leagues are used instead
of 5 hand-picked ones, now that `perf_adjusted` makes them comparable.

Run:
    python potential_index.py
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "devkit"))
import scoring

TEMPLATE = Path(__file__).parent / "dashboard_template.html"
OUT = Path(__file__).parent / "potential_dashboard.html"

MAX_AGE = 25          # a demo choice - the filter itself has no age cap, see scoring.py
MIN_MINUTES = scoring.MIN_MINUTES

# Portuguese labels for every metric that appears in scoring.ROLE_METRICS.
# `higher=False` only for metrics where less is better (scoring.LOWER_IS_BETTER).
PT_LABEL = {
    "Save rate, %": "Taxa de defesa",
    "Prevented goals per 90": "Golos evitados /90",
    "Conceded goals per 90": "Golos sofridos /90",
    "Exits per 90": "Saídas /90",
    "Accurate passes, %": "Passe certo",
    "Accurate long passes, %": "Passe longo certo",
    "Defensive duels won, %": "Duelos def. ganhos",
    "Aerial duels won, %": "Duelos aéreos ganhos",
    "PAdj Interceptions": "Interceções (PAdj)",
    "Shots blocked per 90": "Remates bloqueados /90",
    "Successful defensive actions per 90": "Ações def. bem-sucedidas /90",
    "Accurate progressive passes, %": "Passe progressivo certo",
    "Crosses per 90": "Cruzamentos /90",
    "Accurate crosses, %": "Cruzamento certo",
    "Progressive runs per 90": "Conduções progressivas /90",
    "xA per 90": "xA /90",
    "Key passes per 90": "Passes-chave /90",
    "Accurate forward passes, %": "Passe p/ frente certo",
    "Progressive passes per 90": "Passes progressivos /90",
    "Received passes per 90": "Passes recebidos /90",
    "Passes to final third per 90": "Passes p/ último terço /90",
    "Duels won, %": "Duelos ganhos",
    "Through passes per 90": "Passes de rutura /90",
    "Shot assists per 90": "Assist. de remate /90",
    "Passes to penalty area per 90": "Passes p/ área /90",
    "Dribbles per 90": "Dribles /90",
    "Successful dribbles, %": "Dribles bem-sucedidos",
    "Touches in box per 90": "Toques na área /90",
    "xG per 90": "xG /90",
    "Accelerations per 90": "Acelerações /90",
    "Fouls suffered per 90": "Faltas sofridas /90",
    "Non-penalty goals per 90": "Golos s/ pénalti /90",
    "Goal conversion, %": "Conversão",
    "Shots on target, %": "Remates à baliza",
    "Head goals per 90": "Golos de cabeça /90",
}

PT_ROLE_LABEL = {
    "GK": "Guarda-redes", "CB": "Defesas centrais", "FB": "Laterais",
    "DM": "Médios defensivos", "CM": "Médios", "AM": "Médios ofensivos",
    "W": "Alas", "CF": "Avançados",
}


def build_groups():
    groups = {}
    for role, metrics in scoring.ROLE_METRICS.items():
        groups[role] = {
            "label": PT_ROLE_LABEL[role],
            "metrics": [
                {"col": col, "label": PT_LABEL.get(col, col),
                 "higher": col not in scoring.LOWER_IS_BETTER}
                for col in metrics
            ],
        }
    return groups


def two_proportion_pvalue(x1, n1, x2, n2):
    """Two-sided z-test for a difference in proportions. Returns None if either group is empty."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = np.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (p1 - p2) / se
    return float(2 * (1 - stats.norm.cdf(abs(z))))


def main():
    res = scoring.build()
    cand = res[(res["age"] <= MAX_AGE) & res["minutes"].notna()].copy()

    cand["idx"] = cand["perf_adjusted"].where(cand["perf_adjusted"].notna(), cand["perf_league"])
    cand["moved"] = (cand["club_season"] != cand["club_now"]) & cand["club_now"].notna()
    cand["free"] = cand["club_now"].isna() | (cand["club_now"].astype(str).str.strip() == "")

    players = []
    for _, r in cand.iterrows():
        role = r["role"]
        metrics = scoring.ROLE_METRICS.get(role, [])
        radar = [r.get(f"pct::{col}") for col in metrics]
        radar = [None if pd.isna(v) else float(v) for v in radar]
        players.append({
            "name": r["Player"],
            "team": r["club_season"],
            "clubNow": None if pd.isna(r["club_now"]) else r["club_now"],
            "moved": bool(r["moved"]),
            "free": bool(r["free"]),
            "league": r["league"],
            "country": r["country"],
            "group": role,
            "pos": r["Position"],
            "age": int(r["age"]),
            "min": int(r["minutes"]),
            "mv": None if pd.isna(r["market_value"]) else float(r["market_value"]),
            "idx": round(float(r["idx"]), 1),
        })

    # --- validation, computed for real against this same candidate pool ---
    idx = cand["idx"]
    top_cut = idx.quantile(0.90)
    top_mask = idx >= top_cut
    moved_top = int(cand.loc[top_mask, "moved"].sum())
    n_top = int(top_mask.sum())
    moved_rest = int(cand.loc[~top_mask, "moved"].sum())
    n_rest = int((~top_mask).sum())
    p_value = two_proportion_pvalue(moved_top, n_top, moved_rest, n_rest)

    priced = cand["priced"]
    mv_corr = idx[priced].corr(cand.loc[priced, "market_value"], method="spearman")

    validation = {
        "nTop": n_top,
        "movedTop": round(100 * moved_top / n_top, 1) if n_top else None,
        "movedRest": round(100 * moved_rest / n_rest, 1) if n_rest else None,
        "pValue": round(p_value, 3) if p_value is not None else None,
        "mvCorr": round(float(mv_corr), 2) if pd.notna(mv_corr) else None,
        "nWithMv": int(priced.sum()),
        "pctNoMv": round(100 * (~priced).mean(), 1),
    }

    groups = build_groups()
    for role in groups:
        groups[role]["n"] = int((cand["role"] == role).sum())

    leagues = (cand[["league", "country"]].drop_duplicates()
               .rename(columns={"league": "name", "country": "code"})
               .sort_values("name").to_dict("records"))

    data = {
        "meta": {
            "leagues": leagues,
            "maxAge": MAX_AGE,
            "minMinutes": MIN_MINUTES,
            "fullSeason": scoring.FULL_SEASON,
            "totalPool": len(cand),
            "totalScanned": int(len(res)),
        },
        "groups": groups,
        "players": players,
        "validation": validation,
    }

    html = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = re.sub(r"/\*__DATA__\*/.*?/\*__DATA__\*/", payload.replace("\\", "\\\\"), html, count=1)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}  ({len(cand)} players, {len(leagues)} leagues)")
    print(f"validation: {validation}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

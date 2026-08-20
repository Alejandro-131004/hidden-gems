#!/usr/bin/env python3
"""concept_analysis.py - reproduces the numbers quoted in demo_dashboard2's
"Answers to the open TODOs" section, so they stop being a claim nobody can
re-check.

This is a diagnostic on the OLD demo_dashboard1 approach (one flat list of 10
concepts, attacking group only, equal weights) - not on the current
ROLE_METRICS pipeline in scoring.py. It exists to answer one question: was the
naive index's weak correlation with Market value caused by the two obvious
bugs (age folded in, percentiling across all leagues at once), or by
something else? See devkit/scoring.py and docs/reference-header.txt for the
pipeline actually in use.

Run:
    python concept_analysis.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "devkit"))
import scoring

ROLE = {}
for _p in ["GK"]:                                   ROLE[_p] = "GK"
for _p in ["CB", "RCB", "LCB"]:                     ROLE[_p] = "CB"
for _p in ["RB", "LB", "RWB", "LWB"]:               ROLE[_p] = "FB"
for _p in ["DMF", "RDMF", "LDMF"]:                  ROLE[_p] = "DM"
for _p in ["CMF", "RCMF", "LCMF"]:                  ROLE[_p] = "CM"
for _p in ["AMF"]:                                  ROLE[_p] = "AM"
for _p in ["LW", "RW", "LWF", "RWF", "LAMF", "RAMF"]: ROLE[_p] = "W"
for _p in ["CF"]:                                   ROLE[_p] = "CF"

# the demo_dashboard1 concept list, verbatim - 14 indicators folded into 10
# equally-weighted concepts, "double average" style.
CONCEPTS = {
    "Goal output":     ["Goals per 90", "xG per 90"],
    "Shot threat":     ["Shots per 90"],
    "Chance creation": ["Assists per 90", "xA per 90"],
    "Through balls":   ["Through passes per 90"],
    "Dribbling":       ["Dribbles per 90", "Successful dribbles, %", "Progressive runs per 90"],
    "Key passing":     ["Key passes per 90"],
    "Passing":         ["Passes per 90", "Accurate passes, %"],
    "Fouls drawn":     ["Fouls suffered per 90"],
    "Minutes":         ["Minutes played"],
    "Age (younger)":   None,   # inverted, handled separately
}


def load_attackers(season=scoring.SEASON):
    players = scoring.load_players(season, strict=True)
    players["Pos"] = players["Position"].map(
        lambda p: "NA" if pd.isna(p) else str(p).split(",")[0].strip())
    players["Role"] = players["Pos"].map(lambda p: ROLE.get(p, "MID"))
    return players[players["Role"].isin(["AM", "W", "CF"])].copy()


def concept_scores(df, within_league=False):
    """Percentile every metric, then average within each concept - one column per concept."""
    metric_cols = [c for cols in CONCEPTS.values() if cols for c in cols]
    num = df[metric_cols].apply(pd.to_numeric, errors="coerce")
    group = df["league"] if within_league else None
    pct = num.groupby(group).rank(pct=True) if group is not None else num.rank(pct=True)
    out = {}
    for name, cols in CONCEPTS.items():
        if cols:
            out[name] = pct[cols].mean(axis=1)
        else:
            age = pd.to_numeric(df["Age"], errors="coerce")
            rank = age.groupby(group).rank(pct=True) if group is not None else age.rank(pct=True)
            out[name] = 1 - rank
    return pd.DataFrame(out)


def flat_index(scores):
    return scores.fillna(0).mean(axis=1) * 100


def retention(index_col, mv, frac=0.15):
    """Share of the top `frac` by market value that also lands in the top `frac` by index."""
    n = len(index_col)
    k = int(n * frac)
    top_idx = set(index_col.nlargest(k).index)
    top_mv = set(mv.nlargest(k).index)
    return len(top_idx & top_mv) / len(top_mv)


def main():
    att = load_attackers()
    mv = pd.to_numeric(att["Market value"], errors="coerce")

    scores = concept_scores(att)
    index = flat_index(scores)
    print(f"n attackers = {len(att)}")
    print(f"baseline      Spearman(index, market value) = {index.corr(mv, method='spearman'):.3f}")

    idx_noage = flat_index(scores.drop(columns=["Age (younger)"]))
    print(f"drop age      Spearman = {idx_noage.corr(mv, method='spearman'):.3f}")

    idx_within = flat_index(concept_scores(att, within_league=True))
    print(f"within-league Spearman = {idx_within.corr(mv, method='spearman'):.3f}")

    print("\nconcept x concept correlation:")
    print(scores.corr().round(2).to_string())

    print("\nconcept vs overall index correlation:")
    print(scores.corrwith(index).sort_values(ascending=False).round(2).to_string())

    X = scores.fillna(scores.mean())
    X = (X - X.mean()) / X.std()
    ev = PCA().fit(X).explained_variance_ratio_
    print(f"\nPCA: PC1 = {ev[0]*100:.1f}%   PC1-3 = {ev[:3].sum()*100:.1f}%")

    print(f"\nretention, 10 concepts (top 15%): {retention(index, mv):.1%}")
    blocks_7 = {
        "Goal threat": ["Goal output", "Shot threat"],
        "Creative":    ["Chance creation", "Through balls", "Key passing"],
        "Dribbling":   ["Dribbling"], "Passing": ["Passing"],
        "Fouls drawn": ["Fouls drawn"], "Minutes": ["Minutes"],
        "Age (younger)": ["Age (younger)"],
    }
    block_scores = pd.DataFrame({n: scores[c].mean(axis=1) for n, c in blocks_7.items()})
    print(f"retention, 7 blocks (top 15%):  {retention(flat_index(block_scores), mv):.1%}")

    print("\n--- market value as ground truth: why it's weak ---")
    res = scoring.build()
    age, priced = res["age"], res["priced"]
    band = pd.cut(age, bins=[0, 19, 21, 25, 200], labels=["16-19", "20-21", "22-25", "26+"])
    print("share with no market value, by age band:")
    print(((~priced).groupby(band, observed=True).mean() * 100).round(0).to_string())
    print(f"of priced players, share aged 26+: {(res.loc[priced, 'age'] >= 26).mean()*100:.1f}%")

    priced_mv = res.loc[priced, "market_value"]
    print(f"\ndistinct market values among {len(priced_mv)} priced players: {priced_mv.nunique()}")
    top6 = priced_mv.value_counts().nlargest(6)
    print(f"share of priced players in the 6 most common values: {top6.sum()/len(priced_mv)*100:.1f}%")

    res["league_median_mv"] = res.groupby("league_code")["market_value"].transform("median")
    r = res.loc[priced, "league_median_mv"].corr(res.loc[priced, "market_value"])
    print(f"corr(own price, league median price) = {r:.3f}")

    cov = res.groupby("league_code")["priced"].mean() * 100
    print(f"market-value coverage by league: {cov.min():.0f}% to {cov.max():.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

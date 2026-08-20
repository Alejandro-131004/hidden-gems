#!/usr/bin/env python3
"""scoring.py - the hidden-gems candidate filter, in one importable place.

This module exists so the notebooks explain rather than calculate. Import it,
call `build()`, and reason about the frame that comes back.

WHAT THIS IS. A deterministic subsample. It narrows ~40k players down to a
candidate list small enough to look at by hand or feed to a trained model. It is
NOT a valuation model and it does not try to reproduce market value. The goal is
potential, not price: `Market value` never enters the score, in either direction,
so a priced player and an unpriced one are ranked on the same terms. The pool
happens to skew unpriced - every 16-19 year old in this base carries no market
value at all - but that is a fact about who plays in these leagues, not the
target the filter is chasing.

Success for a filter is recall, not agreement with price: keep the interesting
players while cutting the search space. Judge it on what it retains, not on how
closely it tracks the market.

HOW A SCORE IS BUILT, in order:

    1. eligibility      - MIN_MINUTES of playing time, a known role
    2. rate metrics     - the role's own metrics, percentiled WITHIN league x role
                          so a league's level is not baked into the number
    3. small cells      - a league x role group under MIN_CELL falls back to the
                          same role pooled across leagues of similar strength
    4. thin percentages - a percentage over few attempts (100% off 1.1 dribbles)
                          is shrunk toward the role mean in proportion to volume
    5. thin seasons     - the whole score is shrunk toward the role mean by
                          sqrt(minutes / FULL_SEASON); half a season is evidence,
                          not proof
    6. league level     - only NOW is league strength applied, as a coefficient
                          from data/league_map.csv, to make leagues comparable
    7. age              - kept OUT of the performance score entirely. It lives in
                          its own column and belongs to the potential side, not
                          to how well someone played

Age and minutes are deliberately not skills. Averaging "how young" into "how well
he played" makes both unreadable, and it made the age term correlate -0.20 with
the very index it was part of.

Run as a script for a quick look:
    python scoring.py
    python scoring.py --season 2025-2026 --role CF --top 20
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
REFERENCE_HEADER = REPO / "docs" / "reference-header.txt"
LEAGUE_MAP = DATA / "league_map.csv"

SEASON = "2025-2026"
MIN_MINUTES = 600          # eligibility floor
FULL_SEASON = 1800         # minutes above which a season counts as full evidence
MIN_CELL = 10              # smallest league x role group we trust to percentile in
MIN_ATTEMPTS = 20          # attempts below which a percentage is shrunk
LEAGUE_WEIGHT = 0.35       # how much of the final score is league level (see below)

FOLDER_RE = re.compile(r"^(\d+)\s*-\s*\[([A-Za-z]{2,3})\]\s*-\s*\((.+)\)$")


# --------------------------------------------------------------------------
# roles
# --------------------------------------------------------------------------
ROLE = {}
for _p in ["GK"]:                                   ROLE[_p] = "GK"
for _p in ["CB", "RCB", "LCB"]:                     ROLE[_p] = "CB"
for _p in ["RB", "LB", "RWB", "LWB"]:               ROLE[_p] = "FB"
for _p in ["DMF", "RDMF", "LDMF"]:                  ROLE[_p] = "DM"
for _p in ["CMF", "RCMF", "LCMF"]:                  ROLE[_p] = "CM"
for _p in ["AMF"]:                                  ROLE[_p] = "AM"
for _p in ["LW", "RW", "LWF", "RWF", "LAMF", "RAMF"]: ROLE[_p] = "W"
for _p in ["CF"]:                                   ROLE[_p] = "CF"

ROLE_LABEL = {"GK": "Goalkeeper", "CB": "Centre back", "FB": "Full back",
              "DM": "Defensive midfielder", "CM": "Central midfielder",
              "AM": "Attacking midfielder", "W": "Winger", "CF": "Centre forward"}

# The role metric sets, as mapped from the research in TODO.md. Each role is
# scored on its own craft, so a centre-back is never ranked on a striker's
# shooting volume.
ROLE_METRICS = {
    "GK": ["Save rate, %", "Prevented goals per 90", "Conceded goals per 90",
           "Exits per 90", "Accurate passes, %", "Accurate long passes, %"],
    "CB": ["Defensive duels won, %", "Aerial duels won, %", "PAdj Interceptions",
           "Shots blocked per 90", "Successful defensive actions per 90",
           "Accurate passes, %", "Accurate progressive passes, %"],
    "FB": ["Defensive duels won, %", "PAdj Interceptions", "Successful defensive actions per 90",
           "Crosses per 90", "Accurate crosses, %", "Progressive runs per 90",
           "xA per 90", "Key passes per 90"],
    "DM": ["PAdj Interceptions", "Defensive duels won, %", "Successful defensive actions per 90",
           "Accurate passes, %", "Accurate forward passes, %", "Progressive passes per 90",
           "Received passes per 90"],
    "CM": ["Progressive passes per 90", "Accurate passes, %", "Key passes per 90",
           "xA per 90", "Progressive runs per 90", "Passes to final third per 90",
           "Duels won, %"],
    "AM": ["xA per 90", "Key passes per 90", "Through passes per 90", "Shot assists per 90",
           "Passes to penalty area per 90", "Dribbles per 90", "Successful dribbles, %",
           "Touches in box per 90", "xG per 90"],
    "W": ["Successful dribbles, %", "Progressive runs per 90", "Accelerations per 90",
          "xA per 90", "Key passes per 90", "Crosses per 90", "Accurate crosses, %",
          "xG per 90", "Touches in box per 90", "Fouls suffered per 90"],
    "CF": ["Non-penalty goals per 90", "xG per 90", "Goal conversion, %", "Shots on target, %",
           "Touches in box per 90", "Head goals per 90", "Aerial duels won, %",
           "Fouls suffered per 90"],
}

# metrics where a LOWER value is better
LOWER_IS_BETTER = {"Conceded goals per 90", "xG against per 90", "Fouls per 90"}

# A percentage is only as trustworthy as the attempts behind it. Each ratio is
# paired with the per-90 volume column that produces its denominator, so a 100%
# built on one attempt can be told apart from one built on eighty.
PERCENTAGE_VOLUME = {
    "Successful dribbles, %": "Dribbles per 90",
    "Shots on target, %": "Shots per 90",
    "Goal conversion, %": "Shots per 90",
    "Accurate crosses, %": "Crosses per 90",
    "Accurate passes, %": "Passes per 90",
    "Accurate long passes, %": "Long passes per 90",
    "Accurate forward passes, %": "Forward passes per 90",
    "Accurate progressive passes, %": "Progressive passes per 90",
    "Accurate through passes, %": "Through passes per 90",
    "Defensive duels won, %": "Defensive duels per 90",
    "Aerial duels won, %": "Aerial duels per 90",
    "Duels won, %": "Duels per 90",
    "Save rate, %": "Shots against per 90",
}


# --------------------------------------------------------------------------
# loading, with a header contract
# --------------------------------------------------------------------------
class HeaderMismatch(RuntimeError):
    """A CSV whose columns differ from the canonical Wyscout export."""


def reference_header():
    if not REFERENCE_HEADER.exists():
        raise FileNotFoundError(
            f"{REFERENCE_HEADER} is missing - it is the contract every league CSV "
            f"is checked against. Regenerate it from a known-good export.")
    return [ln.strip() for ln in REFERENCE_HEADER.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def load_players(season=SEASON, strict=True):
    """Read every league CSV of a season, validating each against the reference.

    A different Wyscout preset silently produces missing columns, which then flow
    through the percentiles as NaN and quietly become bottom-of-the-pack scores.
    That is the failure this refuses to let through.
    """
    ref = reference_header()
    root = DATA / season
    if not root.is_dir():
        raise FileNotFoundError(f"no such season folder: {root}")

    frames, skipped, problems = [], [], []
    for folder in sorted(d for d in root.iterdir() if d.is_dir()):
        m = FOLDER_RE.match(folder.name)
        csvs = [p for p in folder.glob("*.csv")]
        if not csvs:
            skipped.append(folder.name)
            continue
        df = pd.read_csv(csvs[0], low_memory=False)
        missing = [c for c in ref if c not in df.columns]
        extra = [c for c in df.columns if c not in ref]
        if missing or extra:
            msg = f"{folder.name}: {len(missing)} missing, {len(extra)} unexpected"
            if missing[:3]:
                msg += f" | missing e.g. {missing[:3]}"
            problems.append(msg)
            if strict:
                continue
        df = df.reindex(columns=ref)
        df["folder"] = folder.name
        df["country"] = m.group(2).upper() if m else ""
        df["league"] = m.group(3) if m else folder.name
        df["season"] = season
        frames.append(df)

    if not frames:
        raise RuntimeError(f"no usable CSV under {root}")

    players = pd.concat(frames, ignore_index=True)
    players.attrs["skipped_folders"] = skipped
    players.attrs["header_problems"] = problems
    if problems:
        print(f"HEADER MISMATCH in {len(problems)} folder(s)"
              f"{' - skipped' if strict else ' - loaded anyway'}:")
        for p in problems:
            print("   ", p)
    return players


def load_league_map():
    if not LEAGUE_MAP.exists():
        raise FileNotFoundError(
            f"{LEAGUE_MAP} is missing - run devkit/make_league_map.py first. "
            f"Without it there is no way to compare one league against another.")
    lm = pd.read_csv(LEAGUE_MAP)
    if lm["strength"].isna().any():
        n = int(lm["strength"].isna().sum())
        print(f"note: {n} league(s) have no strength yet - their players are scored "
              f"within their own league but left out of cross-league comparison")
    return lm


# --------------------------------------------------------------------------
# the pieces of the score
# --------------------------------------------------------------------------
def attempts_for(df, pct_col):
    """Season attempts behind a percentage column, from its per-90 volume."""
    vol = PERCENTAGE_VOLUME.get(pct_col)
    if vol is None or vol not in df.columns:
        return None
    per90 = pd.to_numeric(df[vol], errors="coerce")
    minutes = pd.to_numeric(df["Minutes played"], errors="coerce")
    return per90 * minutes / 90.0


def shrink(values, weights, target=0.5):
    """Pull a percentile toward `target` when the evidence behind it is thin.

    weights is 0..1: 1 keeps the value untouched, 0 replaces it with the target.
    Used twice - for thin percentages, and for thin seasons.
    """
    w = weights.clip(0.0, 1.0).fillna(0.0)
    return target + (values.fillna(target) - target) * w


def role_percentiles(df, role, metrics, cell_col="_cell"):
    """Percentile every metric of a role within its league, not across all leagues.

    Percentiling across 75 leagues at once bakes league level into the metric and
    lets a weak league's leader outrank a strong league's. Doing it within the
    league removes that - and then, separately, the league strength coefficient
    puts the levels back in on purpose rather than by accident.
    """
    out = pd.DataFrame(index=df.index)
    for col in metrics:
        if col not in df.columns:
            continue
        raw = pd.to_numeric(df[col], errors="coerce")
        pct = raw.groupby(df[cell_col]).rank(pct=True)
        if col in LOWER_IS_BETTER:
            pct = 1.0 - pct
        att = attempts_for(df, col)
        if att is not None:
            pct = shrink(pct, (att / MIN_ATTEMPTS), target=0.5)
        out[col] = pct
    return out


def build(season=SEASON, min_minutes=MIN_MINUTES, min_attempts=MIN_ATTEMPTS,
          league_weight=LEAGUE_WEIGHT, strict=True):
    """Score every eligible player and return one row per player.

    Columns added:
        role, role_label     positional group
        perf_league          0-100, how good within his own league and role
        perf_adjusted        0-100, after the league strength coefficient
        evidence             0-1, how much season is behind the number
        age, minutes         kept separate, never folded into performance
        market_value         NaN when the player is unpriced (0 means no price,
                             not "worth zero")
        priced               whether a market value exists at all
    """
    global MIN_ATTEMPTS
    MIN_ATTEMPTS = min_attempts

    players = load_players(season, strict=strict)
    lm = load_league_map()

    # 8. role, with NA kept distinct from a real default
    first_pos = players["Position"].map(
        lambda p: None if pd.isna(p) else str(p).split(",")[0].strip())
    players["role"] = first_pos.map(lambda p: ROLE.get(p) if p else None)
    players["role"] = players["role"].where(first_pos.notna(), "NA")
    unknown = players.loc[players["role"].isna(), :]
    if len(unknown):
        codes = sorted(set(first_pos[players["role"].isna()].dropna()))
        print(f"note: {len(unknown)} player(s) with unmapped position codes {codes} "
              f"- excluded, not silently bucketed")

    players = players.merge(
        lm[["folder", "league_code", "priority", "priority_rank", "division", "strength"]],
        on="folder", how="left")

    minutes = pd.to_numeric(players["Minutes played"], errors="coerce")
    eligible = players[(minutes >= min_minutes) & players["role"].notna()
                       & (players["role"] != "NA")].copy()

    # a strength band, used only as the fallback pool for thin league x role cells
    st = pd.to_numeric(eligible["strength"], errors="coerce")
    eligible["_band"] = pd.cut(st, bins=[-0.1, 15, 30, 50, 101],
                               labels=["d", "c", "b", "a"]).astype(str)
    eligible["_cell"] = eligible["league_code"].astype(str) + "|" + eligible["role"]

    # 3. small cells fall back to the same role across leagues of similar level
    counts = eligible.groupby("_cell")["Player"].transform("size")
    thin = counts < MIN_CELL
    eligible.loc[thin, "_cell"] = ("band:" + eligible.loc[thin, "_band"]
                                   + "|" + eligible.loc[thin, "role"])
    if thin.any():
        print(f"note: {int(thin.sum())} player(s) in league x role cells under "
              f"{MIN_CELL} - percentiled against their strength band instead")

    scored = []
    for role, sub in eligible.groupby("role"):
        metrics = ROLE_METRICS.get(role)
        if not metrics:
            continue
        sub = sub.copy()
        pcts = role_percentiles(sub, role, metrics)
        raw = pcts.mean(axis=1)

        # 5. a short season is evidence, not proof
        mins = pd.to_numeric(sub["Minutes played"], errors="coerce")
        sub["evidence"] = np.sqrt(np.minimum(1.0, mins / FULL_SEASON)).round(3)
        sub["perf_league"] = (shrink(raw, sub["evidence"], target=0.5) * 100).round(1)

        # 6. league level, applied only now and only here
        strength = pd.to_numeric(sub["strength"], errors="coerce") / 100.0
        sub["perf_adjusted"] = (
            (sub["perf_league"] / 100.0) * (1 - league_weight)
            + strength * league_weight) * 100
        sub["perf_adjusted"] = sub["perf_adjusted"].where(strength.notna()).round(1)
        for col in metrics:
            if col in pcts:
                sub[f"pct::{col}"] = (pcts[col] * 100).round(1)
        scored.append(sub)

    res = pd.concat(scored, ignore_index=True)

    mv = pd.to_numeric(res["Market value"], errors="coerce")
    res["market_value"] = mv.where(mv > 0)          # 0 means unpriced
    res["priced"] = res["market_value"].notna()
    res["age"] = pd.to_numeric(res["Age"], errors="coerce")     # 7. never in the score
    res["minutes"] = pd.to_numeric(res["Minutes played"], errors="coerce")
    res["role_label"] = res["role"].map(ROLE_LABEL)
    res["club_season"] = res["Team within selected timeframe"]
    res["club_now"] = res["Team"]

    res["rank_in_league"] = res.groupby(["league_code", "role"])["perf_league"] \
                               .rank(ascending=False, method="min")
    res["rank_overall"] = res.groupby("role")["perf_adjusted"] \
                             .rank(ascending=False, method="min")
    return res


def shortlist(res, role=None, league=None, max_age=None, top=15, priority=None):
    """The columns a scout reads, in the order a scout reads them.

    `priority` filters by the Dribblify scouting bands, where green is the most
    important, then yellow, then red. Pass "green", a list like
    ["green", "yellow"], or a number to mean "this band or better": priority=2
    keeps green and yellow. It never touches the score, only which leagues are
    on the table.
    """
    d = res
    if role:
        d = d[d["role"] == role]
    if league:
        d = d[d["league"].str.contains(league, case=False, na=False)]
    if max_age:
        d = d[d["age"] <= max_age]
    if priority is not None:
        if isinstance(priority, (int, float)):
            d = d[pd.to_numeric(d["priority_rank"], errors="coerce") <= priority]
        else:
            bands = [priority] if isinstance(priority, str) else list(priority)
            d = d[d["priority"].isin(bands)]
    cols = ["Player", "club_season", "league", "age", "minutes",
            "market_value", "perf_league", "perf_adjusted", "evidence"]
    sort_col = "perf_adjusted" if d["perf_adjusted"].notna().any() else "perf_league"
    return d.nlargest(top, sort_col)[cols].reset_index(drop=True)


def main():
    argv = sys.argv[1:]
    def opt(flag, default=None, cast=str):
        return cast(argv[argv.index(flag) + 1]) if flag in argv else default
    season = opt("--season", SEASON)
    role = opt("--role", "CF")
    priority = opt("--priority", None)
    top = opt("--top", 15, int)

    res = build(season=season)
    print(f"\n{len(res)} eligible players | {res['league_code'].nunique()} leagues "
          f"| {res['role'].nunique()} roles")
    print(f"{int(res['priced'].sum())} priced, {int((~res['priced']).sum())} unpriced\n")
    print(f"top {top} - {ROLE_LABEL.get(role, role)}")
    print(shortlist(res, role=role, top=top, priority=priority).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#---------------------- Frameworks --------------------------#

import re
from pathlib import Path
import pandas as pd
import textwrap
import numpy as np

LEAGUE_RE = re.compile(r"\(([^)]+)\)")
COUNTRY_RE = re.compile(r"\[([^\]]+)\]")

# Maps Wyscout primary position code -> role bucket
ROLE = {}
for p in ["GK"]:                         ROLE[p] = "GK"
for p in ["CB","RCB","LCB"]:             ROLE[p] = "CB"
for p in ["RB","LB","RWB","LWB"]:        ROLE[p] = "FB"
for p in ["DMF","RDMF","LDMF"]:          ROLE[p] = "DM"
for p in ["CMF","RCMF","LCMF"]:          ROLE[p] = "CM"
for p in ["AMF"]:                        ROLE[p] = "AM"
for p in ["LW","RW","LWF","RWF","LAMF","RAMF"]: ROLE[p] = "W"
for p in ["CF"]:                         ROLE[p] = "CF"

LEADS = set(k for k in ROLE.values())

#-------------------- Functions --------------------------#

# Parse Folders & Csvs

def parse_league(path):
    # '1 - [GER] - (2. Bundesliga).csv' -> '2. Bundesliga'
    m = LEAGUE_RE.search(path.stem)
    if not m:
        raise ValueError(f"no league in parentheses: {path.name}")
    return m.group(1).strip()


def parse_country(path):
    # '1 - [GER] - (2. Bundesliga).csv' -> 'GER'
    m = COUNTRY_RE.search(path.stem)
    return m.group(1).strip() if m else None


# Creates global dataframe

def mkSeasonDf(folder_name, root="../data", league_from=parse_league,
               out_path=None, min_cols=115, verbose=0):

    # walks given directory and concatenates all csv into a single
    # dataframe, adding a 'league' column based on the csv filename

    base = Path(root) / folder_name
    if not base.is_dir(): raise NotADirectoryError(f"{base.resolve()} does not exist")

    frames, added, skipped, flagged = [], [], [], []

    for sub in sorted(p for p in base.iterdir() if p.is_dir()):
        csvs = sorted(sub.glob("*.csv"))
        if not csvs:
            skipped.append((sub.name, "no csv found"))
            continue

        for csv in csvs:
            try:
                df = pd.read_csv(csv)
            except Exception as e:
                skipped.append((sub.name, f"{csv.name}: {type(e).__name__} : {e}"))
                continue

            if df.empty:
                skipped.append((sub.name, f"{csv.name}: empty file"))
                continue

            ncols = df.shape[1]
            if ncols < min_cols:
                flagged.append((sub.name, csv.name, ncols))

            df.insert(0, "league", league_from(csv))
            frames.append(df)
            added.append((sub.name, csv.name, len(df)))

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if out_path and not combined.empty:
        combined.to_csv(out_path, index=False)

    if verbose >= 1:
        print(f"ADDED ({len(added)}):")
        for sub, name, n in added:
            print(f"  ✓ {sub}:  {n} rows")

        print(f"\nNOT ADDED ({len(skipped)}):")
        for sub, reason in skipped:
            print(f"  ✗ {sub}  :  {reason}")

        print(f"\nTotal: {len(combined)} rows, "
              f"{combined['league'].nunique() if not combined.empty else 0} leagues")

    if len(flagged) > 0:
        print(f"\nFLAGGED [{folder_name}]: under {min_cols} columns! ({len(flagged)}):")
        for sub, name, n in flagged: print(f"  ! {sub}  :  {n} cols (missing {min_cols - n})")

    return combined

# Identify NaN (Global, with Plots)

def nan_groups(df, name="", verbose=1, drop_zero=False, ax=None):
    """Group columns by identical NaN counts. Plot group sizes vs % NaN, then list members."""
    n_rows = len(df)
    nan_count = df.isna().sum()
    if drop_zero:
        nan_count = nan_count[nan_count > 0]
    if nan_count.empty:
        print(f"{name}: no NaNs anywhere")
        return {}

    pct = (nan_count / n_rows * 100).round(2)
    groups = {p: list(pct.index[pct == p]) for p in sorted(pct.unique(), reverse=False)}

    # ---------- plot ----------
    if ax is None:
        _, ax = plt.subplots(figsize=(10, 4))
    xs = [f"{p:.2f}%" for p in groups]
    ys = [len(v) for v in groups.values()]
    pos = range(len(xs))

    bars = ax.bar(pos, ys, color="steelblue", width=0.75)
    top = max(ys)
    for b, y in zip(bars, ys):
        inside = b.get_height() >= top * 0.08          # too short to hold a label
        ax.text(b.get_x() + b.get_width() / 2,
                b.get_height() - top * 0.03 if inside else b.get_height() + top * 0.015,
                str(y),
                ha="center", va="top" if inside else "bottom",
                fontsize=8, fontweight="bold",
                color="white" if inside else "black")

    ax.set_xticks(pos, xs, rotation=90, fontsize=8)
    ax.set_ylim(0, top * 1.12)
    ax.set_xlabel("% NaN")
    ax.set_ylabel("columns in group")
    ax.set_title(f"{name}  —  {len(pct)} columns, {len(groups)} distinct NaN levels")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)

    # ---------- print ----------
    if verbose:
        width = max(len(f"{p:.2f}") for p in groups)
        print(f"\n{'=' * 70}\n{name}   ({n_rows} rows, {len(pct)} columns)\n{'=' * 70}")
        for p, cols in groups.items():
            members = cols if verbose >= 2 else [df.columns.get_loc(c) for c in cols]
            body = ", ".join(map(str, members))
            head = f"{p:>{width}.2f}%  n={len(cols):<4}"
            print(textwrap.fill(body, width=100, initial_indent=head + " [",
                    subsequent_indent=" " * (len(head) + 2)) + "]")
    pos_of = {c: i for i, c in enumerate(df.columns)}
    return {f"{p:.2f}": [pos_of[c] for c in cols] for p, cols in groups.items()}



# Identify NaN (Role Specific)


#---------------------- Frameworks --------------------------#

import re
from pathlib import Path
import pandas as pd

LEAGUE_RE = re.compile(r"\(([^)]+)\)")
COUNTRY_RE = re.compile(r"\[([^\]]+)\]")


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
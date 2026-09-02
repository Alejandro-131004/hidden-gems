#!/usr/bin/env python3
"""
combine_leagues.py — for every subfolder inside ROOT_FOLDER, merge its age-band
xlsx exports into one CSV named after that subfolder.

Set ROOT_FOLDER below (or pass a path as the first argument). For each subfolder
it reads every .xlsx, stacks them, drops duplicate rows (the age bands overlap —
14-21 and 21-60 both hold the 21-year-olds), sorts descending by Market value,
and writes  "<subfolder name>.csv"  inside that subfolder.
"""

import re
import sys
from pathlib import Path

import pandas as pd

SEASON = str(input("Season (e.g. 2025-26): ").strip())
# ------------------

# This script lives in devkit/. The data folder is one level out:
# devkit/ -> repo root -> data/<SEASON>.
ROOT_FOLDER = Path(__file__).resolve().parent.parent / "data" / SEASON

SORT_COL = "Market value"


def natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def combine_one(folder):
    xlsx = sorted([p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")], key=natural)
    if not xlsx:
        print(f"  [skip] {folder.name}  (no .xlsx)")
        return False

    frames = [pd.read_excel(p) for p in xlsx]
    cols0 = list(frames[0].columns)
    for p, f in zip(xlsx[1:], frames[1:]):
        if list(f.columns) != cols0:
            print(f"      ! {p.name} has different columns than {xlsx[0].name}")

    df = pd.concat(frames, ignore_index=True)
    before = len(df)
    # Age bands share their boundary year, so a 21-year-old is in both files as an
    # IDENTICAL row (stats are season totals) — full-row dedup drops exactly those.
    df = df.drop_duplicates(ignore_index=True)
    removed = before - len(df)

    if SORT_COL in df.columns:
        mv = pd.to_numeric(df[SORT_COL], errors="coerce")
        order = mv.sort_values(ascending=False, na_position="last", kind="mergesort").index
        df = df.loc[order].reset_index(drop=True)
    else:
        print(f"      ! no '{SORT_COL}' column — left unsorted")

    dest = folder / f"{folder.name}.csv"
    df.to_csv(dest, index=False, encoding="utf-8-sig")
    #print(f"  {folder.name}")
    #print(f"      {len(xlsx)} file(s) -> {len(df)} rows "
    #      f"({removed} duplicate{'s' if removed != 1 else ''} removed) -> {dest.name}")

    # verify against SIZE.txt (the Wyscout result count you recorded)
    verify(folder, len(df))
    return True


def verify(folder, n_rows):
    """Expected count comes from the NAME of a .txt file, e.g. 538.txt -> 538."""
    txts = list(folder.glob("*.txt"))
    numbered = [(t, int(re.search(r"\d+", t.stem).group()))
                for t in txts if re.search(r"\d+", t.stem)]

    if not numbered:
        if txts:  # e.g. the placeholder SIZE.txt
            names = ", ".join(t.name for t in txts)
            print(f"      no numbered .txt — rename {names} to the count, e.g. 538.txt")
        else:
            print("      no .txt with the count — add one named after the row count, e.g. 538.txt")
        return

    if len(numbered) > 1:
        print(f"      ! more than one numbered .txt: {', '.join(t.name for t, _ in numbered)} "
              f"— verification skipped")
        return

    t, expected = numbered[0]
    if expected != n_rows:
        print(f"  {folder.name}")
        print(f"      VERIFY FAIL  csv has {n_rows} rows but {t.name} says {expected} "
              f"(off by {n_rows - expected:+d})")


    # print(f"      VERIFY OK  ({n_rows} rows = {t.name})")


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(ROOT_FOLDER)
    if not root.is_dir():
        print(f"not a folder: {root.resolve()}")
        return 1

    subfolders = sorted([d for d in root.iterdir() if d.is_dir()], key=natural)
    print(f"root       : {root.resolve()}")
    print(f"subfolders : {len(subfolders)}\n")

    made = sum(combine_one(d) for d in subfolders)
    print(f"\n{made} CSV(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())

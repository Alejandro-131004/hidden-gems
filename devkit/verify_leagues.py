#!/usr/bin/env python3
"""
verify_leagues.py — check every league folder in a season and report its state.

For each subfolder it compares the row count of "<folder>.csv" against the
number taken from the numbered .txt file (e.g. 538.txt), and reports anything
that is missing, empty or inconsistent. Read-only on the data: the only file it
writes is the report.

Run:
    python verify_leagues.py                          # defaults to data/<SEASON>
    python verify_leagues.py data/2024-2025
    python verify_leagues.py data/2024-2025 --all     # list OK folders too
    python verify_leagues.py -o report.txt            # choose the report path

The report is written to verify_report.txt next to this script unless -o says
otherwise, and is always printed to the terminal as well.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SEASON = "2025-2026"
ROOT_FOLDER = Path(__file__).resolve().parent.parent / "data" / SEASON
DEFAULT_OUT = Path(__file__).resolve().parent / "verify_report.txt"


def natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def expected_count(folder):
    """Read the expected row count from a numbered .txt filename, e.g. 538.txt.

    Returns (count, reason). count is None when it cannot be determined.
    """
    txts = list(folder.glob("*.txt"))
    numbered = [(t, int(re.search(r"\d+", t.stem).group()))
                for t in txts if re.search(r"\d+", t.stem)]
    if not numbered:
        if txts:
            return None, f"no numbered .txt (found {', '.join(t.name for t in txts)})"
        return None, "no .txt with the expected count"
    if len(numbered) > 1:
        return None, f"several numbered .txt: {', '.join(t.name for t, _ in numbered)}"
    return numbered[0][1], None


def check_one(folder):
    """Return (status, message, n_rows). Status is OK, FAIL, EMPTY or NO_CSV."""
    xlsx = [p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")]
    csv = folder / f"{folder.name}.csv"

    if not csv.exists():
        if not xlsx:
            return "EMPTY", "no .xlsx and no .csv - nothing exported yet", 0
        return "NO_CSV", f"{len(xlsx)} .xlsx present but no .csv - run combine_leagues.py", 0

    try:
        n_rows = len(pd.read_csv(csv))
    except Exception as exc:
        return "FAIL", f"could not read {csv.name}: {exc}", 0

    expected, reason = expected_count(folder)
    if expected is None:
        return "FAIL", f"{n_rows} rows but {reason}", n_rows
    if expected != n_rows:
        return "FAIL", f"csv has {n_rows} rows, expected {expected} ({n_rows - expected:+d})", n_rows
    return "OK", f"{n_rows} rows", n_rows


def parse_args(argv):
    """Return (root, show_all, out_path)."""
    positional, out, show_all, i = [], DEFAULT_OUT, False, 0
    while i < len(argv):
        if argv[i] in ("-o", "--out") and i + 1 < len(argv):
            out = Path(argv[i + 1]); i += 2
        elif argv[i] == "--all":
            show_all = True; i += 1
        else:
            positional.append(argv[i]); i += 1
    root = Path(positional[0]) if positional else ROOT_FOLDER
    return root, show_all, out


def main():
    root, show_all, out = parse_args(sys.argv[1:])

    lines = []
    def add(text=""):
        lines.append(text)

    if not root.is_dir():
        print(f"not a folder: {root.resolve()}")
        return 1

    folders = sorted([d for d in root.iterdir() if d.is_dir()], key=natural)

    add("=" * 60)
    add("LEAGUE VERIFICATION")
    add("=" * 60)
    add(f"generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    add(f"root      : {root.resolve()}")
    add(f"folders   : {len(folders)}")
    add()

    results, total_rows = {}, 0
    for folder in folders:
        status, message, n_rows = check_one(folder)
        results.setdefault(status, []).append((folder.name, message))
        if status == "OK":
            total_rows += n_rows

    for status in ("FAIL", "NO_CSV", "EMPTY"):
        rows = results.get(status, [])
        if not rows:
            continue
        add(f"{status}  ({len(rows)})")
        for name, message in rows:
            add(f"    {name}")
            add(f"        {message}")
        add()

    ok = results.get("OK", [])
    if show_all and ok:
        add(f"OK  ({len(ok)})")
        for name, message in ok:
            add(f"    {name:<45} {message}")
        add()

    add("-" * 60)
    add(f"OK      {len(ok)}")
    add(f"FAIL    {len(results.get('FAIL', []))}")
    add(f"NO_CSV  {len(results.get('NO_CSV', []))}")
    add(f"EMPTY   {len(results.get('EMPTY', []))}")
    add(f"players in verified leagues: {total_rows}")

    text = "\n".join(lines)
    print(text)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"\nwritten to {out.resolve()}")

    return 1 if results.get("FAIL") or results.get("NO_CSV") else 0


if __name__ == "__main__":
    sys.exit(main())
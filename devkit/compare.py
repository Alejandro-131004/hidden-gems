#!/usr/bin/env python3
"""
compare.py — confirm two exports hold the same players, cell for cell.

The flow, in the order it runs:
    1. build a  Player | Team | Age  key column inside df1
    2. build the same key column inside df2
    3. print head(5) of each, sorted by Market value descending, so you can
       eyeball that both start with the same top players
    4. compare every cell, matching rows BY KEY (not by position — the two files
       can be in any order)

Duplicate keys are flagged: if two rows share a Player|Team|Age they can't be
matched one-to-one, so the report tells you and you add a column to the key.

Use in a notebook / code:
    from compare import compare
    compare(df1, df2)                                  # default key below
    compare(df1, df2, key=["Player", "Team", "Age"])   # or choose your own

Or as a script:
    python compare.py file1.csv file2.xlsx
    python compare.py a.csv b.xlsx -o report.txt --key "Player,Team,Age"
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

MAX_LISTED = 500            # differences written out in full
KEY_COL = "Key"            # name of the key column added into each frame
SORT_COL = "Market value"  # preview is sorted by this, descending
PREVIEW_N = 5              # rows shown in the preview

# The identifying key. There is no numeric player id in the Wyscout export, so
# identity is a composite. Age is in it because Player+Team is NOT unique — three
# different "M. Schulz" play for Preußen Münster (ages 31, 30, 22).
DEFAULT_KEY = ["Player", "Team", "Age"]


def load(path):
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path)
    if path.suffix.lower() in (".tsv", ".txt"):
        return pd.read_csv(path, sep="\t")
    return pd.read_csv(path)


def same(a, b):
    """True if two cells match. NaN matches NaN. 3 matches 3.0."""
    a_null, b_null = pd.isna(a), pd.isna(b)
    if a_null or b_null:
        return bool(a_null and b_null)
    if a is b or a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        pass
    return str(a).strip() == str(b).strip()


def make_key(df, key):
    """Return a  'Player | Team | Age'  string Series for df.

    Missing values become the literal '<NA>' so a blank team (pandas keeps it as
    NaN through astype(str)) can't poison the key or break sorting later.
    """
    parts = [df[c].astype("string").fillna("<NA>").str.strip() for c in key]
    out = parts[0]
    for p in parts[1:]:
        out = out + " | " + p
    return out.astype(str).reset_index(drop=True)


def preview(df, name, add):
    """Write head(PREVIEW_N) of df, sorted by Market value descending."""
    add(f"  {name}:")
    cols = [KEY_COL] + ([SORT_COL] if SORT_COL in df.columns else [])
    if SORT_COL in df.columns:
        mv = pd.to_numeric(df[SORT_COL], errors="coerce")
        top = df.assign(_mv=mv).sort_values("_mv", ascending=False, na_position="last").head(PREVIEW_N)
    else:
        add(f"    (no '{SORT_COL}' column — showing first {PREVIEW_N} rows unsorted)")
        top = df.head(PREVIEW_N)
    add(f"    {KEY_COL:<45} {SORT_COL if SORT_COL in df.columns else '':>14}")
    add(f"    {'-'*45} {'-'*14}")
    for _, row in top.iterrows():
        mv = f"{row[SORT_COL]}" if SORT_COL in df.columns else ""
        add(f"    {str(row[KEY_COL])[:45]:<45} {mv:>14}")
    add("")


def compare(df1, df2, out="comparison_report.txt", name1="df1", name2="df2", key=None):
    """Compare two frames.

    key=None  -> POSITIONAL: row i of df1 vs row i of df2, in whatever order
                 the frames are already in. Sort them yourself first.
    key=[...] -> KEY-MATCHED: rows are lined up by that key regardless of order,
                 and duplicate keys are flagged.
    """
    L = []
    add = L.append
    passed = {}

    df1 = df1.reset_index(drop=True).copy()
    df2 = df2.reset_index(drop=True).copy()
    c1, c2 = list(df1.columns), list(df2.columns)

    add("=" * 70)
    add("COMPARISON REPORT")
    add("=" * 70)
    add(f"generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    add(f"{name1:9} : {len(df1)} rows x {len(df1.columns)} columns")
    add(f"{name2:9} : {len(df2)} rows x {len(df2.columns)} columns")
    add(f"mode      : {'KEY-MATCHED ' + str(key) if key else 'POSITIONAL (row i vs row i)'}")
    add("")

    # -------------------------------------------------- shape
    add("-" * 70); add("SHAPE"); add("-" * 70)
    passed["rows"] = len(df1) == len(df2)
    passed["cols"] = len(c1) == len(c2)
    passed["names"] = c1 == c2
    add(f"rows        : {name1}={len(df1)}  {name2}={len(df2)}   "
        f"{'OK' if passed['rows'] else 'DIFFER'}")
    add(f"columns     : {name1}={len(c1)}  {name2}={len(c2)}   "
        f"{'OK' if passed['cols'] else 'DIFFER'}")
    add(f"column names: {'identical & same order' if passed['names'] else 'DIFFER'}")
    if not passed["names"]:
        o1 = [c for c in c1 if c not in set(c2)]
        o2 = [c for c in c2 if c not in set(c1)]
        if o1: add(f"  only in {name1}: {o1}")
        if o2: add(f"  only in {name2}: {o2}")
    add("")

    shared = [c for c in c1 if c in set(c2)]

    if key:
        # ================================================== KEY-MATCHED
        missing = [c for c in key if c not in df1.columns or c not in df2.columns]
        if missing:
            raise KeyError(f"key column(s) not in both files: {missing}")

        add("-" * 70); add("KEY  (built inside each frame)"); add("-" * 70)
        df1[KEY_COL] = make_key(df1, key)
        df2[KEY_COL] = make_key(df2, key)
        add(f"added '{KEY_COL}' column to both, from {key}")

        k1, k2 = df1[KEY_COL], df2[KEY_COL]
        vc1, vc2 = k1.value_counts(), k2.value_counts()
        dup1, dup2 = vc1[vc1 > 1], vc2[vc2 > 1]
        passed["key"] = dup1.empty and dup2.empty
        if passed["key"]:
            add("key is UNIQUE in both frames")
        else:
            add("KEY IS NOT UNIQUE — rows can't be matched one-to-one.")
            add(f"Add a column to the key (currently {key}) to separate them.")
            for nm, dup in ((name1, dup1), (name2, dup2)):
                if not dup.empty:
                    add(f"  in {nm}:")
                    for kv, n in dup.items(): add(f"    {n}x  {kv}")
        add("")

        set1, set2 = set(k1), set(k2)
        only1, only2 = sorted(set1 - set2), sorted(set2 - set1)
        add(f"keys in both      : {len(set1 & set2)}")
        add(f"only in {name1:9} : {len(only1)}")
        for kv in only1[:MAX_LISTED]: add(f"    {kv}")
        if len(only1) > MAX_LISTED: add(f"    ... {len(only1)-MAX_LISTED} more")
        add(f"only in {name2:9} : {len(only2)}")
        for kv in only2[:MAX_LISTED]: add(f"    {kv}")
        if len(only2) > MAX_LISTED: add(f"    ... {len(only2)-MAX_LISTED} more")
        passed["members"] = not only1 and not only2
        add("")

        add("-" * 70); add("CELL VALUES  (matched by key)"); add("-" * 70)
        dupset = set(dup1.index) | set(dup2.index)
        compare_cols = [c for c in shared if c not in key]
        common = sorted((set1 & set2) - dupset)
        if not compare_cols or not common:
            add("SKIPPED  nothing to compare")
            passed["cells"] = False
        else:
            if dupset: add(f"note: {len(dupset)} duplicated key(s) skipped")
            idx1 = {kv: i for i, kv in enumerate(k1) if kv not in dupset}
            idx2 = {kv: i for i, kv in enumerate(k2) if kv not in dupset}
            per_column, diffs = {}, []
            for kv in common:
                r1, r2 = df1.iloc[idx1[kv]], df2.iloc[idx2[kv]]
                for col in compare_cols:
                    if not same(r1[col], r2[col]):
                        per_column[col] = per_column.get(col, 0) + 1
                        diffs.append((str(kv), col, r1[col], r2[col]))
            total = len(common) * len(compare_cols)
            passed["cells"] = not diffs
            _report_diffs(add, diffs, per_column, total, len(common), name1, name2)

        summary = [("key unique", passed["key"]), ("row count", passed["rows"]),
                   ("column count", passed["cols"]), ("column names", passed["names"]),
                   ("same players", passed["members"]), ("cell values", passed["cells"])]

    else:
        # ================================================== POSITIONAL
        add("-" * 70); add("CELL VALUES  (row i vs row i, no key)"); add("-" * 70)
        n = min(len(df1), len(df2))
        compare_cols = shared
        if not passed["rows"]:
            add(f"note: row counts differ — comparing the first {n} rows only")
        if len(compare_cols) != len(c1) or len(compare_cols) != len(c2):
            add(f"note: comparing the {len(compare_cols)} shared columns")
        per_column, diffs = {}, []
        for i in range(n):
            r1, r2 = df1.iloc[i], df2.iloc[i]
            for col in compare_cols:
                if not same(r1[col], r2[col]):
                    per_column[col] = per_column.get(col, 0) + 1
                    diffs.append((f"row {i}", col, r1[col], r2[col]))
        total = n * len(compare_cols)
        passed["cells"] = not diffs and passed["rows"]
        _report_diffs(add, diffs, per_column, total, n, name1, name2, keyhdr="row")

        summary = [("row count", passed["rows"]), ("column count", passed["cols"]),
                   ("column names", passed["names"]), ("cell values", passed["cells"])]

    # -------------------------------------------------- summary
    add("")
    add("=" * 70); add("SUMMARY"); add("=" * 70)
    for label, ok in summary:
        add(f"  {label:<14} {'PASS' if ok else 'FAIL'}")
    add("")
    ok = all(v for _, v in summary)
    add("RESULT: IDENTICAL" if ok else "RESULT: DIFFERENCES FOUND")
    add("=" * 70)

    text = "\n".join(L)
    Path(out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten to {Path(out).resolve()}")
    return ok


def _report_diffs(add, diffs, per_column, total, n_rows, name1, name2, keyhdr="key"):
    add("")
    if not diffs:
        add(f"PASS  {n_rows} rows, {total} cells, 0 different")
        return
    add(f"FAIL  {n_rows} rows, {total} cells, {len(diffs)} different "
        f"({len(diffs)/total:.4%})" if total else f"FAIL  {len(diffs)} different")
    add("")
    add(f"  differing columns ({len(per_column)}):")
    for col, c in sorted(per_column.items(), key=lambda kv: -kv[1]):
        add(f"    {c:>6}  {col}")
    add("")
    shown = min(len(diffs), MAX_LISTED)
    add(f"  differences (showing {shown} of {len(diffs)}):")
    add(f"    {keyhdr:<45} {'column':<30} {name1:<18} {name2}")
    add(f"    {'-'*45} {'-'*30} {'-'*18} {'-'*18}")
    for kv, col, v1, v2 in diffs[:MAX_LISTED]:
        add(f"    {str(kv)[:45]:<45} {str(col)[:30]:<30} "
            f"{str(v1)[:18]:<18} {str(v2)[:18]}")
    if len(diffs) > MAX_LISTED:
        add(f"    ... {len(diffs)-MAX_LISTED} more (raise MAX_LISTED)")


if __name__ == "__main__":
    argv = sys.argv[1:]
    out, key, args, i = "comparison_report.txt", None, [], 0
    while i < len(argv):
        if argv[i] in ("-o", "--out") and i + 1 < len(argv):
            out = argv[i + 1]; i += 2
        elif argv[i] == "--key" and i + 1 < len(argv):
            key = [c.strip() for c in argv[i + 1].split(",")]; i += 2
        else:
            args.append(argv[i]); i += 1
    if len(args) != 2:
        print(__doc__); sys.exit(1)
    f1, f2 = args
    sys.exit(0 if compare(load(f1), load(f2), out=out,
                          name1=Path(f1).name, name2=Path(f2).name, key=key) else 1)

#!/usr/bin/env python3
"""
compare.py — check two tables hold the same players, write differences to a txt.

Rows are matched BY KEY, not by position, so it doesn't matter that the two
files are in a different order (e.g. one came from a market-value sort, the other
from an age-split export). Cells are only compared once rows are lined up by key.

Use as a script:
    python compare.py file1.csv file2.xlsx
    python compare.py file1.csv file2.xlsx -o report.txt
    python compare.py a.csv b.xlsx --key "Player,Team within selected timeframe,Age"

Or in code:
    from compare import compare
    compare(df1, df2)                                  # writes comparison_report.txt
    compare(df1, df2, key=["Player", "Team", "Age"])   # choose the key yourself

Checks, in order:
    1. row count equal
    2. column count equal
    3. column names equal (and in the same order)
    4. row identity — the key uniquely identifies every row, and both files
       hold the same set of keys. DUPLICATE KEYS ARE FLAGGED: if two rows share
       a key, they can't be matched 1-to-1, so pick a key that separates them.
    5. cell values — for each shared key, every other cell matches
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

MAX_LISTED = 500        # differences written out in full

# Columns used to identify a row when you don't pass --key. Tried in order;
# the first combination whose columns all exist is used. There is no numeric
# player id in the Wyscout export, so identity is a composite. Age is included
# because name+team is NOT unique — e.g. three different "M. Schulz" at Preußen
# Münster (ages 31, 30, 22) collide on name+team but not on name+team+age.
DEFAULT_KEYS = [
    ["Player", "Team within selected timeframe", "Age"],
    ["Player", "Team", "Age"],
    ["Player", "Age"],
    ["Player"],
]


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


def pick_key(df1, df2, key):
    """Choose the identifying columns. Explicit key wins; else first default
    whose columns exist in both frames."""
    if key:
        cols = [key] if isinstance(key, str) else list(key)
        missing = [c for c in cols if c not in df1.columns or c not in df2.columns]
        if missing:
            raise KeyError(f"key column(s) not in both files: {missing}")
        return cols
    for combo in DEFAULT_KEYS:
        if all(c in df1.columns and c in df2.columns for c in combo):
            return combo
    # last resort: first shared column
    shared = [c for c in df1.columns if c in set(df2.columns)]
    return shared[:1] or [df1.columns[0]]


def key_of(df, key):
    """One string per row joining the key columns, so it can be compared/hashed.

    Missing values become the literal '<NA>' rather than a real NaN — otherwise a
    blank team (pandas' nullable string dtype keeps it as NaN through astype(str))
    would poison the whole key and later break sorting on mixed str/float.
    """
    parts = [df[c].astype("string").fillna("<NA>").str.strip() for c in key]
    out = parts[0]
    for p in parts[1:]:
        out = out + " | " + p
    return out.astype(str).reset_index(drop=True)


def compare(df1, df2, out="comparison_report.txt", name1="df1", name2="df2", key=None):
    L = []
    add = L.append
    passed = {}

    df1 = df1.reset_index(drop=True)
    df2 = df2.reset_index(drop=True)

    add("=" * 70)
    add("COMPARISON REPORT")
    add("=" * 70)
    add(f"generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    add(f"{name1:9} : {len(df1)} rows x {len(df1.columns)} columns")
    add(f"{name2:9} : {len(df2)} rows x {len(df2.columns)} columns")
    add("")

    # ---------------------------------------------------------------- 1
    add("-" * 70); add("1. ROW COUNT"); add("-" * 70)
    passed[1] = len(df1) == len(df2)
    add(f"PASS  both have {len(df1)} rows" if passed[1] else
        f"FAIL  {name1} has {len(df1)}, {name2} has {len(df2)}"
        f"  (difference: {abs(len(df1) - len(df2))})")
    add("")

    # ---------------------------------------------------------------- 2
    add("-" * 70); add("2. COLUMN COUNT"); add("-" * 70)
    passed[2] = len(df1.columns) == len(df2.columns)
    add(f"PASS  both have {len(df1.columns)} columns" if passed[2] else
        f"FAIL  {name1} has {len(df1.columns)}, {name2} has {len(df2.columns)}")
    add("")

    # ---------------------------------------------------------------- 3
    add("-" * 70); add("3. COLUMN NAMES"); add("-" * 70)
    c1, c2 = list(df1.columns), list(df2.columns)
    only1c = [c for c in c1 if c not in set(c2)]
    only2c = [c for c in c2 if c not in set(c1)]
    order = [(i, a, b) for i, (a, b) in enumerate(zip(c1, c2)) if a != b]
    passed[3] = c1 == c2
    if passed[3]:
        add("PASS  identical names, identical order")
    else:
        add("FAIL")
        if only1c:
            add(f"  only in {name1} ({len(only1c)}):")
            for c in only1c: add(f"    {c!r}")
        if only2c:
            add(f"  only in {name2} ({len(only2c)}):")
            for c in only2c: add(f"    {c!r}")
        if not only1c and not only2c and order:
            add(f"  same names, different order — {len(order)} position(s):")
            for i, a, b in order: add(f"    position {i}: {name1}={a!r}  {name2}={b!r}")
    add("")

    # ---------------------------------------------------------------- 4
    add("-" * 70); add("4. ROW IDENTITY (matched by key)"); add("-" * 70)
    kcols = pick_key(df1, df2, key)
    add(f"key columns: {kcols}")
    if not key:
        add("  (auto-chosen; pass key=[...] or --key to override)")

    k1 = key_of(df1, kcols)
    k2 = key_of(df2, kcols)

    vc1, vc2 = k1.value_counts(), k2.value_counts()
    dup1 = vc1[vc1 > 1]
    dup2 = vc2[vc2 > 1]

    set1, set2 = set(k1), set(k2)
    only1 = sorted(set1 - set2)
    only2 = sorted(set2 - set1)

    passed[4] = dup1.empty and dup2.empty and not only1 and not only2

    if dup1.empty and dup2.empty:
        add("no duplicate keys — every row is uniquely identified")
    else:
        add("DUPLICATE KEYS FOUND — these rows cannot be matched one-to-one.")
        add("Add a column to the key that tells them apart, then re-run.")
        for nm, dup in ((name1, dup1), (name2, dup2)):
            if not dup.empty:
                add(f"  in {nm} ({len(dup)} key(s) repeated):")
                for kv, n in dup.items():
                    add(f"    {n}x  {kv}")
    add("")
    add(f"keys in both      : {len(set1 & set2)}")
    add(f"only in {name1:9} : {len(only1)}")
    for kv in only1[:MAX_LISTED]:
        add(f"    {kv}")
    if len(only1) > MAX_LISTED:
        add(f"    ... {len(only1) - MAX_LISTED} more")
    add(f"only in {name2:9} : {len(only2)}")
    for kv in only2[:MAX_LISTED]:
        add(f"    {kv}")
    if len(only2) > MAX_LISTED:
        add(f"    ... {len(only2) - MAX_LISTED} more")
    add("")

    # ---------------------------------------------------------------- 5
    add("-" * 70); add("5. CELL VALUES (aligned by key)"); add("-" * 70)

    dupset = set(dup1.index) | set(dup2.index)
    shared_cols = [c for c in c1 if c in set(c2)]
    compare_cols = [c for c in shared_cols if c not in kcols]  # key cols match by definition
    common_keys = sorted((set1 & set2) - dupset)

    if not compare_cols:
        add("SKIPPED  no non-key columns in common")
        passed[5] = False
    elif not common_keys:
        add("SKIPPED  no shared keys to compare")
        passed[5] = False
    else:
        if dupset:
            add(f"note: {len(dupset)} duplicated key(s) skipped here (see check 4)")
        if len(compare_cols) != len(shared_cols):
            add(f"note: comparing {len(compare_cols)} non-key shared columns")

        # index each frame by key for O(1) row lookup
        idx1 = {kv: i for i, kv in enumerate(k1) if kv not in dupset}
        idx2 = {kv: i for i, kv in enumerate(k2) if kv not in dupset}

        per_column = {}
        diffs = []
        for kv in common_keys:
            r1 = df1.iloc[idx1[kv]]
            r2 = df2.iloc[idx2[kv]]
            for col in compare_cols:
                if not same(r1[col], r2[col]):
                    per_column[col] = per_column.get(col, 0) + 1
                    diffs.append((kv, col, r1[col], r2[col]))

        total = len(common_keys) * len(compare_cols)
        passed[5] = not diffs
        add("")
        if passed[5]:
            add(f"PASS  {len(common_keys)} matched rows, {total} cells, 0 different")
        else:
            add(f"FAIL  {len(common_keys)} matched rows, {total} cells, "
                f"{len(diffs)} different ({len(diffs)/total:.4%})")
            add("")
            add(f"  differing columns ({len(per_column)}):")
            for col, n in sorted(per_column.items(), key=lambda kv: -kv[1]):
                add(f"    {n:>6}  {col}")
            add("")
            shown = min(len(diffs), MAX_LISTED)
            add(f"  differences (showing {shown} of {len(diffs)}):")
            add(f"    {'key':<45} {'column':<34} {name1:<20} {name2}")
            add(f"    {'-'*45} {'-'*34} {'-'*20} {'-'*20}")
            for kv, col, v1, v2 in diffs[:MAX_LISTED]:
                add(f"    {str(kv)[:45]:<45} {str(col)[:34]:<34} "
                    f"{str(v1)[:20]:<20} {str(v2)[:20]}")
            if len(diffs) > MAX_LISTED:
                add(f"    ... {len(diffs) - MAX_LISTED} more (raise MAX_LISTED)")
    add("")

    # ---------------------------------------------------------------- summary
    add("=" * 70); add("SUMMARY"); add("=" * 70)
    labels = {1: "row count", 2: "column count", 3: "column names",
              4: "row identity", 5: "cell values"}
    for k in range(1, 6):
        add(f"  {k}. {labels[k]:<15} {'PASS' if passed[k] else 'FAIL'}")
    add("")
    ok = all(passed.values())
    add("RESULT: IDENTICAL" if ok else "RESULT: DIFFERENCES FOUND")
    add("=" * 70)

    text = "\n".join(L)
    Path(out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten to {Path(out).resolve()}")
    return ok


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

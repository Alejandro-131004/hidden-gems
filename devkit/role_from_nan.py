"""
Derive role buckets from missing-value structure instead of a hand table.

Rationale
---------
If two positions really belong to the same role, Wyscout collects the same
stat set for them, so the columns that are "structurally empty" (all-NaN) for
one should be structurally empty for the other too. We therefore describe each
position by its NaN profile -- the per-column share of missing values -- and
group positions whose profiles are close. This gives the lowest-difference
grouping the data actually supports, rather than one imposed by hand.

Pipeline (per dataframe, and once on all frames pooled):
    1. nan_profiles      position -> vector of per-column NaN%
    2. profile_distance  pairwise distance between those vectors
    3. cluster_positions hierarchical clustering, cut at the largest gap
    4. compare_to_role   line up the derived groups against a reference map

Everything is a plain function; nothing is read from globals except the
optional `ROLE`/`EXCLUDE` defaults, which you can override per call.

    from role_from_nan import derive_groups, derive_groups_global

    per_season = {yr: derive_groups(df, name=yr) for yr, df in dfs.items()}
    glob       = derive_groups_global(dfs)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster, dendrogram
from scipy.spatial.distance import squareform

__all__ = [
    "nan_profiles", "profile_distance", "cluster_positions", "merge_ladder",
    "derive_groups", "derive_groups_global", "compare_to_role",
    "role_bucket_spread", "EXCLUDE",
]

EXCLUDE = ["Player", "Team", "Position", "Age", "Market value",
           "Contract expires", "Birth country", "Passport country", "Foot"]


# --------------------------------------------------------------------------
# 1. per-position NaN profiles
# --------------------------------------------------------------------------
def _primary(series):
    """First comma-separated position code, upper-cased and stripped."""
    return (series.fillna("").astype(str)
            .str.split(",").str[0].str.strip().str.upper())


def nan_profiles(df, exclude=None, position_col="Position", min_rows=1):
    """
    Return (profiles, counts, feature_cols).

    profiles : DataFrame, index = primary position code, columns = feature
               columns, values = NaN percentage (0-100) for that position.
    counts   : Series, rows behind each position.
    Positions with fewer than `min_rows` rows are dropped from `profiles`
    (too noisy to place) but still appear in `counts`.
    """
    exclude = EXCLUDE if exclude is None else exclude
    feats = [c for c in df.columns if c not in exclude]
    code = _primary(df[position_col])

    profiles, counts = {}, {}
    for pos, sub in df.groupby(code):
        if pos == "":
            continue
        counts[pos] = len(sub)
        if len(sub) >= min_rows:
            profiles[pos] = sub[feats].isna().mean() * 100.0

    prof = pd.DataFrame(profiles).T.reindex(columns=feats)
    n = pd.Series(counts, name="n").sort_values(ascending=False)
    return prof, n, feats


# --------------------------------------------------------------------------
# 2. distance between profiles
# --------------------------------------------------------------------------
def profile_distance(profiles, metric="mad", tol=1.0):
    """
    Pairwise distance between position NaN profiles.

    metric:
      "mad"      mean absolute difference in NaN% across columns (percentage
                 points). The continuous generalisation of the original
                 per-column delta; recommended default.
      "max"      largest single-column NaN% difference.
      "cols_off" count of columns whose NaN% differs by more than `tol`
                 (the original "columns off" measure, as a distance).
    """
    pos = list(profiles.index)
    M = profiles.to_numpy(dtype=float)
    k = len(pos)
    D = np.zeros((k, k))
    for i in range(k):
        for j in range(i + 1, k):
            diff = np.abs(M[i] - M[j])
            if metric == "mad":
                d = np.nanmean(diff)
            elif metric == "max":
                d = np.nanmax(diff)
            elif metric == "cols_off":
                d = float(np.nansum(diff > tol))
            else:
                raise ValueError(f"unknown metric {metric!r}")
            D[i, j] = D[j, i] = d
    return pd.DataFrame(D, index=pos, columns=pos)


# --------------------------------------------------------------------------
# 3. clustering
# --------------------------------------------------------------------------
def _auto_threshold(Z):
    """
    Cut at the "tightness cliff": the largest *relative* jump between
    successive merge heights. Positions inside a true role merge at tiny
    heights (sampling noise); the first cross-role merge is much larger, so
    the biggest ratio marks where tight groups end. This targets low
    within-group difference, unlike the largest *absolute* gap, which sits at
    the top of the tree and yields only two groups.
    """
    h = np.sort(Z[:, 2])
    if len(h) < 2:
        return (h[-1] + 1.0) if len(h) else 1.0
    eps = 1e-6
    hh = np.maximum(h, eps)
    ratios = hh[1:] / hh[:-1]
    i = int(np.argmax(ratios))
    return float(np.sqrt(hh[i] * hh[i + 1]))          # geometric midpoint


def merge_ladder(Z, labels_order):
    """DataFrame: cut just above each merge height -> resulting #clusters."""
    h = np.sort(Z[:, 2])
    n_leaves = len(labels_order)
    rows = [{"cut_height": float(hi), "n_clusters": n_leaves - (k + 1)}
            for k, hi in enumerate(h)]
    return pd.DataFrame(rows)


def cluster_positions(dist, threshold=None, n_clusters=None, method="complete"):
    """
    Hierarchical clustering of positions from a distance matrix.

    method     : linkage. "complete" bounds the *maximum* difference inside a
                 group (tightest, recommended). "average" is smoother.
    n_clusters : if given, force this many groups (overrides threshold).
    threshold  : cut height. None -> auto at the tightness cliff.
    Returns (groups, meta) where
      groups : dict {group_id -> [positions]}, group_id in size order
      meta   : dict with the linkage matrix Z, threshold, order, ladder.
    """
    pos = list(dist.index)
    if len(pos) == 1:
        return {1: pos}, {"Z": None, "threshold": 0.0, "order": pos,
                          "ladder": None}

    condensed = squareform(dist.to_numpy(dtype=float), checks=False)
    Z = linkage(condensed, method=method)

    if n_clusters is not None:
        labels = fcluster(Z, t=n_clusters, criterion="maxclust")
        t = None
    else:
        t = _auto_threshold(Z) if threshold is None else threshold
        labels = fcluster(Z, t=t, criterion="distance")

    raw = {}
    for p, lab in zip(pos, labels):
        raw.setdefault(int(lab), []).append(p)
    # relabel groups by size (largest first) for stable, readable ids
    ordered = sorted(raw.values(), key=lambda g: (-len(g), g))
    groups = {i + 1: g for i, g in enumerate(ordered)}
    return groups, {"Z": Z, "threshold": t, "order": pos,
                    "ladder": merge_ladder(Z, pos)}


# --------------------------------------------------------------------------
# comparison against a reference map (e.g. the hand-built ROLE)
# --------------------------------------------------------------------------
def compare_to_role(groups, role):
    """
    For each derived group, show which reference buckets its members came from.
    A group that mixes buckets = ROLE was too strict (it split positions the
    data says belong together). A reference bucket spread across groups = ROLE
    was too loose.
    """
    rows = []
    for gid, members in groups.items():
        buckets = {}
        for p in members:
            b = role.get(p, "<unmapped>")
            buckets.setdefault(b, []).append(p)
        rows.append({
            "group": gid,
            "positions": ", ".join(members),
            "role_buckets": ", ".join(sorted(buckets)),
            "mixes_buckets": len(buckets) > 1,
        })
    return pd.DataFrame(rows)


def role_bucket_spread(groups, role):
    """
    The other direction: for each reference bucket, how many derived groups its
    positions land in. n_groups > 1 means the data splits a bucket ROLE keeps
    whole (ROLE too *loose* there). Complements compare_to_role, which catches
    groups that merge buckets (ROLE too *strict* there).
    """
    pos2grp = {p: gid for gid, ms in groups.items() for p in ms}
    buckets = {}
    for p, gid in pos2grp.items():
        b = role.get(p, "<unmapped>")
        buckets.setdefault(b, {}).setdefault(gid, []).append(p)

    rows = []
    for b, grps in buckets.items():
        detail = "; ".join(f"g{g}:{','.join(ps)}"
                           for g, ps in sorted(grps.items()))
        rows.append({"role_bucket": b, "n_groups": len(grps),
                     "split": len(grps) > 1, "detail": detail})
    return (pd.DataFrame(rows)
            .sort_values(["split", "n_groups"], ascending=[False, False])
            .reset_index(drop=True))


# --------------------------------------------------------------------------
# convenience wrappers
# --------------------------------------------------------------------------
def _print_groups(name, groups, counts, threshold):
    cut = "forced k" if threshold is None else f"cut @ {threshold:.2f}pp"
    print(f"\n{'=' * 70}\n{name}   ({len(groups)} groups, {cut})\n{'=' * 70}")
    for gid, members in groups.items():
        tagged = ", ".join(f"{p}(n={int(counts.get(p, 0))})" for p in members)
        print(f"  group {gid}: {tagged}")


def derive_groups(df, name="frame", exclude=None, position_col="Position",
                  metric="mad", tol=1.0, min_rows=20, threshold=None,
                  method="complete", role=None, verbose=False):
    """
    Full pipeline for one dataframe. Returns a dict with keys:
      profiles, counts, distance, groups, meta, comparison(if role given).
    """
    prof, n, feats = nan_profiles(df, exclude, position_col, min_rows)
    dist = profile_distance(prof, metric=metric, tol=tol)
    groups, meta = cluster_positions(dist, threshold=threshold, method=method)

    result = {"profiles": prof, "counts": n, "distance": dist,
              "groups": groups, "meta": meta}
    if role is not None:
        result["comparison"] = compare_to_role(groups, role)
        result["spread"] = role_bucket_spread(groups, role)

    if verbose:
        _print_groups(name, groups, n, meta["threshold"])
        dropped = [p for p in n.index if p not in prof.index]
        if dropped:
            print(f"  (dropped, < {min_rows} rows: "
                  f"{', '.join(f'{p}({int(n[p])})' for p in dropped)})")
        if role is not None:
            mixed = result["comparison"].query("mixes_buckets")
            if len(mixed):
                print("  ROLE too strict (data merges different buckets):")
                for _, r in mixed.iterrows():
                    print(f"    [{r.role_buckets}] -> {r.positions}")
            split = result["spread"].query("split")
            if len(split):
                print("  ROLE too loose (data splits one bucket):")
                for _, r in split.iterrows():
                    print(f"    {r.role_bucket} -> {r.detail}")
            if not len(mixed) and not len(split):
                print("  derived groups match ROLE exactly")
    return result


def derive_groups_global(dfs, exclude=None, position_col="Position",
                         metric="mad", tol=1.0, min_rows=20, threshold=None,
                         method="complete", role=None, verbose=1,
                         how="pool"):
    """
    One grouping across all frames.

    how="pool"   concatenate all frames on their shared feature columns and
                 profile the pooled sample (larger n per position). Default.
    how="mean"   average the per-frame distance matrices over positions that
                 appear in each frame (equal weight per season, robust to a
                 single season's quirks).
    """
    if how == "pool":
        shared = set.intersection(
            *[{c for c in df.columns if c not in (exclude or EXCLUDE)}
              for df in dfs.values()])
        keep_meta = [c for c in (exclude or EXCLUDE)]
        frames = []
        for df in dfs.values():
            cols = [c for c in df.columns if c in shared or c in keep_meta]
            frames.append(df[cols])
        pooled = pd.concat(frames, ignore_index=True)
        return derive_groups(pooled, name="GLOBAL (pooled)", exclude=exclude,
                             position_col=position_col, metric=metric, tol=tol,
                             min_rows=min_rows, threshold=threshold,
                             method=method, role=role, verbose=verbose)

    elif how == "mean":
        mats, all_pos = [], set()
        for df in dfs.values():
            prof, _, _ = nan_profiles(df, exclude, position_col, min_rows)
            d = profile_distance(prof, metric=metric, tol=tol)
            mats.append(d)
            all_pos |= set(d.index)
        all_pos = sorted(all_pos)
        stack = np.full((len(mats), len(all_pos), len(all_pos)), np.nan)
        for k, d in enumerate(mats):
            idx = [all_pos.index(p) for p in d.index]
            stack[np.ix_([k], idx, idx)] = d.to_numpy()
        avg = np.nanmean(stack, axis=0)
        avg[np.isnan(avg)] = np.nanmax(avg) * 1.5  # never-co-seen -> far apart
        np.fill_diagonal(avg, 0.0)
        dist = pd.DataFrame(avg, index=all_pos, columns=all_pos)
        groups, meta = cluster_positions(dist, threshold=threshold,
                                         method=method)
        result = {"distance": dist, "groups": groups, "meta": meta}
        if role is not None:
            result["comparison"] = compare_to_role(groups, role)
        if verbose:
            counts = pd.Series(dtype=int)
            _print_groups("GLOBAL (mean of seasons)", groups, counts,
                          meta["threshold"])
        return result

    raise ValueError("how must be 'pool' or 'mean'")


# --------------------------------------------------------------------------
# optional: dendrograms for a set of results
# --------------------------------------------------------------------------
def plot_dendrograms(results, global_result=None, figsize=(11, 3.0)):
    """
    results : dict {name -> result dict from derive_groups}
    Draw one dendrogram per frame plus (optionally) the global one.
    """
    import matplotlib.pyplot as plt

    items = list(results.items())
    if global_result is not None:
        items.append(("GLOBAL", global_result))
    n = len(items)
    fig, axes = plt.subplots(n, 1, figsize=(figsize[0], figsize[1] * n),
                             squeeze=False)
    for ax, (name, res) in zip(axes[:, 0], items):
        Z = res["meta"]["Z"]
        if Z is None:
            ax.set_axis_off()
            ax.set_title(f"{name} — single position")
            continue
        dendrogram(Z, labels=list(res["meta"]["order"]),
                   color_threshold=res["meta"]["threshold"], ax=ax,
                   leaf_font_size=8)
        ax.axhline(res["meta"]["threshold"], color="0.6", ls="--", lw=0.8)
        ax.set_title(f"{name} — cut @ {res['meta']['threshold']:.2f}",
                     fontsize=10)
        ax.set_ylabel("profile distance")
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig, axes

def checkNansRoles(dfs, PRIMARY_ONLY, TOL, MIN_ROWS, EXCLUDE):
    

    pos_col = "Position"
    PRIMARY_ONLY = True      # "RCB, RB" -> RCB only. False = row counts for both.
    TOL = 1.0                # percentage points
    MIN_ROWS = 30            # below this a NaN% is too noisy to trust
    EXCLUDE = ["Player", "Team", "Position", "Age", "Market value",
            "Contract expires", "Birth country", "Passport country", "Foot"]


    # --------------------------------------------------------------------------
    # helpers
    # --------------------------------------------------------------------------
    def position_mask(series, code):
        """Rows whose position is `code`."""
        s = series.fillna("").astype(str)
        if PRIMARY_ONLY:
            return s.str.split(",").str[0].str.strip().str.upper() == code
        return s.str.upper().str.split(",").apply(
            lambda parts: code in [p.strip() for p in parts]
        )


    def nan_profile(frame, cols):
        """Per-column NaN percentage."""
        return frame[cols].isna().mean() * 100


    def feature_columns(frame):
        return [c for c in frame.columns if c not in EXCLUDE]


    # --------------------------------------------------------------------------
    # 3. the check
    # --------------------------------------------------------------------------
    records = []

    for season, df in dfs.items():
        print(f"\n{'=' * 70}\nSEASON {season}   ({len(df)} rows)\n{'=' * 70}")

        cols = feature_columns(df)
        masks = {}
        for role, positions in ROLES.items():
            for pos in positions:
                m = position_mask(df[pos_col], pos)
                if m.sum():
                    masks[pos] = m

        missing = [p for ps in ROLES.values() for p in ps if p not in masks]
        if missing:
            print(f"  [!] not present in this season: {', '.join(missing)}")

        for role, positions in ROLES.items():
            present = [p for p in positions if p in masks]
            if len(present) < 2:
                continue  # single-position role, nothing to compare against

            lead = LEADS.get(role)
            if lead not in present:
                lead = max(present, key=lambda p: masks[p].sum())
                note = " (fallback: largest sample)"
            else:
                note = ""

            lead_n = int(masks[lead].sum())
            lead_profile = nan_profile(df[masks[lead]], cols)
            print(f"\n  role {role}  lead = {lead}, n={lead_n}{note}")

            if lead_n < MIN_ROWS:
                print(f"    [!] lead has only {lead_n} rows — treat results as noisy")

            for pos in present:
                if pos == lead:
                    continue

                n = int(masks[pos].sum())
                profile = nan_profile(df[masks[pos]], cols)
                delta = (profile - lead_profile).abs()
                off = delta[delta > TOL].sort_values(ascending=False)

                for col, d in off.items():
                    records.append({
                        "season": season, "role": role, "position": pos,
                        "lead": lead, "column": col, "delta_pp": d,
                        "nan_pos": profile[col], "nan_lead": lead_profile[col],
                        "n": n,
                    })

                if len(off) == 0:
                    print(f"    {pos:<5} n={n:<5} consistent")
                    continue

                flag = "  [low n]" if n < MIN_ROWS else ""
                print(
                    f"    position {pos} might not belong to role {role} because "
                    f"it shows {len(off)} columns, {', '.join(off.index)} "
                    f"different from the lead position{flag}"
                )

    # --------------------------------------------------------------------------
    # 4. summary across seasons
    # --------------------------------------------------------------------------
    report = pd.DataFrame(records)

    if report.empty:
        print("\nNo mismatches above tolerance. The table holds.")
    else:
        print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")

        per_pair = (report.groupby(["role", "position", "column"])
                        .agg(seasons=("season", "nunique"),
                            mean_delta=("delta_pp", "mean"),
                            max_delta=("delta_pp", "max"))
                        .reset_index()
                        .sort_values(["seasons", "mean_delta"],
                                    ascending=[False, False]))

        # A column that drifts in a single season is probably a collection
        # artefact; one that drifts in every season is a real structural split.
        persistent = per_pair[per_pair["seasons"] == len(dfs)]
        print(f"\nDrifting in all {len(dfs)} seasons (structural, worth acting on):")
        print(persistent.to_string(index=False) if len(persistent)
            else "  none")

        print("\nMismatching columns per position (all seasons pooled):")
        print(report.groupby(["role", "position"])["column"]
                    .nunique().sort_values(ascending=False).to_string())

    report.to_csv("role_grouping_mismatches.csv", index=False)
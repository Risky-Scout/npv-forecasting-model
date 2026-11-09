"""
03b_assumption_governance.py
-------------------------------------------------------------------------------
Links the experience studies to the NPV assumptions, and controls which
assumption set the engine may run on. Runs after the studies (01-03) and
before the NPV engine (04).

1. Assumption indications (output/assumptions/assumption_indications.csv)
   For every assumption the experience can inform, the indicated value next to
   the assumption in use, with the credibility basis:
     * Lapse by duration, shock lapse: limited-fluctuation credibility,
       Z = min(1, sqrt(n / 1082)) with n = observed lapses (full credibility
       1,082 events: 90% probability of being within 5%). Indicated =
       Z x experience + (1 - Z) x current assumption.
     * Claim payment pattern: developed share paid at each lag, from incurred
       months with at least 12 months of run-out; also a tail test (share paid
       beyond the last lag in the pattern), which replaces the old "tail = 1.0"
       limitation with a measured check.
     * Medicaid rate cells: member mix and capitation relativity from exposure
       (fully credible); medical-cost and churn relativities are moved off 1.0
       only when the cell differs from the blend by more than 2 standard errors.
     * Trend (v4): credibility blend of the normalized fit with an external
       benchmark, Z = sd_b^2 / (sd_b^2 + se^2) with se the member-level bootstrap
       standard error (config/trend_benchmarks.json). The v3 basis for never
       blending (four years pin trend only to +/-2 points) was withdrawn: the
       v4 seed study measured the sampling SD at 0.5-1.05 points.
   A difference beyond tolerance is flagged REVIEW. Indications never change the
   assumption file by themselves: they go to the assumption committee, and a
   proposed file (output/assumptions/proposed_npv_assumptions.json) is written
   for that review.

2. Approval gate (config/assumption_approvals.json)
   The SHA-256 of the assumption file in use plus the governed configuration
   files (claim-cost curves, trend benchmarks, forecast settings; canonical JSON)
   must match an approval record for the same model version. In production mode an
   unapproved set stops the run (exit 1); in synthetic mode it is a warning.
   Any edit to the assumptions, however small, changes the hash and needs a new
   approval: that is the control (ASOP 56 governance; model risk policy).

Usage:  python 03b_assumption_governance.py           (gate + indications)
        python 03b_assumption_governance.py --hash    (print the hash only)
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Forecast bias review trigger (same 5% threshold as 02's flag).
BIAS_TOL = 0.05
# RUN_CONFIG and ASSUMPTION_OUT let tests exercise the gate (e.g. production mode on an edited set)
# without touching the run's own configuration or outputs.
OUT = Path(os.environ.get("ASSUMPTION_OUT", OUTPUT_DIR / "assumptions"))
import assumption_control as _ac  # noqa: E402
RUN = _ac.run_config()
ASSUMPTION_FILE = _ac.assumption_file()          # the same resolver the engine, workbook and notebook use
APPROVALS = _ac.approvals_file()
FULL_CRED = 1082
LAGS = 9


from assumption_control import approval_status, canonical_hash, approval_hash  # noqa: E402,F401  (shared with the engine)


def z_cred(n: float) -> float:
    return float(min(1.0, np.sqrt(max(n, 0.0) / FULL_CRED)))


def row(lob, assumption, current, experience, n, z, indicated, tol, basis, note=""):
    diff = None if (indicated is None or current is None) else float(indicated) - float(current)
    status = "INFO" if tol is None else ("REVIEW" if diff is not None and abs(diff) > tol else "OK")
    return dict(lob=lob, assumption=assumption, current=current, experience=experience, credibility_n=n,
                credibility_z=z, indicated=indicated, difference=diff, tolerance=tol, status=status, basis=basis, note=note)


# Pooling points match the silver layer (SQL 02): one catastrophic claim must not set the payment
# pattern for the whole line (a single $1.85M Medicaid claim paid at lag 0 moves the dollar-weighted
# lag-0 share by 2 points if left unpooled).
POOL = json.loads((ROOT / "config" / "experience_parameters.json").read_text())["pooling_points"]


def payment_pattern(claims: pd.DataFrame, lob: str, paid_through: str):
    """Developed share of pooled paid claims at lags 0..LAGS-1 and the tail beyond, from mature months."""
    c = claims[claims.lob == lob].copy()
    c["paid_amount"] = c["paid_amount"].clip(upper=POOL[lob])
    pt = pd.Period(paid_through, freq="M")
    inc = pd.PeriodIndex(c["incurred_month"], freq="M"); pdm = pd.PeriodIndex(c["paid_month"], freq="M")
    c["lag"] = (pdm.year - inc.year) * 12 + (pdm.month - inc.month)
    mature = (pt.year - inc.year) * 12 + (pt.month - inc.month) >= 12
    c = c[mature]
    tot = c["paid_amount"].sum()
    by = c.groupby(np.minimum(c["lag"], LAGS))["paid_amount"].sum().reindex(range(LAGS + 1), fill_value=0.0) / tot
    return by.values[:LAGS], float(by.values[LAGS]), int(len(c))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    A = json.loads(ASSUMPTION_FILE.read_text())
    if "--hash" in sys.argv:
        print(approval_hash(A)); return 0
    approvals = json.loads(APPROVALS.read_text())["approvals"] if APPROVALS.exists() else []
    status = approval_status(A, approvals)
    mode = RUN.get("mode", "synthetic")

    rows = []
    proposed = copy.deepcopy(A)
    # ---------------- lapse and shock lapse (GLM study + persistency study)
    lap = pd.read_csv(OUTPUT_DIR / "lapse" / "lapse_assumption_table.csv", dtype={"duration_year": str})
    per = pd.read_csv(OUTPUT_DIR / "sql_results" / "04_lapse_persistency_study.csv", dtype={"duration_year": str})
    for lob in ["MEDSUPP", "DENTAL_VISION"]:
        ind = []
        for i, d in enumerate(["1", "2", "3+"]):
            exp = float(lap[(lap.lob == lob) & (lap.duration_year == d)]["fitted_annual_lapse_excl_jan_shock"].iloc[0])
            n = float(per[(per.lob == lob) & (per.duration_year == d)]["lapses"].iloc[0])
            cur = A[lob]["lapse_by_duration"][i]; z = z_cred(n)
            v = z * exp + (1 - z) * cur; ind.append(round(v, 4))
            rows.append(row(lob, f"lapse_by_duration[{d}]", cur, exp, n, z, v, max(0.005, 0.05 * cur),
                            "Poisson GLM, non-January months; limited-fluctuation credibility on lapse count"))
        proposed[lob]["lapse_by_duration"] = ind
    exc = float(per[per.lob == "MEDSUPP"]["january_excess_monthly_lapse"].iloc[0])
    mm = pd.read_csv(DATA_DIR / "member_months.csv", usecols=["member_id", "lob", "month"], dtype={"month": str})
    mem = pd.read_csv(DATA_DIR / "members.csv", usecols=["member_id", "issue_month"], dtype={"issue_month": str})
    ms = mm[(mm.lob == "MEDSUPP") & mm.month.str.endswith("-01")].drop_duplicates(["member_id", "month"]).merge(mem, on="member_id")
    dur = (pd.PeriodIndex(ms.month, freq="M").year - pd.PeriodIndex(ms.issue_month, freq="M").year) * 12 + \
          (pd.PeriodIndex(ms.month, freq="M").month - pd.PeriodIndex(ms.issue_month, freq="M").month)
    n_shock = exc * float((dur >= 12).sum())
    cur = A["MEDSUPP"]["shock_lapse_add_from_year2"]; z = z_cred(n_shock)
    v = z * exc + (1 - z) * cur
    rows.append(row("MEDSUPP", "shock_lapse_add_from_year2", cur, exc, round(n_shock), z, v, 0.005,
                    "January excess lapse over the base monthly rate (renewal durations)"))
    proposed["MEDSUPP"]["shock_lapse_add_from_year2"] = round(v, 4)
    rows.append(row("MEDSUPP", "antiselection.shopper_relative_claim_cost", A["MEDSUPP"]["antiselection"]["shopper_relative_claim_cost"],
                    None, None, None, None, None, "Judgment",
                    "Not measurable on this book. Production: claims A/E of the block by months since rate increase, split by whether members shopped (quoted elsewhere)."))

    # ---------------- trend: v4 credibility blend of the fit with an external benchmark
    # v3 never blended trend, on the basis that four years pin trend only to about +/-2 points.
    # The v4 seed study (docs/validation/trend_estimator_study.md) measured the actual sampling SD
    # of the normalized fits at roughly 0.4-1.0 points, so that basis no longer holds. v4 weights
    # the fit by its precision: Z = sd_b^2 / (sd_b^2 + se^2), se = member-level bootstrap SE (02),
    # sd_b = benchmark uncertainty (config/trend_benchmarks.json). Indicated = Z fit + (1-Z) bench,
    # rounded. A gap beyond tolerance between the assumption and the indication is REVIEW.
    ft = pd.read_csv(OUTPUT_DIR / "trend" / "fitted_trends.csv").set_index("lob")
    TB = json.loads((ROOT / "config" / "trend_benchmarks.json").read_text())
    TREND_TOL, RND = TB["tolerance"], TB["rounding"]
    basis = {"MEDSUPP": "Log-linear fit on completed PMPM normalized for age, selection and plan (config/claim_cost_curves.json)",
             "DENTAL_VISION": "Log-linear fit on completed PMPM normalized for year-1 pent-up and product (v4)",
             "MEDICAID": "Log-linear fit on completed PMPM (existing book)"}
    for lob in ["MEDSUPP", "DENTAL_VISION", "MEDICAID"]:
        b = TB[lob]; key = b["series"]
        cur = A[lob]["claim_trend"]; fit = float(ft.loc[key, "fitted_annual_trend"])
        # v4.1: the member bootstrap understated the true sampling SD by up to ~14% (seed study,
        # Med Supp ratio 0.86), so it is inflated by the configured factor before weighting.
        se = float(ft.loc[key, "trend_se_bootstrap"]) * TB.get("bootstrap_se_inflation", 1.0)
        z = b["benchmark_sd"] ** 2 / (b["benchmark_sd"] ** 2 + se ** 2)
        ind_v = round((z * fit + (1 - z) * b["benchmark"]) / RND) * RND
        r_ = row(lob, "claim_trend", cur, fit, None, z, ind_v, TREND_TOL, basis[lob],
                 f"Credibility blend: Z = {z:.2f} on the fit (bootstrap SE {se:.2%}) and {1 - z:.2f} on the benchmark "
                 f"{b['benchmark']:.2%} (sd {b['benchmark_sd']:.2%}; {b['source'].split('.')[0]}).")
        r_["status"] = "REVIEW" if abs(ind_v - cur) > TREND_TOL + 1e-12 else "OK"
        if lob == "MEDICAID":
            r_["status"] = "INFO"
            r_["note"] = ("Existing book, not the new-state contract; contract trend comes from the state rate certification. "
                          + r_["note"])
        else:
            proposed[lob]["claim_trend"] = ind_v
        rows.append(r_)
    # v4: forecast bias diagnostics from the rolling-origin study (02). A PERSISTENT bias in the
    # selected forecasting model is a REVIEW item; a one-year holdout bias is reported as INFO.
    bd = pd.read_csv(OUTPUT_DIR / "trend" / "forecast_bias_diagnostics.csv")
    for r in bd.itertuples():
        if r.lob.endswith("_NORMALIZED"):
            continue
        rr = row(r.lob, "forecast_bias (rolling origins)", 0.0, r.rolling_mean_bias, r.origins, None, None, BIAS_TOL,
                 f"{r.selected_model}: mean bias over {r.origins} forecast origins (SE {r.rolling_se_bias:.1%}); last holdout {r.last_holdout_bias:+.1%}",
                 r.verdict)
        rr["status"] = "REVIEW" if bool(r.persistent_bias) else ("INFO" if abs(r.last_holdout_bias) > BIAS_TOL else "OK")
        rows.append(rr)

    # ---------------- v4 Med Supp new-business model-point mix (issues in the last two experience years)
    mp = A["MEDSUPP"].get("model_points")
    if mp:
        memm = pd.read_csv(DATA_DIR / "members.csv", dtype={"issue_month": str})
        y0 = str(int(RUN["experience_end"][:4]) - 1) + "-01"
        rec = memm[(memm.lob == "MEDSUPP") & (memm.issue_month >= y0) & (memm.issue_month <= RUN["experience_end"])]
        rec = rec[rec.plan.isin(list(mp["plans"]))]          # plans not offered to new business are excluded (Plan F)
        edges = {"65": (65, 65), "66-69": (66, 69), "70-74": (70, 74), "75+": (75, 200)}
        for bnd in mp["bands"]:
            lo_, hi_ = edges[bnd["band"]]
            inb = rec[(rec.issue_age >= lo_) & (rec.issue_age <= hi_)]
            for plan in mp["plans"]:
                e_ = float(((inb.plan == plan).sum()) / len(rec))
                rows.append(row("MEDSUPP", f"model_points.mix[{bnd['band']}|{plan}]", mp["mix"][bnd["band"]].get(plan, 0.0), e_,
                                int((inb.plan == plan).sum()), 1.0, e_, 0.005, f"Share of Med Supp issues {y0[:4]}-{RUN['experience_end'][:4]}, Plan F excluded"))
            rows.append(row("MEDSUPP", f"model_points.issue_age[{bnd['band']}]", bnd["issue_age"], float(inb.issue_age.mean()), int(len(inb)), 1.0,
                            float(inb.issue_age.mean()), 0.25, "Mean issue age in the band"))

    # ---------------- claim payment pattern and tail
    claims = pd.read_csv(DATA_DIR / "claims.csv", usecols=["lob", "incurred_month", "paid_month", "paid_amount"],
                         dtype={"incurred_month": str, "paid_month": str})
    for lob in ["MEDSUPP", "DENTAL_VISION", "MEDICAID"]:
        pat, tail, n = payment_pattern(claims, lob, RUN["paid_through"])
        cur = A[lob]["claim_payment_pattern"]
        exp_pat = pat / pat.sum()
        mx = float(np.max(np.abs(exp_pat - np.array(cur))))
        rows.append(row(lob, "claim_payment_pattern (max lag difference)", 0.0, mx, n, z_cred(n), mx, 0.01,
                        "Developed share of pooled paid claims by lag, incurred months with >= 12 months run-out",
                        "experience: " + ", ".join(f"{x:.3f}" for x in exp_pat)))
        rows.append(row(lob, f"tail beyond lag {LAGS - 1} (share of paid)", 0.0, tail, n, None, tail, 0.001,
                        "Share of mature claims paid after the last lag in the pattern",
                        "Above tolerance: lengthen the pattern or add a tail factor."))
        # Pooling charge: expected claims above the pooling point, as a share of pooled claims. The
        # experience (PMPMs, trend, patterns) is on pooled claims; an all-in claim cost set from it
        # must add this back. The NPV claim-cost assumptions in this kit are all-in.
        cl_lob = claims[(claims.lob == lob) & (claims.incurred_month >= RUN["experience_start"]) & (claims.incurred_month <= RUN["experience_end"])]
        excess = float((cl_lob["paid_amount"] - cl_lob["paid_amount"].clip(upper=POOL[lob])).clip(lower=0).sum())
        pooled = float(cl_lob["paid_amount"].clip(upper=POOL[lob]).sum())
        rows.append(row(lob, f"pooling charge (excess over ${POOL[lob]:,.0f} / pooled claims)", None, excess / pooled, int((cl_lob["paid_amount"] > POOL[lob]).sum()),
                        None, None, None, "Experience period, all incurred months",
                        "Add to a claim cost calibrated from pooled experience; a single year's excess is volatile, so production uses a longer, larger base."))
        p_ = np.round(exp_pat, 4); p_[0] += 1 - p_.sum()
        proposed[lob]["claim_payment_pattern"] = [float(x) for x in p_]

    # ---------------- Medicaid rate cells
    mmc = pd.read_csv(DATA_DIR / "member_months.csv", dtype={"rate_cell": str, "month": str})
    mmc = mmc[(mmc.lob == "MEDICAID")].drop_duplicates(["member_id", "month"])
    memc = pd.read_csv(DATA_DIR / "members.csv", dtype={"rate_cell": str, "term_month": str, "issue_month": str})
    exp_end, exp_start = RUN["experience_end"], RUN["experience_start"]
    rc = A["MEDICAID"]["rate_cells"]; cells = rc["cells"]
    last_year = mmc[mmc.month.str.startswith(exp_end[:4])]
    share = last_year.groupby("rate_cell").size().reindex(cells) / len(last_year)
    win = mmc[(mmc.month >= exp_start) & (mmc.month <= exp_end)]
    prem = win.groupby("rate_cell")["premium"].sum().reindex(cells)
    mmn = win.groupby("rate_cell").size().reindex(cells)
    cap = prem / mmn; cap_rel = cap / float((share * cap).sum())
    # Loss ratio by cell on fully run-out years, claims pooled at the Medicaid pooling point.
    cl_full = pd.read_csv(DATA_DIR / "claims.csv", dtype={"incurred_month": str})
    cm = cl_full[cl_full.lob == "MEDICAID"].merge(memc[["member_id", "rate_cell"]], on="member_id", how="inner")
    mature_end = str(pd.Period(RUN["paid_through"], freq="M") - 12)
    cm = cm[(cm.incurred_month >= exp_start) & (cm.incurred_month <= mature_end)].copy()
    cm["pooled"] = cm["paid_amount"].clip(upper=POOL["MEDICAID"])
    wm = mmc[(mmc.month >= exp_start) & (mmc.month <= mature_end)]
    lr = cm.groupby("rate_cell")["pooled"].sum().reindex(cells) / wm.groupby("rate_cell")["premium"].sum().reindex(cells)
    nclm = cm.groupby("rate_cell").size().reindex(cells)
    cv = (cm.groupby("rate_cell")["pooled"].std() / cm.groupby("rate_cell")["pooled"].mean()).reindex(cells)
    lr_blend = float(cm["pooled"].sum() / wm["premium"].sum())
    se_rel = (lr / lr_blend) * np.sqrt((1 + cv ** 2) / nclm)
    terms = memc[(memc.lob == "MEDICAID") & memc.term_month.notna() & (memc.term_month >= exp_start) & (memc.term_month <= exp_end)]
    churn = terms.groupby("rate_cell").size().reindex(cells) / mmn
    churn_blend = float(len(terms) / mmn.sum())
    se_ch = (churn / churn_blend) / np.sqrt(terms.groupby("rate_cell").size().reindex(cells))
    for i, c in enumerate(cells):
        rows.append(row("MEDICAID", f"rate_cells.member_share[{c}]", rc["member_share"][i], float(share[c]), int(mmn[c]), 1.0, float(share[c]), 0.01,
                        f"Member-month mix, {exp_end[:4]}"))
        rows.append(row("MEDICAID", f"rate_cells.capitation_relativity[{c}]", rc["capitation_relativity"][i], float(cap_rel[c]), int(mmn[c]), 1.0,
                        float(cap_rel[c]), 0.02, "Capitation PMPM by cell / blended at the cell mix"))
        rel = float(lr[c] / lr_blend); credible = abs(rel - 1) > 2 * float(se_rel[c])
        rows.append(row("MEDICAID", f"rate_cells.loss_ratio_relativity[{c}]", rc["loss_ratio_relativity"][i], rel, int(nclm[c]),
                        None, rel if credible else 1.0, 0.02, "Pooled ($250K) loss ratio by cell / blended; moved off 1.0 only if > 2 SE",
                        f"SE {float(se_rel[c]):.3f}; {'credible' if credible else 'not credible'} difference"))
        relq = float(churn[c] / churn_blend); credq = abs(relq - 1) > 2 * float(se_ch[c])
        rows.append(row("MEDICAID", f"rate_cells.disenrollment_relativity[{c}]", rc["disenrollment_relativity"][i], relq, int(terms.groupby('rate_cell').size()[c]),
                        None, relq if credq else 1.0, 0.02, "Terminations per member-month by cell / blended; moved off 1.0 only if > 2 SE",
                        f"SE {float(se_ch[c]):.3f}; {'credible' if credq else 'not credible'} difference"))
    proposed["MEDICAID"]["rate_cells"]["member_share"] = [round(float(share[c]), 3) for c in cells]
    proposed["MEDICAID"]["rate_cells"]["capitation_relativity"] = [round(float(cap_rel[c]), 4) for c in cells]
    rows.append(row("MEDICAID", "annual_disenrollment", A["MEDICAID"]["annual_disenrollment"], 1 - (1 - churn_blend) ** 12, int(len(terms)), None, None, None,
                    "Existing book churn, including the 2023-24 redetermination wave",
                    "Contract assumption comes from the new state's data book; book experience is informational."))

    ind = pd.DataFrame(rows)
    ind.to_csv(OUT / "assumption_indications.csv", index=False)
    (OUT / "proposed_npv_assumptions.json").write_text(json.dumps(proposed, indent=2) + "\n")
    from kitpaths import display
    review_keys = [f"{r.lob}.{r.assumption}" for r in ind[ind.status == "REVIEW"].itertuples()]
    disp = {}
    if status.get("approved"):
        disp = next(a for a in json.loads(APPROVALS.read_text())["approvals"] if a["id"] == status["approval_id"]).get("review_dispositions", {})
    missing = [k for k in review_keys if k not in disp]
    status.update(mode=mode, assumption_file=display(ASSUMPTION_FILE), n_review=len(review_keys),
                  review_items=review_keys, review_dispositions={k: disp[k] for k in review_keys if k in disp},
                  undispositioned_review_items=missing)
    # An approval only covers the indications it addressed: every REVIEW item needs a recorded
    # disposition in the approval record, or the set is treated as not approved for production.
    if status.get("approved") and missing:
        status.update(approved=False, reason=f"approval {status['approval_id']} has no disposition for REVIEW items {missing}")
    (OUT / "assumption_status.json").write_text(json.dumps(status, indent=2) + "\n")

    pd.set_option("display.width", 220)
    show = ind[["lob", "assumption", "current", "experience", "credibility_z", "indicated", "status"]]
    print(show.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))
    print(f"\nREVIEW items for the assumption committee: {status['n_review']}")
    print(f"Assumption set sha256 {status['sha256'][:16]}... model version {status['model_version']}")
    if status["approved"]:
        print(f"APPROVED: {status['approval_id']} by {status['approved_by']} on {status['approved_on']} ({status['scope']})")
        return 0
    msg = f"NOT APPROVED: {status['reason']}"
    if mode == "production":
        print(msg + "\nProduction runs require an approved assumption set. The run stops here.")
        return 1
    print("WARNING (synthetic mode, run continues): " + msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

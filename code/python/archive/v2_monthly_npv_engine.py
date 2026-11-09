"""
archive/v2_monthly_npv_engine.py  (version 2: monthly cash-flow model, ARCHIVED)

Archived unchanged from NPV Kit v3 Phase 1 except for the file paths (assumption file one folder
up; outputs to output/npv_v2) and one renamed assumption key read with a fallback. It is the reference for the v3 regression test:
the v3 engine with every v3 feature switched off must reproduce this engine to the cent.
-------------------------------------------------------------------------------
Net Present Value engine for three lines of business:

    Medicare Supplement   - policy-cohort NPV (1,000 policies issued at 65)
    Dental & Vision       - policy-cohort NPV (1,000 policies)
    Medicaid              - contract NPV (new state launch, 10,000 members)

Why monthly (version 2): version 1 (archive/v1_annual_npv_engine.py) projected
annually with mid-year conventions. The backtest (06_npv_backtest.py) showed
those conventions were biased: most materially, new Medicaid members never
churned in the year they joined (-12% on Medicaid NPV). Version 2 projects
month by month with the contract mechanics the backtest simulates, and the
backtest now finds no statistically significant bias. Version 1 is retained,
unchanged, as the documented prior model.

Cash-flow conventions (each one is the realistic contract mechanic):
  * Premium is collected monthly IN ADVANCE from everyone in force at the start
    of the month (time k-1 months). Commission and premium tax ride on premium,
    so they are paid at the same time.
  * A lapse takes effect at the END of the month already paid for, so the
    lapsing member is covered (and can claim) all month. Deaths occur
    mid-month on average, so exposure = in force at start - 1/2 x deaths.
  * Claims and per-policy maintenance are paid mid-month on exposure.
  * The Med Supp rate increase hits on the policy anniversary, so the extra
    "shock" lapse happens in month 1 of each renewal year; the base lapse is
    spread evenly. Month 1 is sized so each year's total lapse still equals the
    annual assumption (base + shock): the annual study is honoured exactly and
    only the timing within the year changes.
  * Acquisition cost (Med Supp, D&V) and start-up cost (Medicaid) are paid at
    issue / before go-live: time 0, not discounted.
  * Medicaid: members churn monthly; new members join evenly through the year
    (half a month of exposure in the month they join) and can churn from the
    next month. Capitation, claims and admin are mid-month. The minimum-MLR
    remittance is settled on each contract year's results and paid six months
    after the year closes (t + 0.5 years). Years after the initial term are
    weighted by the renewal probability (or set to 0/1 in a Monte Carlo
    scenario).

Annual assumptions are converted to monthly rates exactly:
  monthly q = 1 - (1 - annual q)^(1/12),  monthly discount v = (1 + i)^(-1/12).
Twelve months at the monthly rate reproduce the annual survival, so the
assumption tables need no restatement for the monthly model.

Design principles:
  * One assumption file (npv_assumptions.json) drives this engine and the Excel
    workbook; 05_reconcile_python_to_excel.py ties the two out to the cent.
  * Each projection is a pure function of its assumptions. Sensitivities and
    Monte Carlo call the same functions with shocked inputs, so there is no
    second, simplified model to drift out of line with the base case.
  * The functions are vectorized: any assumption can be a NumPy array of
    scenarios, so the 10,000-scenario Monte Carlo runs in one pass.

Outputs (../../output/npv/): monthly and annual projections, npv_summary,
tornado (csv + png), Monte Carlo (csv, stats, png).
All assumptions are illustrative and are not Humana assumptions.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = OUTPUT_DIR / "npv_v2"
OUT.mkdir(parents=True, exist_ok=True)
# Single source of truth for every assumption; the Excel workbook is built from the same file.
A = json.loads((HERE.parent / "npv_assumptions.json").read_text())


def a2m(q):
    """Annual decrement rate -> constant monthly rate with the same annual survival.

    Uses the constant-force (geometric) split, 1 - (1 - q)^(1/12), rather than q / 12;
    q / 12 would understate the annual decrement once the survivors of each month compound.
    """
    return 1.0 - (1.0 - np.asarray(q, dtype=float)) ** (1.0 / 12.0)


def _f(x):
    """Cast an assumption (scalar or scenario array) to float so it broadcasts across scenarios."""
    return np.asarray(x, dtype=float)


# ----------------------------------------------------------------------------
# Projections (return a dict of monthly arrays; arrays may carry a scenario axis)
#
# Month index k = 0..K-1; policy year t = k // 12 + 1; month of policy year m = 1..12.
# Discounting: cash at the start of month k+1 uses v^(k/12); cash mid-month uses
# v^((k + 0.5)/12). Time-0 items (acquisition, start-up) carry no discount.
# ----------------------------------------------------------------------------
def project_medsupp(a: dict, g: dict) -> dict:
    """Medicare Supplement cohort, monthly. Underwritten at issue age 65, attained-age rated.

    Profit emergence is driven by four features:
      * Selection wear-off: underwritten lives claim below ultimate (selection_factors) for
        the first two policy years and reach ultimate in year 3.
      * Front-loaded commission: first-year commission is roughly double renewal, so year 1
        carries most of the acquisition strain and persistency decides whether it is recovered.
      * Rate increase vs trend: each year the loss ratio moves by roughly
        (claim trend + claim age slope) - (rate increase + premium age step), compounded with
        the selection wear-off. That spread, not the year-1 loss ratio, dominates long-duration NPV.
      * Shock lapse: each rate increase triggers extra lapse at the anniversary, removing
        healthier lives that can re-underwrite elsewhere (the anti-selective effect itself is
        not modeled; only the volume of lapse).
    """
    Y = a["projection_years"]; K = 12 * Y
    i = _f(g["discount_rate"])
    lapse = [_f(x) for x in a["lapse_by_duration"]]
    shock = _f(a["shock_lapse_add_from_year2"])
    rate_inc, trend = _f(a["annual_rate_increase"]), _f(a["claim_trend"])
    clm1 = _f(a["annual_claim_cost_year1_ultimate"])
    # Starting in force broadcast to the scenario shape implied by whichever inputs are arrays.
    n = _f(a["cohort_size"]) * np.ones(np.broadcast(lapse[0], rate_inc, trend, i, clm1).shape)
    rec = {k: [] for k in ["year", "month", "in_force_start", "deaths", "lapses", "exposure", "premium",
                           "claims", "commission", "premium_tax", "maintenance", "pv_cash"]}
    for k in range(K):
        t, m = k // 12 + 1, k % 12 + 1
        # Duration band for lapse and selection: years 1, 2, 3+ (ultimate).
        d = min(t, 3) - 1
        # Attained age is held constant within the policy year (annual age step).
        age = a["issue_age"] + t - 1
        # Gompertz-type mortality from age 65, capped at 0.35 so extreme ages stay a valid probability.
        qd = a2m(min(a["mortality_q65"] * np.exp(a["mortality_growth"] * (age - 65)), 0.35))
        base = np.minimum(lapse[d], 1.0)
        q_year = np.minimum(base + (shock if t >= 2 else 0.0), 1.0)
        base_m = a2m(base)
        # Month 1 lapse solves (1 - q1)(1 - base_m)^11 = 1 - q_year, so the policy-year total
        # equals base + shock while months 2-12 run at the base rate.
        ql = 1 - (1 - q_year) / (1 - base_m) ** 11 if m == 1 else base_m   # anniversary month carries the shock
        # Premium per policy: attained-age step plus compounded approved rate increases.
        prem_pp = a["annual_premium_year1"] * (1 + a["premium_age_step"]) ** (t - 1) * (1 + rate_inc) ** (t - 1)
        # Claim cost per policy: ultimate year-1 cost x aging x trend x selection factor for the duration.
        clm_pp = clm1 * (1 + a["claim_age_slope"]) ** (t - 1) * (1 + trend) ** (t - 1) * a["selection_factors"][d]
        comm_rate = a["first_year_commission"] if t == 1 else a["renewal_commission"]
        maint_pp = a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1)
        # Decrement order within the month: deaths first, lapse applies to survivors of death.
        deaths = n * qd
        lapses = (n - deaths) * ql
        expo = n - 0.5 * deaths                      # lapsers covered to month end; deaths mid-month
        prem = n * prem_pp / 12                      # collected in advance
        claims = expo * clm_pp / 12
        comm, tax, maint = prem * comm_rate, prem * a["premium_tax"], expo * maint_pp / 12
        # Premium-related items at the start of the month; claims and maintenance at mid-month.
        pv = (prem - comm - tax) * (1 + i) ** (-k / 12) - (claims + maint) * (1 + i) ** (-(k + 0.5) / 12)
        for key, val in zip(rec, [t, m, n, deaths, lapses, expo, prem, claims, comm, tax, maint, pv]):
            rec[key].append(val)
        n = n - deaths - lapses
    out = {k: np.array(v) for k, v in rec.items()}
    # Acquisition (underwriting, issue, marketing) is incurred at issue: time 0, undiscounted.
    out["acquisition_t0"] = a["cohort_size"] * a["acquisition_cost_per_policy"]
    # Discount factors kept for the annual table's PV premium / PV claims columns.
    out["v_prem"] = np.array([(1 + i) ** (-k / 12) for k in range(K)])
    out["v_mid"] = np.array([(1 + i) ** (-(k + 0.5) / 12) for k in range(K)])
    out["npv"] = out["pv_cash"].sum(axis=0) - out["acquisition_t0"]
    return out


def project_dv(a: dict, g: dict) -> dict:
    """Individual Dental & Vision cohort, monthly.

    Short-duration product: high early lapse, a first-year pent-up demand load (members buy
    coverage when they already need care), front-loaded commission and no mortality decrement.
    With lapses at the end of the paid month and no deaths, exposure equals in force at the
    start of the month.
    """
    Y = a["projection_years"]; K = 12 * Y
    i = _f(g["discount_rate"])
    lapse = [_f(x) for x in a["lapse_by_duration"]]
    trend, clm1, pent = _f(a["claim_trend"]), _f(a["annual_claim_cost_year1_ultimate"]), _f(a["first_year_pent_up_factor"])
    n = _f(a["cohort_size"]) * np.ones(np.broadcast(lapse[0], trend, i, clm1, pent).shape)
    rec = {k: [] for k in ["year", "month", "in_force_start", "lapses", "exposure", "premium", "claims",
                           "commission", "premium_tax", "maintenance", "pv_cash"]}
    for k in range(K):
        t, m = k // 12 + 1, k % 12 + 1
        # Annual lapse capped at 95% so stressed scenarios (lapse multiplier > 1) stay valid.
        ql = a2m(np.minimum(lapse[min(t, 3) - 1], 0.95))
        prem_pp = a["annual_premium_year1"] * (1 + a["annual_rate_increase"]) ** (t - 1)
        # Pent-up demand applies to policy year 1 only; ultimate cost then grows with trend.
        clm_pp = clm1 * (1 + trend) ** (t - 1) * (pent if t == 1 else 1.0)
        comm_rate = a["first_year_commission"] if t == 1 else a["renewal_commission"]
        maint_pp = a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1)
        lapses = n * ql
        expo = n                                       # lapse at the end of the paid month
        prem = n * prem_pp / 12
        claims = expo * clm_pp / 12
        comm, tax, maint = prem * comm_rate, prem * a["premium_tax"], expo * maint_pp / 12
        pv = (prem - comm - tax) * (1 + i) ** (-k / 12) - (claims + maint) * (1 + i) ** (-(k + 0.5) / 12)
        for key, val in zip(rec, [t, m, n, lapses, expo, prem, claims, comm, tax, maint, pv]):
            rec[key].append(val)
        n = n - lapses
    out = {k: np.array(v) for k, v in rec.items()}
    out["acquisition_t0"] = a["cohort_size"] * a["acquisition_cost_per_policy"]
    out["v_prem"] = np.array([(1 + i) ** (-k / 12) for k in range(K)])
    out["v_mid"] = np.array([(1 + i) ** (-(k + 0.5) / 12) for k in range(K)])
    out["npv"] = out["pv_cash"].sum(axis=0) - out["acquisition_t0"]
    return out


def project_medicaid(a: dict, g: dict, renewed=None) -> dict:
    """Medicaid managed care contract NPV for a new state launch, monthly.

    renewed=None -> expected value (years after the term weighted by the renewal
    probability); renewed=bool array -> one Monte Carlo outcome per scenario.

    Membership is an open group: existing members disenroll at the monthly churn rate and
    new members join evenly through the year. Joiners are exposed to churn from the month
    after they arrive, so membership converges to the steady state
    n* = monthly joiners / monthly churn rate rather than growing with the annual joiner count.
    (Version 1 let a year's joiners escape that year's churn, which overstated member months.)

    MLR floor: the plan remits max(0, floor x revenue - medical) for each contract year.
    The plan keeps adverse claim experience but gives back favorable experience below the
    floor, so it is short an option on medical cost. The remittance is settled on the
    contract year's results and paid six months after year end. A best-estimate run sits
    above the floor and shows little or no remittance; the expected cost of the floor
    appears only in the Monte Carlo (see monte_carlo(no_floor=True)).
    """
    Y = a["projection_years"]; K = 12 * Y
    i = _f(g["discount_rate"])
    q = a2m(_f(a["annual_disenrollment"]))
    rate_upd, trend, mcr = _f(a["annual_rate_update"]), _f(a["claim_trend"]), _f(a["medical_cost_ratio_year1"])
    new = a["annual_new_members"] / 12
    n = _f(a["starting_members"]) * np.ones(np.broadcast(q, rate_upd, trend, mcr, i).shape)
    rec = {k: [] for k in ["year", "month", "members_start", "leavers", "joiners", "member_months", "revenue",
                           "medical", "premium_tax", "admin", "pv_operating"]}
    yearly = {k: [] for k in ["year", "revenue", "medical", "mlr_remittance", "pv_remittance", "pv_operating", "prob_in_force"]}
    # Contract-year accumulators: the MLR test is applied to a full year's revenue and medical.
    rev_y = med_y = pv_y = 0.0
    for k in range(K):
        t, m = k // 12 + 1, k % 12 + 1
        # Capitation moves with the state's annual rate update; medical cost with claim trend.
        # The gap between the two is the margin drift over the contract.
        cap = a["capitation_pmpm_year1"] * (1 + rate_upd) ** (t - 1)
        # Year-1 medical carries an acuity load: a new state's first-year population has unmanaged
        # pent-up need before care management takes effect.
        med_pmpm = a["capitation_pmpm_year1"] * mcr * (1 + trend) ** (t - 1) * ((1 + a["new_state_acuity_load_year1"]) if t == 1 else 1.0)
        admin_pmpm = a["admin_pmpm_year1"] * (1 + g["expense_inflation"]) ** (t - 1)
        leave = n * q
        # Member months: leavers and joiners each contribute half a month on average.
        expo = n - 0.5 * leave + 0.5 * new
        rev, med = expo * cap, expo * med_pmpm
        tax, admin = rev * a["premium_tax"], expo * admin_pmpm
        # Capitation, medical, premium tax and admin are all treated as mid-month cash flows.
        pv = (rev - med - tax - admin) * (1 + i) ** (-(k + 0.5) / 12)
        for key, val in zip(rec, [t, m, n, leave, new, expo, rev, med, tax, admin, pv]):
            rec[key].append(val)
        rev_y = rev_y + rev; med_y = med_y + med; pv_y = pv_y + pv
        # This month's joiners enter the base exposed to churn from next month.
        n = n - leave + new
        if m == 12:
            # Contract-year close: MLR remittance on the year's results, paid at t + 0.5 years.
            remit = np.maximum(0.0, a["mlr_floor"] * rev_y - med_y)
            # Probability the contract is still in force: 1 through the initial term, then the
            # renewal probability (expected value) or the scenario's 0/1 renewal outcome.
            if t <= a["contract_term_years"]:
                p_in = np.ones_like(np.asarray(pv_y, dtype=float))
            elif renewed is None:
                p_in = a["renewal_probability"] * np.ones_like(np.asarray(pv_y, dtype=float))
            else:
                p_in = np.asarray(renewed, dtype=float)
            for key, val in zip(yearly, [t, rev_y, med_y, remit, remit * (1 + i) ** (-(t + 0.5)), pv_y, p_in]):
                yearly[key].append(val)
            rev_y = med_y = pv_y = 0.0
    out = {k: np.array(v) for k, v in rec.items()}
    out["yearly"] = {k: np.array(v) for k, v in yearly.items()}
    # Start-up (implementation, network build, readiness review) is spent before go-live: time 0.
    out["startup_t0"] = a["startup_cost_year1"]
    out["v_mid"] = np.array([(1 + i) ** (-(k + 0.5) / 12) for k in range(K)])
    y = out["yearly"]
    # NPV = sum over contract years of P(in force) x (PV operating result - PV remittance) - start-up.
    # The renewal weight applies to the whole year, remittance included, since no contract
    # means no remittance.
    out["npv"] = (y["prob_in_force"] * (y["pv_operating"] - y["pv_remittance"])).sum(axis=0) - out["startup_t0"]
    return out


PROJECT = {"MEDSUPP": project_medsupp, "DENTAL_VISION": project_dv, "MEDICAID": project_medicaid}


def npv(lob: str, a: dict, g: dict) -> float:
    """Scalar NPV for one line under assumption set (a, g); used by the tornado and the backtest."""
    return float(PROJECT[lob](a, g)["npv"])


# ----------------------------------------------------------------------------
# Annual summary tables (what the workbook's annual sheets show)
#
# The monthly projection is the model; the annual table is a presentation of it.
# Each year's PV profit is the sum of that year's discounted monthly cash, with
# acquisition / start-up in year 1 at time 0, so the column adds back to the
# monthly NPV exactly. main() asserts this: an annual view that did not tie to
# the headline NPV would let reviewers sign off on numbers the model did not produce.
# ----------------------------------------------------------------------------
def annual_table(lob: str, p: dict) -> pd.DataFrame:
    """Roll monthly cash flows into policy / contract years, matching the Excel annual sheets."""
    yr = p["year"]
    def by_year(x):
        return pd.Series(x).groupby(yr).sum().values
    if lob in ("MEDSUPP", "DENTAL_VISION"):
        Y = int(yr.max())
        start = p["in_force_start"][::12]
        # End of year = start of next year; the final year's end is rolled forward from its last month.
        # D&V has no deaths array, hence the zero default.
        end = np.r_[start[1:], p["in_force_start"][-1] - (p.get("deaths", np.zeros_like(yr))[-1] + p["lapses"][-1])]
        acq = np.r_[p["acquisition_t0"], np.zeros(Y - 1)]
        df = pd.DataFrame({"year": np.arange(1, Y + 1), "in_force_start": start, "in_force_end": end,
                           "policy_years_exposure": by_year(p["exposure"]) / 12,
                           "premium": by_year(p["premium"]), "claims": by_year(p["claims"]),
                           "commission": by_year(p["commission"]), "premium_tax": by_year(p["premium_tax"]),
                           "maintenance": by_year(p["maintenance"]), "acquisition": acq})
        df["profit"] = df.premium - df.claims - df.commission - df.premium_tax - df.maintenance - df.acquisition
        # Acquisition is at time 0, so it is deducted at face value, not discounted.
        df["pv_profit"] = by_year(p["pv_cash"]) - acq
        df["pv_premium"] = by_year(p["premium"] * p["v_prem"])
        df["pv_claims"] = by_year(p["claims"] * p["v_mid"])
    else:
        y = p["yearly"]
        Y = len(y["year"])
        start = p["members_start"][::12]
        end = np.r_[start[1:], p["members_start"][-1] - p["leavers"][-1] + p["joiners"][-1]]
        df = pd.DataFrame({"year": y["year"], "members_start": start, "members_end": end,
                           "member_months": by_year(p["member_months"]), "revenue": y["revenue"], "medical": y["medical"],
                           "mlr_remittance": y["mlr_remittance"], "premium_tax": by_year(p["premium_tax"]),
                           "admin": by_year(p["admin"]), "startup": np.r_[p["startup_t0"], np.zeros(Y - 1)]})
        # 'profit' is the undiscounted result if the contract is in force; the expected and PV
        # columns apply the renewal probability.
        df["profit"] = df.revenue - df.medical - df.mlr_remittance - df.premium_tax - df.admin - df.startup
        df["prob_in_force"] = y["prob_in_force"]
        # Start-up is certain (sunk at go-live), so it is not probability-weighted.
        df["expected_profit"] = np.where(df.year == 1, (df.profit + df.startup) * df.prob_in_force - df.startup, df.profit * df.prob_in_force)
        df["pv_profit"] = y["prob_in_force"] * (y["pv_operating"] - y["pv_remittance"]) - df.startup
        df["pv_revenue"] = y["prob_in_force"] * by_year(p["revenue"] * p["v_mid"])
        df["pv_claims"] = y["prob_in_force"] * by_year(p["medical"] * p["v_mid"])
        df["capitation_pmpm"] = df.revenue / df.member_months
        df["medical_pmpm"] = df.medical / df.member_months
        df["mlr"] = df.medical / df.revenue
    df["cumulative_pv_profit"] = df["pv_profit"].cumsum()
    if "premium" in df:
        df["loss_ratio"] = df.claims / df.premium
    return df


def summarize(lob: str, tab: pd.DataFrame) -> dict:
    """Headline metrics from the annual table.

    PV loss ratio and NPV margin are ratios of present values (not averages of annual
    ratios), so they weight each year by its discounted volume. Break-even year is the first
    year in which cumulative PV profit turns positive (discounted payback); None if never.
    """
    pv_rev = tab["pv_premium" if "pv_premium" in tab else "pv_revenue"].sum()
    pv_clm = tab["pv_claims"].sum()
    npv_ = tab["pv_profit"].sum()
    units = A[lob]["starting_members"] if lob == "MEDICAID" else A[lob]["cohort_size"]
    cum = tab["cumulative_pv_profit"]
    be = int(tab.loc[cum > 0, "year"].min()) if (cum > 0).any() else None
    return dict(lob=lob, npv=npv_, pv_revenue=pv_rev, pv_claims=pv_clm, pv_loss_ratio=pv_clm / pv_rev,
                npv_margin=npv_ / pv_rev, breakeven_year=be, npv_per_unit=npv_ / units)


# ----------------------------------------------------------------------------
# Sensitivities (tornado): one assumption shocked at a time
#
# Each shock deep-copies the assumptions and reruns the production projection
# through npv(), so a sensitivity is the same model with one input changed, not a
# separate approximation. The Excel Sensitivity sheet runs the same 15 scenarios
# and the reconcile script ties them out.
# ----------------------------------------------------------------------------
def shocked(lob: str, key: str, how: str, val: float) -> float:
    """NPV with one assumption shocked: 'mult_list' scales every duration, 'add' / 'mult' scalars."""
    a = copy.deepcopy(A[lob]); g = copy.deepcopy(A["global"])
    # The discount rate lives in the global block; every other driver is line-specific.
    target = g if key == "discount_rate" else a
    if how == "mult_list":
        target[key] = [x * val for x in target[key]]
    elif how == "add":
        target[key] = target[key] + val
    elif how == "mult":
        target[key] = target[key] * val
    return npv(lob, a, g)


# (label, assumption key, shock type, favorable-side value, adverse-side value).
# Shock sizes are illustrative judgment ranges, not calibrated percentiles, so the chart
# ranks exposure to each driver rather than equal-probability outcomes. Renewal +/- 0.25
# around the 75% base gives the 100% / 50% labels;
# the pent-up shocks around 1.28 give 1.15 / 1.40.
SENS = {
    "MEDSUPP": [("Lapse rates x0.8 / x1.2", "lapse_by_duration", "mult_list", 0.8, 1.2),
                ("Claim trend -1% / +1%", "claim_trend", "add", -0.01, 0.01),
                ("Rate increases +1% / -1%", "annual_rate_increase", "add", 0.01, -0.01),
                ("Discount rate -2% / +2%", "discount_rate", "add", -0.02, 0.02),
                ("Year-1 claim cost -5% / +5%", "annual_claim_cost_year1_ultimate", "mult", 0.95, 1.05)],
    "DENTAL_VISION": [("Lapse rates x0.8 / x1.2", "lapse_by_duration", "mult_list", 0.8, 1.2),
                      ("Pent-up factor 1.15 / 1.40", "first_year_pent_up_factor", "add", -0.13, 0.12),
                      ("Claim trend -1% / +1%", "claim_trend", "add", -0.01, 0.01),
                      ("Discount rate -2% / +2%", "discount_rate", "add", -0.02, 0.02),
                      ("Year-1 claim cost -5% / +5%", "annual_claim_cost_year1_ultimate", "mult", 0.95, 1.05)],
    "MEDICAID": [("Rate update +1% / -1%", "annual_rate_update", "add", 0.01, -0.01),
                 ("Claim trend -1% / +1%", "claim_trend", "add", -0.01, 0.01),
                 ("Yr-1 medical ratio -2pt / +2pt", "medical_cost_ratio_year1", "add", -0.02, 0.02),
                 ("Renewal probability 100% / 50%", "renewal_probability", "add", 0.25, -0.25),
                 ("Disenrollment x0.8 / x1.2", "annual_disenrollment", "mult", 0.8, 1.2)],
}


def tornado() -> pd.DataFrame:
    """Base, favorable and adverse NPV for every driver in SENS; swing = |favorable - adverse|."""
    rows = []
    for lob, items in SENS.items():
        base = npv(lob, A[lob], A["global"])
        for name, key, how, lo, hi in items:
            rows.append(dict(lob=lob, driver=name, base_npv=base,
                             npv_favorable_side=shocked(lob, key, how, lo),
                             npv_adverse_side=shocked(lob, key, how, hi)))
    df = pd.DataFrame(rows)
    df["swing"] = (df["npv_favorable_side"] - df["npv_adverse_side"]).abs()
    return df


# ----------------------------------------------------------------------------
# Monte Carlo (parameter uncertainty). Each driver gets its own independent
# random shock; the SAME trend shock applies to all three lines (one shared cost
# environment) and the same lapse multiplier to Med Supp and D&V. Correlations
# between drivers (trend -> rate increases -> shock lapse) are not modeled;
# that is a stated simplification. In practice Med Supp rate increases partly
# follow emerging trend, so treating them as independent likely overstates the
# Med Supp spread; the trend -> rate -> shock-lapse chain is not captured at all.
#
# Each draw is a level shift held for the whole projection (a wrong assumption),
# not year-to-year volatility: this measures uncertainty ABOUT the assumptions.
# Randomness of outcomes given the assumptions (process risk) is measured
# separately by the backtest's Test A.
# ----------------------------------------------------------------------------
def monte_carlo(no_floor: bool = False) -> pd.DataFrame:
    """Vectorized NPV distribution by line. no_floor=True reruns Medicaid with the MLR floor removed.

    The fixed seed gives the no-floor run the identical draws (common random numbers), so the
    difference in Medicaid mean NPV between the two runs is the expected cost of the floor
    alone, free of sampling noise.
    """
    mc = A["monte_carlo"]
    rng = np.random.default_rng(mc["seed"])
    n = mc["n_sims"]
    # Lognormal lapse multiplier with mu = -sigma^2 / 2, so E[multiplier] = 1: positive,
    # right-skewed, and centred on the best-estimate lapse level rather than biasing it.
    lapse_mult = rng.lognormal(mean=-0.5 * mc["lapse_multiplier_sd"] ** 2, sigma=mc["lapse_multiplier_sd"], size=n)
    # Additive normal shocks to annual rates (trend, rate increase, Medicaid rate update).
    trend_shock = rng.normal(0, mc["trend_shock_sd"], size=n)
    # Positive shortfall = approved Med Supp rate increases below the filed assumption.
    # Archive compatibility (kit v3.0.0): the key was renamed medsupp_rate_increase_deviation_sd.
    rate_short = rng.normal(0, mc.get("rate_increase_shortfall_sd", mc.get("medsupp_rate_increase_deviation_sd")), size=n)
    md_rate = rng.normal(0, mc["medicaid_rate_update_sd"], size=n)
    # Renewal resolved as 0/1 per scenario; the probability should match MEDICAID renewal_probability
    # so the Monte Carlo mean is comparable with the deterministic expected value.
    renewed = rng.random(n) < mc["medicaid_renewal_prob"]
    g = A["global"]

    a = copy.deepcopy(A["MEDSUPP"])
    a["lapse_by_duration"] = [x * lapse_mult for x in a["lapse_by_duration"]]
    a["claim_trend"] = a["claim_trend"] + trend_shock
    a["annual_rate_increase"] = a["annual_rate_increase"] - rate_short
    ms = project_medsupp(a, g)["npv"]

    a = copy.deepcopy(A["DENTAL_VISION"])
    # Same 95% cap as the projection, applied after the multiplier.
    a["lapse_by_duration"] = [np.minimum(x * lapse_mult, 0.95) for x in a["lapse_by_duration"]]
    a["claim_trend"] = a["claim_trend"] + trend_shock
    dv = project_dv(a, g)["npv"]

    a = copy.deepcopy(A["MEDICAID"])
    a["claim_trend"] = a["claim_trend"] + trend_shock
    a["annual_rate_update"] = a["annual_rate_update"] + md_rate
    if no_floor:
        # Counterfactual only: isolates what the floor's asymmetric payoff costs in expectation.
        a["mlr_floor"] = 0.0
    md = project_medicaid(a, g, renewed=renewed)["npv"]
    return pd.DataFrame({"MEDSUPP": ms, "DENTAL_VISION": dv, "MEDICAID": md})


# ----------------------------------------------------------------------------
# Run: deterministic projections and tie-out, tornado, Monte Carlo.
# ----------------------------------------------------------------------------
def main() -> None:
    g = A["global"]
    summaries = []
    for lob, fn in PROJECT.items():
        p = fn(A[lob], g)
        # Export only the monthly series (1-D, length K); scalars and the Medicaid yearly block are excluded.
        monthly = {k: v for k, v in p.items() if isinstance(v, np.ndarray) and v.ndim == 1 and len(v) == 12 * A[lob]["projection_years"]}
        pd.DataFrame(monthly).to_csv(OUT / f"npv_monthly_{lob}.csv", index=False)
        tab = annual_table(lob, p)
        tab.to_csv(OUT / f"npv_projection_{lob}.csv", index=False)
        s = summarize(lob, tab)
        # Control: the annual presentation must add back to the monthly NPV.
        assert abs(s["npv"] - float(p["npv"])) < 1e-6, "annual table does not add up to the monthly NPV"
        summaries.append(s)
    s = pd.DataFrame(summaries)
    s.to_csv(OUT / "npv_summary.csv", index=False)
    print("\nDeterministic NPV summary (version 2, monthly; illustrative assumptions)")
    print(s.to_string(index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))

    tor = tornado()
    tor.to_csv(OUT / "npv_tornado.csv", index=False)
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    for ax, lob in zip(axes, PROJECT):
        # Largest swing plotted at the top; bars drawn as offsets from the base NPV.
        d = tor[tor.lob == lob].sort_values("swing")
        base = d["base_npv"].iloc[0] / 1e3
        for j, r in enumerate(d.itertuples()):
            ax.barh(j, r.npv_favorable_side / 1e3 - base, left=base, color="#2A9D8F")
            ax.barh(j, r.npv_adverse_side / 1e3 - base, left=base, color="#D1495B")
        ax.axvline(base, color="black", lw=1)
        ax.set_yticks(range(len(d))); ax.set_yticklabels(d["driver"], fontsize=8)
        ax.set_title(f"{lob}: NPV sensitivity ($000)", fontsize=10)
        ax.set_xlabel("NPV ($000)  green = favorable, red = adverse")
    plt.tight_layout(); plt.savefig(OUT / "npv_tornado.png", dpi=130); plt.close()
    print("\nTop driver by line:")
    print(tor.loc[tor.groupby("lob")["swing"].idxmax(), ["lob", "driver", "swing"]].to_string(index=False))

    print(f"\nRunning Monte Carlo ({A['monte_carlo']['n_sims']:,} scenarios) ...")
    mc = monte_carlo()
    mc.to_csv(OUT / "npv_monte_carlo.csv", index=False)
    # Same seed, floor removed: mean_without_mlr_floor - mean is the floor's expected cost.
    mc_nf = monte_carlo(no_floor=True)
    stats = pd.DataFrame({lob: {"P5": np.percentile(mc[lob], 5), "P50": np.percentile(mc[lob], 50),
                                "P95": np.percentile(mc[lob], 95), "mean": mc[lob].mean(),
                                "prob_npv_negative": (mc[lob] < 0).mean()} for lob in mc}).T
    stats.loc["MEDICAID", "mean_without_mlr_floor"] = mc_nf["MEDICAID"].mean()
    stats.to_csv(OUT / "npv_monte_carlo_stats.csv")
    print(stats.to_string(float_format=lambda x: f"{x:,.3f}" if abs(x) < 1 else f"{x:,.0f}"))
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.2))
    for ax, lob in zip(axes, mc.columns):
        ax.hist(mc[lob] / 1e3, bins=60, color="#12343B", alpha=0.85)
        for q, c in [(5, "#D1495B"), (50, "black"), (95, "#2A9D8F")]:
            ax.axvline(np.percentile(mc[lob], q) / 1e3, color=c, ls="--", lw=1)
        ax.axvline(0, color="grey", lw=0.8)
        ax.set_title(f"{lob}: NPV distribution ($000)\nP(NPV<0) = {(mc[lob] < 0).mean():.1%}", fontsize=10)
    plt.tight_layout(); plt.savefig(OUT / "npv_monte_carlo.png", dpi=130); plt.close()
    print(f"\nOutputs written to {OUT}")


if __name__ == "__main__":
    main()

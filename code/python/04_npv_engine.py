"""
04_npv_engine.py  (version 4: monthly cash-flow model + after-tax profit test + v4 risk layers)
-------------------------------------------------------------------------------
Net Present Value engine for three lines of business:

    Medicare Supplement   - policy-cohort NPV (1,000 policies issued at 65)
    Dental & Vision       - policy-cohort NPV (1,000 policies)
    Medicaid              - contract NPV (new state launch, 10,000 members)

Two results per line:
    Cash NPV  (pre-tax)  - claims at incurred timing, no reserves, capital or tax. This is the
                           version 2 measure, kept so results stay comparable.
    DE NPV    (after-tax) - distributable earnings: claims paid on the line's lag pattern, claim
                           reserves and the Medicaid MLR remittance held as liabilities, required
                           capital held and released, investment income on assets, federal income
                           tax. This is the v3 headline for a pricing or business decision.

History
    Version 1 (archive/v1_annual_npv_engine.py): annual, mid-year conventions. The backtest
      found it biased (Medicaid -12%: new members never churned in their first year).
    Version 2 (archive/v2_monthly_npv_engine.py): monthly, with the real contract mechanics.
      Backtest unbiased on all three lines.
    Version 3 (this file) adds, each behind a switch in npv_assumptions.json -> global.features:
      * antiselective_shock_lapse   Med Supp: only healthier "shoppers" take the rate-increase
                                    shock lapse, so the remaining block's claim cost rises.
      * dynamic_shock_lapse         Med Supp: shock lapse scales with the size of the increase.
      * medicaid_rate_cells         Medicaid: Child / Adult / ABD rate cells with their own mix,
                                    capitation, cost and churn, plus budget-neutral risk adjustment.
      * correlated_monte_carlo      Trend, rate actions and lapse drawn jointly, not independently.
    and a profit-test layer (profit_test()) that turns the cash flows into DE NPV.
    Version 4 adds, each neutral at its neutral settings (tests/test_v4_model.py):
      * medicaid_contract_terms     quality withhold, directed payments, MLR floor or risk corridor,
                                    acuity path with lagged risk adjustment.
      * dv_benefit_categories       preventive / basic / major / vision: share, trend add-on,
                                    pent-up relativity, waiting period.
      * mc_process_risk             year-to-year AR(1) trend and rate noise in the Monte Carlo.
      * mc_management_response      Med Supp / D&V rate filings follow lagged experience.
    and break-even (reverse stress), planning scenarios, Monte Carlo attribution and Med Supp
    model points (issue-age band x plan).
    With every switch off the cash-flow engine reproduces version 2 to the cent
    (tests/test_v3_model.py). The profit test with zero tax, zero capital, an earned rate equal
    to the discount rate and immediate claim payment reproduces the cash NPV exactly.

Cash-flow conventions (each one is the realistic contract mechanic):
  * Premium is collected monthly IN ADVANCE from everyone in force at the start of the month.
    Commission and premium tax ride on premium, so they are paid at the same time.
  * A lapse takes effect at the END of the month already paid for, so the lapsing member is
    covered (and can claim) all month. Deaths occur mid-month on average, so exposure = in force
    at start - 1/2 x deaths.
  * Claims are incurred and (in the cash NPV) paid mid-month on exposure; per-policy maintenance
    is paid mid-month. The profit test re-times claim payments on the lag pattern.
  * The Med Supp rate increase hits on the policy anniversary, so the extra "shock" lapse
    happens in month 1 of each renewal year; the base lapse is spread evenly. Month 1 is sized
    so each year's total block lapse still equals the annual assumption (base + shock).
  * Acquisition cost (Med Supp, D&V) and start-up cost (Medicaid) are paid at issue / before
    go-live: time 0, not discounted.
  * Medicaid: members churn monthly; new members join evenly through the year (half a month of
    exposure in the month they join) and churn from the next month. The minimum-MLR remittance
    is settled on each contract year's results and paid six months after the year closes.
    Years after the initial term are weighted by the renewal probability (or 0/1 per scenario).

Annual assumptions are converted to monthly rates exactly:
  monthly q = 1 - (1 - annual q)^(1/12),  monthly discount v = (1 + i)^(-1/12).

Design principles:
  * One assumption file (npv_assumptions.json) drives this engine and the Excel workbook;
    05_reconcile_python_to_excel.py ties the two out to the cent.
  * Each projection is a pure function of its assumptions. Sensitivities and Monte Carlo call the
    same functions with shocked inputs, so there is no second, simplified model to drift.
  * Vectorized: any assumption can be a NumPy array of scenarios, so the 10,000-scenario Monte
    Carlo (cash and DE NPV) runs in one pass.

Outputs (../../output/npv/): monthly and annual projections, profit-test tables, npv_summary,
tornado (csv + png), Monte Carlo (csv, stats, png), v2 -> v3 bridge.
Assumptions come from public sources, statute and documented judgment (config/assumption_basis.json); they are not Humana assumptions.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = OUTPUT_DIR / "npv"
# Single source of truth for every assumption; the Excel workbook and the notebook read the same
# file through the same resolver (assumption_control.py), which is also what the approval gate
# (03b_assumption_governance.py) checks.
import assumption_control as _ac  # noqa: E402
ASSUMPTION_FILE = _ac.assumption_file()
A = json.loads(ASSUMPTION_FILE.read_text())
FEATURES = ["antiselective_shock_lapse", "dynamic_shock_lapse", "medicaid_rate_cells", "correlated_monte_carlo",
            # v4 (each neutral at its neutral settings; see tests/test_v4_model.py)
            "medicaid_contract_terms", "dv_benefit_categories", "mc_process_risk", "mc_management_response"]


def a2m(q):
    """Annual decrement rate -> constant monthly rate with the same annual survival.

    Uses the constant-force (geometric) split, 1 - (1 - q)^(1/12), rather than q / 12;
    q / 12 would understate the annual decrement once the survivors of each month compound.
    """
    return 1.0 - (1.0 - np.asarray(q, dtype=float)) ** (1.0 / 12.0)


def _f(x):
    """Cast an assumption (scalar or scenario array) to float so it broadcasts across scenarios."""
    return np.asarray(x, dtype=float)


def feature(g: dict, name: str) -> bool:
    """A v3 feature is on only if the assumption set says so (absent = off = version 2 behaviour)."""
    return bool(g.get("features", {}).get(name, False))


def without_features(g: dict) -> dict:
    """Copy of the global block with every v3 feature switched off (the version 2 model)."""
    g2 = copy.deepcopy(g)
    g2["features"] = {k: False for k in FEATURES}
    return g2


def growth_index(rate, t: int, dev=None):
    """Compound index for policy / contract year t (year 1 = 1.0).

    Without a path this is exactly (1 + rate)^(t - 1), the expression every earlier version
    used, so deterministic results are bit-identical. v4 Monte Carlo paths pass dev, an array of
    per-step deviations (dev[s - 1] is the deviation of the step from year s to year s + 1, with
    an optional scenario axis): index_t = prod_{s < t} (1 + rate + dev_s).
    """
    if dev is None:
        return (1 + rate) ** (t - 1)
    out = np.ones(np.broadcast(np.asarray(rate, dtype=float), np.asarray(dev[0], dtype=float)).shape) if t > 1 else 1.0
    for s_ in range(1, t):
        out = out * (1 + rate + dev[s_ - 1])
    return out


def _path(a: dict, key: str):
    """Monte Carlo deviation path attached to an assumption copy (never present in the base)."""
    return a.get("_paths", {}).get(key)


# ----------------------------------------------------------------------------
# Projections (return a dict of monthly arrays; arrays may carry a scenario axis)
#
# Month index k = 0..K-1; policy year t = k // 12 + 1; month of policy year m = 1..12.
# Discounting: cash at the start of month k+1 uses v^(k/12); cash mid-month uses
# v^((k + 0.5)/12). Time-0 items (acquisition, start-up) carry no discount.
# ----------------------------------------------------------------------------
def medsupp_shock(a: dict, g: dict, rate_inc):
    """Annual shock lapse added in renewal years.

    Dynamic (v3): shock = max(0, base shock + slope x (rate increase - reference increase)), so a
    larger approved increase drives more lapse and a smaller one less. At the reference increase
    (the filed assumption) it equals the base shock, so the deterministic base case is unchanged;
    the effect shows in the rate-increase sensitivity and the Monte Carlo.
    """
    shock = _f(a["shock_lapse_add_from_year2"])
    if feature(g, "dynamic_shock_lapse"):
        d = a["dynamic_shock_lapse"]
        shock = np.maximum(0.0, shock + d["shock_lapse_per_unit_rate_increase"] * (rate_inc - d["reference_rate_increase"]))
    return shock


def medsupp_classes(a: dict, g: dict):
    """(shopper share at issue, shopper claim relativity, non-shopper claim relativity, propensity).

    Anti-selective shock lapse (v3): members are split at issue into 'shoppers', who can pass
    underwriting elsewhere, and 'non-shoppers'. At each rate increase shoppers are `propensity`
    times as likely as non-shoppers to take the extra (shock) lapse. The claim relativities are
    normalized so the issue-mix average is exactly 1.0: year-1 claims are unchanged and the effect
    builds as shoppers leave faster. Off: one class with relativity 1.0 (the version 2 model).
    """
    if not feature(g, "antiselective_shock_lapse"):
        return 1.0, 1.0, 1.0, 1.0
    s = a["antiselection"]
    p, rs = s["shopper_share_at_issue"], s["shopper_relative_claim_cost"]
    rn = (1.0 - p * rs) / (1.0 - p) if p < 1 else 1.0
    return p, rs, rn, s["shopper_shock_lapse_propensity"]


def project_medsupp(a: dict, g: dict) -> dict:
    """Medicare Supplement cohort, monthly. Underwritten at issue age 65, attained-age rated.

    Profit emergence is driven by:
      * Selection wear-off: underwritten lives claim below ultimate (selection_factors) for the
        first two policy years and reach ultimate in year 3.
      * Front-loaded commission: first-year commission is roughly double renewal, so year 1
        carries most of the acquisition strain and persistency decides whether it is recovered.
      * Rate increase vs trend: each year the loss ratio moves by roughly
        (claim trend + claim age slope) - (rate increase + premium age step), compounded with the
        selection wear-off. That spread, not the year-1 loss ratio, dominates long-duration NPV.
      * Shock lapse at each anniversary rate increase, which (v3) is anti-selective: the healthier
        shoppers leave, so the remaining block's claim cost per policy rises over time.
    """
    Y = a["projection_years"]; K = 12 * Y
    i = _f(g["discount_rate"])
    lapse = [_f(x) for x in a["lapse_by_duration"]]
    rate_inc, trend = _f(a["annual_rate_increase"]), _f(a["claim_trend"])
    shock = medsupp_shock(a, g, rate_inc)
    # v4 Monte Carlo paths (absent in every deterministic run): year-by-year trend deviations and
    # rate-increase deviations (process risk, management response). The anniversary shock lapse
    # then follows each year's actual increase.
    tdev, rdev = _path(a, "trend"), _path(a, "rate")
    clm1 = _f(a["annual_claim_cost_year1_ultimate"])
    share, rs, rn, kappa = medsupp_classes(a, g)
    rs, rn = _f(rs), _f(rn)
    # Starting in force broadcast to the scenario shape implied by whichever inputs are arrays.
    shape = np.broadcast(lapse[0], rate_inc, trend, i, clm1, shock, rs, rn).shape
    nS = _f(a["cohort_size"]) * share * np.ones(shape)          # shoppers
    nN = _f(a["cohort_size"]) * (1.0 - share) * np.ones(shape)  # non-shoppers
    rec = {k: [] for k in ["year", "month", "in_force_start", "deaths", "lapses", "exposure", "premium",
                           "claims", "commission", "premium_tax", "maintenance", "pv_cash",
                           "in_force_shoppers", "claim_relativity"]}
    cap_months = 0
    for k in range(K):
        t, m = k // 12 + 1, k % 12 + 1
        # Duration band for lapse and selection: years 1, 2, 3+ (ultimate).
        d = min(t, 3) - 1
        # Attained age is held constant within the policy year (annual age step).
        age = a["issue_age"] + t - 1
        # Gompertz-type mortality from age 65, capped at 0.35 so extreme ages stay a valid probability.
        qd = a2m(min(a["mortality_q65"] * np.exp(a["mortality_growth"] * (age - 65)), 0.35))
        base = np.minimum(lapse[d], 1.0)
        shock_t = shock if (rdev is None or t < 2) else medsupp_shock(a, g, rate_inc + rdev[t - 2])
        q_year = np.minimum(base + (shock_t if t >= 2 else 0.0), 1.0)
        base_m = a2m(base)
        # Block lapse rate this month. Month 1 solves (1 - q1)(1 - base_m)^11 = 1 - q_year, so the
        # policy-year total equals base + shock while months 2-12 run at the base rate.
        ql = 1 - (1 - q_year) / (1 - base_m) ** 11 if m == 1 else base_m
        n = nS + nN
        # Decrement order within the month: deaths first, lapse applies to survivors of death.
        dS, dN = nS * qd, nN * qd
        sS, sN = nS - dS, nN - dN                  # survivors of death, by class
        # Both classes take the base monthly rate. The block's extra (shock) lapse this month,
        # (survivors) x (ql - base_m), is split so a shopper is kappa times as likely as a
        # non-shopper to take it: extra rate x for non-shoppers and kappa x for shoppers, with
        # x = extra lapses / (kappa x shoppers + non-shoppers). The block total equals the lapse
        # study as long as the shopper rate stays below 100%; it is capped there, and the run warns
        # if that ever happens (it does not in any base, tornado or Monte Carlo scenario of this
        # kit: the highest anniversary-month shopper rate is about 16%). With one class (feature
        # off) this is the version 2 block lapse.
        extra = (sS + sN) * (ql - base_m)
        x = extra / np.maximum(kappa * sS + sN, 1e-300)
        capped = np.any(base_m + kappa * x > 1.0)
        lS = sS * np.minimum(base_m + kappa * x, 1.0)
        lN = sN * (base_m + x)
        deaths, lapses = dS + dN, lS + lN
        expoS, expoN = nS - 0.5 * dS, nN - 0.5 * dN          # lapsers covered to month end
        expo = expoS + expoN
        # Premium per policy: attained-age step plus compounded approved rate increases.
        prem_pp = a["annual_premium_year1"] * (1 + a["premium_age_step"]) ** (t - 1) * growth_index(rate_inc, t, rdev)
        # Claim cost per policy: ultimate year-1 cost x aging x trend x selection factor for the duration.
        clm_pp = clm1 * (1 + a["claim_age_slope"]) ** (t - 1) * growth_index(trend, t, tdev) * a["selection_factors"][d]
        comm_rate = a["first_year_commission"] if t == 1 else a["renewal_commission"]
        maint_pp = a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1)
        prem = n * prem_pp / 12                      # collected in advance
        claims = (expoS * rs + expoN * rn) * clm_pp / 12
        comm, tax, maint = prem * comm_rate, prem * a["premium_tax"], expo * maint_pp / 12
        # Premium-related items at the start of the month; claims and maintenance at mid-month.
        pv = (prem - comm - tax) * (1 + i) ** (-k / 12) - (claims + maint) * (1 + i) ** (-(k + 0.5) / 12)
        rel = (expoS * rs + expoN * rn) / expo
        for key, val in zip(rec, [t, m, n, deaths, lapses, expo, prem, claims, comm, tax, maint, pv, nS, rel]):
            rec[key].append(val)
        cap_months += int(capped)
        nS, nN = sS - lS, sN - lN
    out = {k: np.array(v) for k, v in rec.items()}
    out["shopper_cap_months"] = cap_months     # months where the shopper lapse rate hit 100% (block lapse below study)
    # Acquisition (underwriting, issue, marketing) is incurred at issue: time 0, undiscounted.
    out["acquisition_t0"] = a["cohort_size"] * a["acquisition_cost_per_policy"]
    out["v_prem"] = np.array([(1 + i) ** (-k / 12) for k in range(K)])
    out["v_mid"] = np.array([(1 + i) ** (-(k + 0.5) / 12) for k in range(K)])
    out["npv"] = out["pv_cash"].sum(axis=0) - out["acquisition_t0"]
    # Cash-flow streams for the profit test: start-of-month, mid-month non-claim, incurred claims.
    out["cf_start"] = out["premium"] - out["commission"] - out["premium_tax"]
    out["cf_mid"] = -out["maintenance"]
    out["incurred"] = out["claims"]
    out["t0"] = -out["acquisition_t0"]
    return out


def project_dv(a: dict, g: dict) -> dict:
    """Individual Dental & Vision cohort, monthly.

    Short-duration product: high early lapse, a first-year pent-up demand load (members buy
    coverage when they already need care), front-loaded commission and no mortality decrement.
    With lapses at the end of the paid month and no deaths, exposure equals in force at the
    start of the month. Unchanged from version 2 apart from the profit-test streams.
    """
    Y = a["projection_years"]; K = 12 * Y
    i = _f(g["discount_rate"])
    lapse = [_f(x) for x in a["lapse_by_duration"]]
    trend, clm1, pent = _f(a["claim_trend"]), _f(a["annual_claim_cost_year1_ultimate"]), _f(a["first_year_pent_up_factor"])
    n = _f(a["cohort_size"]) * np.ones(np.broadcast(lapse[0], trend, i, clm1, pent).shape)
    tdev, rdev = _path(a, "trend"), _path(a, "rate")
    cats = dv_categories(a, g)
    rec = {k: [] for k in ["year", "month", "in_force_start", "lapses", "exposure", "premium", "claims",
                           "commission", "premium_tax", "maintenance", "pv_cash"]}
    for k in range(K):
        t, m = k // 12 + 1, k % 12 + 1
        # Annual lapse capped at 95% so stressed scenarios (lapse multiplier > 1) stay valid.
        ql = a2m(np.minimum(lapse[min(t, 3) - 1], 0.95))
        prem_pp = a["annual_premium_year1"] * growth_index(_f(a["annual_rate_increase"]), t, rdev)
        # Pent-up demand applies to policy year 1 only; ultimate cost then grows with trend.
        if cats is None:
            clm_pp = clm1 * growth_index(trend, t, tdev) * (pent if t == 1 else 1.0)
        else:
            # v4 benefit categories: each category has its own share of ultimate cost, trend add-on,
            # year-1 pent-up relativity (normalized so the blended year-1 factor is the line's
            # pent-up factor) and waiting period in months (no benefit before it ends).
            # v4.1: pent-up demand is DEFERRED by the waiting period, not removed: the category's
            # pent-up load applies to its first 12 months of eligibility (policy months wt .. wt+11).
            # With no waiting period that is policy year 1, as before.
            clm_pp = clm1 * sum(sh * growth_index(trend + ta, t, tdev) * (pent * pr if wt <= k < wt + 12 else 1.0) * (1.0 if k >= wt else 0.0)
                                for sh, ta, pr, wt in cats)
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
    out["cf_start"] = out["premium"] - out["commission"] - out["premium_tax"]
    out["cf_mid"] = -out["maintenance"]
    out["incurred"] = out["claims"]
    out["t0"] = -out["acquisition_t0"]
    return out


def dv_categories(a: dict, g: dict):
    """D&V benefit categories as (share, trend add-on, pent-up relativity, waiting months), or None.

    Shares are of ultimate claim cost (they sum to 1); the pent-up relativities are normalized so
    the share-weighted year-1 factor equals first_year_pent_up_factor, which keeps the line-level
    pent-up lever (tornado, Excel) meaning the same thing. With every trend add-on 0, relativity 1
    and waiting period 0 this reproduces the single-category model.
    """
    if not feature(g, "dv_benefit_categories"):
        return None
    bc = a["benefit_categories"]
    sh = np.array(bc["cost_share"], dtype=float)
    assert abs(sh.sum() - 1.0) < 1e-9, "D&V benefit category shares must sum to 1"
    pr = np.array(bc["pent_up_relativity"], dtype=float); pr = pr / (sh * pr).sum()
    return list(zip(sh, bc["trend_add_on"], pr, bc["waiting_period_months"]))


def medicaid_cells(a: dict, g: dict):
    """Normalized rate-cell table: (names, share, capitation rel., loss-ratio rel., churn rel.).

    Normalization keeps the contract-level assumptions meaningful as the blended year-1 values
    at the starting mix:
        sum(share x cap_rel) = 1              blended year-1 capitation = capitation_pmpm_year1
        sum(share x cap_rel x cost_rel) = 1   blended year-1 cost ratio  = medical_cost_ratio_year1
        sum(share x churn_rel) = 1            blended year-1 churn       = annual_disenrollment
    so the tornado and Monte Carlo shock the same levers as before, and the cells add the mix
    drift those levers cannot show (cells churn at different rates, so the mix moves over time).
    Off: one cell with every relativity 1.0, which is the version 2 model.
    """
    if not feature(g, "medicaid_rate_cells"):
        return ["ALL"], np.array([1.0]), np.array([1.0]), np.array([1.0]), np.array([1.0])
    rc = a["rate_cells"]
    s = np.array(rc["member_share"], dtype=float); s = s / s.sum()
    cap = np.array(rc["capitation_relativity"], dtype=float); cap = cap / (s * cap).sum()
    # Loss-ratio relativity: a cell's medical cost is its capitation x blended cost ratio x this.
    med = np.array(rc["loss_ratio_relativity"], dtype=float); med = med / (s * cap * med).sum()
    chn = np.array(rc["disenrollment_relativity"], dtype=float); chn = chn / (s * chn).sum()
    return list(rc["cells"]), s, cap, med, chn


def medicaid_risk_adjustment(a: dict, g: dict):
    """(revenue factor, medical factor) from budget-neutral risk adjustment; (1, 1) when off.

    The plan's medical cost scales with its acuity relative to the state average. The state's
    budget-neutral risk adjustment moves capitation by revenue_capture x (acuity - 1): it pays for
    most, not all, of a sicker-than-average population (and claws back most of a healthier one).
    """
    if not feature(g, "medicaid_rate_cells"):
        return 1.0, 1.0
    ra = a["risk_adjustment"]
    acu = _f(ra["plan_acuity"])
    return 1.0 + ra["revenue_capture"] * (acu - 1.0), acu


def medicaid_terms(a: dict, g: dict):
    """v4 Medicaid contract terms (None when the feature is off: the v3 contract).

    acuity          medical-cost multiplier by contract year (e.g. post-redetermination acuity
                    shift); risk adjustment recognizes capture x (path - 1) after `lag` years
    withhold        quality withhold, share of capitation held back; earn_back = expected share
                    earned back, received six months after the year with the MLR settlement
    dp_pmpm         directed payments PMPM in year 1 (pass-through: revenue and medical), growing
                    at dp_trend; included in the MLR numerator and denominator
    rule            "floor": remit max(0, floor x revenue - medical); "corridor": symmetric risk
                    corridor around target_mlr (plan keeps all within +/- band_1, shares beyond)
    Every neutral setting (acuity 1, withhold 0, directed payments 0, rule floor) is the v3 model.
    """
    if not feature(g, "medicaid_contract_terms"):
        return None
    c = a["contract_terms"]
    Y = a["projection_years"]
    path = list(c["acuity_shift"]["medical_factor_by_year"]) + [1.0] * Y
    return dict(acuity=np.array(path[:Y], dtype=float), lag=int(c["acuity_shift"]["risk_adjustment_lag_years"]),
                capture=a["risk_adjustment"]["revenue_capture"],
                withhold=c["quality_withhold"]["withhold_pct"], earn_back=c["quality_withhold"]["expected_earn_back"],
                dp_pmpm=c["directed_payments"]["pmpm_year1"], dp_trend=c["directed_payments"]["annual_trend"],
                rule=c["mlr_rule"]["type"], corridor=c["mlr_rule"].get("corridor", {}))


def mlr_settlement(revenue, medical, a: dict, ct: dict):
    """Amount paid to the state for a contract year (negative = paid to the plan).

    floor:    max(0, floor x revenue - medical)
    corridor: around target T with bands b1 < b2 and plan shares s2 (between b1 and b2) and s3
              (beyond b2): the state takes (1 - share) of the gain when the MLR is below T - b1,
              and pays (1 - share) of the loss when it is above T + b1. Symmetric.
    """
    if ct is None or ct["rule"] == "floor":
        return np.maximum(0.0, a["mlr_floor"] * revenue - medical)
    if ct["rule"] != "corridor":
        raise ValueError(f"unknown MLR rule {ct['rule']!r}")
    co = ct["corridor"]
    T, b1, b2, s2, s3 = co["target_mlr"], co["band_1"], co["band_2"], co["plan_share_band_2"], co["plan_share_beyond"]
    m = medical / revenue
    gain = (1 - s2) * np.clip(T - b1 - m, 0.0, b2 - b1) + (1 - s3) * np.maximum(T - b2 - m, 0.0)
    loss = (1 - s2) * np.clip(m - T - b1, 0.0, b2 - b1) + (1 - s3) * np.maximum(m - T - b2, 0.0)
    return revenue * (gain - loss)


def project_medicaid(a: dict, g: dict, renewed=None) -> dict:
    """Medicaid managed care contract NPV for a new state launch, monthly.

    renewed=None -> expected value (years after the term weighted by the renewal probability);
    renewed=bool array -> one Monte Carlo outcome per scenario.

    Membership is an open group per rate cell: existing members disenroll at the cell's monthly
    churn rate and new members join evenly through the year in the joiner mix. Joiners are
    exposed to churn from the month after they arrive, so each cell converges to its own steady
    state (monthly joiners / monthly churn rate).

    MLR floor: the plan remits max(0, floor x revenue - medical) for each contract year. It keeps
    adverse claim experience but gives back favorable experience below the floor, so it is short
    an option on medical cost. A best-estimate run sits above the floor and shows little or no
    remittance; the expected cost of the floor appears only in the Monte Carlo.
    """
    Y = a["projection_years"]; K = 12 * Y
    i = _f(g["discount_rate"])
    names, share, cap_rel, med_rel, chn_rel = medicaid_cells(a, g)
    ra_rev, ra_med = medicaid_risk_adjustment(a, g)
    dis = _f(a["annual_disenrollment"])
    rate_upd, trend, mcr = _f(a["annual_rate_update"]), _f(a["claim_trend"]), _f(a["medical_cost_ratio_year1"])
    ra_rev, ra_med = _f(ra_rev), _f(ra_med)
    shape = np.broadcast(dis, rate_upd, trend, mcr, i, ra_rev, ra_med).shape
    tdev, rdev = _path(a, "trend"), _path(a, "rate")
    ct = medicaid_terms(a, g)
    C = len(names)
    # Monthly churn per cell; the annual rate is capped at 100% for stressed scenarios.
    q = [a2m(np.minimum(dis * chn_rel[c], 1.0)) for c in range(C)]
    new = [a["annual_new_members"] / 12 * share[c] for c in range(C)]
    n = [_f(a["starting_members"]) * share[c] * np.ones(shape) for c in range(C)]
    rec = {k: [] for k in ["year", "month", "members_start", "leavers", "joiners", "member_months", "revenue",
                           "medical", "premium_tax", "admin", "pv_operating"]}
    for c in range(C):
        rec[f"members_{names[c]}"] = []
    yearly = {k: [] for k in ["year", "revenue", "medical", "mlr_remittance", "pv_remittance", "pv_operating", "prob_in_force", "withhold_earned"]}
    rev_y = med_y = pv_y = capg_y = 0.0
    for k in range(K):
        t, m = k // 12 + 1, k % 12 + 1
        # Capitation moves with the state's annual rate update; medical cost with claim trend.
        cap = a["capitation_pmpm_year1"] * growth_index(rate_upd, t, rdev)
        # Year-1 medical carries an acuity load: a new state's first-year population has unmanaged
        # pent-up need before care management takes effect.
        med_pmpm = a["capitation_pmpm_year1"] * mcr * growth_index(trend, t, tdev) * ((1 + a["new_state_acuity_load_year1"]) if t == 1 else 1.0)
        admin_pmpm = a["admin_pmpm_year1"] * (1 + g["expense_inflation"]) ** (t - 1)
        leave = [n[c] * q[c] for c in range(C)]
        # Member months: leavers and joiners each contribute half a month on average.
        expo = [n[c] - 0.5 * leave[c] + 0.5 * new[c] for c in range(C)]
        e_tot = sum(expo)
        if ct is None:
            rev = sum(expo[c] * cap_rel[c] for c in range(C)) * cap * ra_rev
            med = sum(expo[c] * cap_rel[c] * med_rel[c] for c in range(C)) * med_pmpm * ra_med
            tax, admin = rev * a["premium_tax"], e_tot * admin_pmpm
            cap_gross_m = rev
        else:
            # v4 contract terms. Acuity path (e.g. after a redetermination wave the remaining members
            # are sicker): medical x path_t, and risk adjustment recognizes it only after its lag.
            # Quality withhold: a share of capitation is held back and earned back after the year.
            # Directed payments: state-directed provider payments passed through (revenue = medical).
            acu_t = ct["acuity"][t - 1]
            ra_t = 1.0 + ct["capture"] * (ct["acuity"][t - 1 - ct["lag"]] - 1.0) if t - 1 - ct["lag"] >= 0 else 1.0
            cap_gross_m = sum(expo[c] * cap_rel[c] for c in range(C)) * cap * ra_rev * ra_t
            dp = e_tot * ct["dp_pmpm"] * (1 + ct["dp_trend"]) ** (t - 1)
            rev = cap_gross_m * (1 - ct["withhold"]) + dp
            med = sum(expo[c] * cap_rel[c] * med_rel[c] for c in range(C)) * med_pmpm * ra_med * acu_t + dp
            # Premium tax / assessment on gross capitation (including the withheld part) and on
            # directed payments.
            tax, admin = (cap_gross_m + dp) * a["premium_tax"], e_tot * admin_pmpm
        # Capitation, medical, premium tax and admin are all treated as mid-month cash flows.
        pv = (rev - med - tax - admin) * (1 + i) ** (-(k + 0.5) / 12)
        vals = [t, m, sum(n), sum(leave), sum(new) * np.ones(shape), e_tot, rev, med, tax, admin, pv] + [n[c] for c in range(C)]
        for key, val in zip(rec, vals):
            rec[key].append(val)
        rev_y = rev_y + rev; med_y = med_y + med; pv_y = pv_y + pv; capg_y = capg_y + cap_gross_m
        # This month's joiners enter the base exposed to churn from next month.
        n = [n[c] - leave[c] + new[c] for c in range(C)]
        if m == 12:
            # Contract-year close: MLR remittance on the year's results, paid at t + 0.5 years.
            if ct is None:
                remit = np.maximum(0.0, a["mlr_floor"] * rev_y - med_y)
                earn = 0.0 * remit
            else:
                # Withhold earned back on the year's gross capitation, received with the settlement.
                earn = ct["withhold"] * ct["earn_back"] * capg_y
                # Settlement to the state on the MLR revenue base (revenue received + withhold earned):
                # MLR floor remittance, or a risk corridor (negative = state pays the plan).
                remit = mlr_settlement(rev_y + earn, med_y, a, ct)
            remit = remit - earn          # net amount paid to the state at year end + 6 months
            # Probability the contract is still in force: 1 through the initial term, then the
            # renewal probability (expected value) or the scenario's 0/1 renewal outcome.
            if t <= a["contract_term_years"]:
                p_in = np.ones_like(np.asarray(pv_y, dtype=float))
            elif renewed is None:
                p_in = a["renewal_probability"] * np.ones_like(np.asarray(pv_y, dtype=float))
            else:
                p_in = np.asarray(renewed, dtype=float) * np.ones_like(np.asarray(pv_y, dtype=float))
            for key, val in zip(yearly, [t, rev_y, med_y, remit, remit * (1 + i) ** (-(t + 0.5)), pv_y, p_in, earn]):
                yearly[key].append(val)
            rev_y = med_y = pv_y = capg_y = 0.0
    out = {k: np.array(v) for k, v in rec.items()}
    out["yearly"] = {k: np.array(v) for k, v in yearly.items()}
    out["cells"] = names
    # Start-up (implementation, network build, readiness review) is spent before go-live: time 0.
    out["startup_t0"] = a["startup_cost_year1"]
    out["v_mid"] = np.array([(1 + i) ** (-(k + 0.5) / 12) for k in range(K)])
    y = out["yearly"]
    # NPV = sum over contract years of P(in force) x (PV operating result - PV remittance) - start-up.
    out["npv"] = (y["prob_in_force"] * (y["pv_operating"] - y["pv_remittance"])).sum(axis=0) - out["startup_t0"]
    # Profit-test streams, probability-weighted by the contract year in which they arise (claims
    # incurred in a year are paid, and its remittance settled, whether or not the contract renews).
    w = np.repeat(y["prob_in_force"], 12, axis=0)
    out["cf_start"] = np.zeros_like(out["revenue"])
    out["cf_mid"] = w * (out["revenue"] - out["premium_tax"] - out["admin"])
    out["incurred"] = w * out["medical"]
    out["remit_weighted"] = y["prob_in_force"] * y["mlr_remittance"]
    out["t0"] = -out["startup_t0"] * np.ones(np.asarray(out["npv"]).shape)
    return out


PROJECT = {"MEDSUPP": project_medsupp, "DENTAL_VISION": project_dv, "MEDICAID": project_medicaid}


def npv(lob: str, a: dict, g: dict) -> float:
    """Scalar cash NPV for one line under assumption set (a, g); used by the tornado and backtest."""
    return float(PROJECT[lob](a, g)["npv"])


# ----------------------------------------------------------------------------
# Profit test: cash flows -> after-tax distributable earnings (DE)
#
# Monthly, end-of-month accounting. For month k (all items may carry a scenario axis):
#   paid claims       P_k  = sum_j pattern_j x incurred_{k-j}            (mid-month)
#   claim reserve     CR_k = CR_{k-1} + incurred_k - P_k                  (unpaid claims, end of month)
#   MLR liability     RL_k = remittances accrued at each contract-year end, held until paid at +6 months
#   required capital  RC_k = capital % x 12 x paid_k                      (tapers with the claim runoff)
#   book profit       BP_k = start cash x (1+j) + (mid cash - P_k) x (1+j)^0.5
#                            - (CR_k - CR_{k-1}) - remittance accrued_k + j x (CR_{k-1} + RL_{k-1})
#   tax               T_k  = tax rate x (BP_k + j x RC_{k-1})
#   distributable     DE_k = BP_k + j x RC_{k-1} - T_k - (RC_k - RC_{k-1})
#   DE NPV = t0 x (1 - tax rate) + sum_k DE_k x v^(k+1)      (v = monthly hurdle discount)
# where j is the monthly investment earned rate. Remittance payments do not hit income (they
# release the liability). Identity (tested): tax 0, capital 0, j = hurdle and immediate payment
# give DE NPV = cash NPV exactly, because reserve and liability terms telescope to zero.
# v4.1: the IRC §848 DAC tax is deducted as a separate timing cost (dac_tax below).
# Simplifications (documented): tax reserves = statutory reserves; the §848(c)(2) general-deductions
# cap is ignored and the tax year is the policy year; tax losses used against other company income
# in the year they arise; no active-life (contract) reserve.
# ----------------------------------------------------------------------------
def profit_test(lob: str, p: dict, a: dict, g: dict, overrides: dict | None = None) -> dict:
    """Distributable-earnings projection on top of projection p. overrides: for bridge steps."""
    pt = dict(g["profit_test"]); pt.update(overrides or {})
    tau = pt["federal_income_tax_rate"]
    ie = pt["investment_earned_rate"]
    rc_pct = pt.get("required_capital_pct_of_annual_claims", a["required_capital_pct_of_annual_claims"])
    pat = np.asarray(pt.get("claim_payment_pattern", a["claim_payment_pattern"]), dtype=float)
    assert abs(pat.sum() - 1.0) < 1e-9, f"{lob}: claim payment pattern must sum to 1"
    i = _f(g["discount_rate"])
    j = (1 + ie) ** (1 / 12) - 1
    K = len(p["incurred"])
    H = K + max(len(pat) - 1, 6) + 1                     # runoff for claims and the last remittance
    scen = np.asarray(p["npv"]).shape

    def pad(x):
        x = np.asarray(x, dtype=float) * np.ones((K,) + scen)
        return np.concatenate([x, np.zeros((H - K,) + scen)], axis=0)

    S, M, C = pad(p["cf_start"]), pad(p["cf_mid"]), pad(p["incurred"])
    P = np.zeros_like(C)
    for lag, w in enumerate(pat):
        P[lag:] += w * C[:H - lag]
    CR = np.cumsum(C - P, axis=0)
    acc, pay = np.zeros_like(C), np.zeros_like(C)
    if "remit_weighted" in p:
        R = p["remit_weighted"]
        for y in range(1, len(R) + 1):
            acc[12 * y - 1] += R[y - 1]           # accrued at the contract-year end
            pay[12 * y + 5] += R[y - 1]           # paid six months later
    RL = np.cumsum(acc - pay, axis=0)
    # Capital on annualized PAID claims: it builds as claims start to be paid and runs off with the
    # claim reserve, rather than being released the month new claims stop (end of the projection,
    # or a Medicaid contract that is not renewed) while reserves are still outstanding.
    RC = rc_pct * 12 * P
    prev = lambda x: np.concatenate([np.zeros((1,) + scen), x[:-1]], axis=0)
    inv_res = j * (prev(CR) + prev(RL))
    inv_cap = j * prev(RC)
    BP = S * (1 + j) + (M - P) * (1 + j) ** 0.5 - (CR - prev(CR)) - acc + inv_res
    T = tau * (BP + inv_cap)
    DE = BP + inv_cap - T - (RC - prev(RC))
    v = (1 + i) ** (-(np.arange(H) + 1) / 12)
    v = v.reshape((H,) + (1,) * len(scen)) if scen else v
    t0 = np.asarray(p["t0"], dtype=float)
    de_npv = t0 * (1 - tau) + (DE * v).sum(axis=0)
    dac = dac_tax(lob, p, pt, tau, i, scen)
    de_npv = de_npv - dac["pv"]
    return dict(paid_claims=P, claim_reserve=CR, mlr_liability=RL, required_capital=RC, book_profit=BP,
                inv_income=inv_res + inv_cap, tax=T, de=DE, pv_de=DE * v, t0_after_tax=t0 * (1 - tau),
                incurred=C, de_npv=de_npv, months=H, dac=dac)


# IRC §848 amortization weights for amounts capitalized in tax year y, deducted in years y .. y+15:
# 180-month straight line with the half-year convention: 1/30, then 1/15 for 14 years, then 1/30.
DAC_WEIGHTS = np.array([1 / 30] + [1 / 15] * 14 + [1 / 30])


def dac_tax(lob: str, p: dict, pt: dict, tau: float, i, scen) -> dict:
    """v4.1: tax cost of the IRC §848 'DAC tax' (specified policy acquisition expenses).

    For specified insurance contracts a percentage of net premiums is capitalized each tax year and
    deducted over 180 months (half-year convention), instead of being deducted when incurred. It is a
    TIMING cost: extra tax now, less tax later. Rate by line in global.profit_test.dac_tax_rate_by_line
    (Med Supp, a guaranteed-renewable A&H contract, 'other' specified contracts: 9.2%; D&V excepted-
    benefit dental treated as cancellable and Medicaid capitation as outside §848: 0). Tax year = policy
    / contract year; the extra tax of year t, tau x (capitalized_t - amortized_t), is paid at the end of
    year t and discounted at the hurdle rate, over the projection and the 15 amortization years after it.
    Net premiums = gross premiums (no return premiums or reinsurance in the model).
    """
    rate = float(pt.get("dac_tax_rate", pt.get("dac_tax_rate_by_line", {}).get(lob, 0.0)))
    Y = int(len(p["year"])) // 12 if "year" in p else 0
    if rate == 0.0 or "premium" not in p:
        z = np.zeros((1,) + scen)
        return dict(rate=rate, capitalized=z, amortized=z, extra_tax=z, pv=np.zeros(scen) if scen else 0.0)
    prem = np.asarray(p["premium"], dtype=float)
    cap = rate * prem.reshape((Y, 12) + prem.shape[1:]).sum(axis=1)          # by tax year, (Y, scen)
    T_ = Y + len(DAC_WEIGHTS) - 1
    amort = np.zeros((T_,) + cap.shape[1:])
    for y in range(Y):
        amort[y:y + len(DAC_WEIGHTS)] += np.multiply.outer(DAC_WEIGHTS, cap[y]) if cap.ndim > 1 else DAC_WEIGHTS * cap[y]
    capf = np.concatenate([cap, np.zeros((T_ - Y,) + cap.shape[1:])], axis=0)
    extra = tau * (capf - amort)
    disc = (1 + i) ** (-np.arange(1, T_ + 1))
    disc = disc.reshape((T_,) + (1,) * (extra.ndim - 1))
    return dict(rate=rate, capitalized=capf, amortized=amort, extra_tax=extra, pv=(extra * disc).sum(axis=0))


def de_npv(lob: str, a: dict, g: dict, renewed=None, overrides=None) -> float:
    """Scalar DE NPV for one line (used by the tornado and the bridge)."""
    p = project_medicaid(a, g, renewed) if lob == "MEDICAID" else PROJECT[lob](a, g)
    return float(profit_test(lob, p, a, g, overrides)["de_npv"])


def profit_table(lob: str, pt: dict) -> pd.DataFrame:
    """Annual view of the profit test (runoff months fall into the year after the projection)."""
    H = pt["months"]
    yr = np.arange(H) // 12 + 1
    f = lambda x: pd.Series(np.asarray(x)).groupby(yr).sum().values
    end = lambda x: pd.Series(np.asarray(x)).groupby(yr).last().values
    df = pd.DataFrame({"year": np.unique(yr), "incurred_claims": f(pt["incurred"]), "paid_claims": f(pt["paid_claims"]),
                       "claim_reserve_end": end(pt["claim_reserve"]), "mlr_liability_end": end(pt["mlr_liability"]),
                       "required_capital_end": end(pt["required_capital"]), "investment_income": f(pt["inv_income"]),
                       "book_profit_pre_tax": f(pt["book_profit"]), "federal_tax": f(pt["tax"]),
                       "distributable_earnings": f(pt["de"]), "pv_distributable_earnings": f(pt["pv_de"])})
    # Time-0 acquisition / start-up (after tax) belongs to year 1.
    df.loc[0, "distributable_earnings"] += float(pt["t0_after_tax"])
    df.loc[0, "pv_distributable_earnings"] += float(pt["t0_after_tax"])
    df["cumulative_pv_de"] = df["pv_distributable_earnings"].cumsum()
    df.attrs["pv_dac_tax"] = float(np.asarray(pt["dac"]["pv"]))
    return df


# ----------------------------------------------------------------------------
# Annual summary tables (what the workbook's annual sheets show)
#
# The monthly projection is the model; the annual table is a presentation of it. Each year's PV
# profit is the sum of that year's discounted monthly cash, with acquisition / start-up in year 1
# at time 0, so the column adds back to the monthly NPV exactly. main() asserts this.
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
        end = np.r_[start[1:], p["in_force_start"][-1] - (p.get("deaths", np.zeros_like(yr))[-1] + p["lapses"][-1])]
        acq = np.r_[p["acquisition_t0"], np.zeros(Y - 1)]
        df = pd.DataFrame({"year": np.arange(1, Y + 1), "in_force_start": start, "in_force_end": end,
                           "policy_years_exposure": by_year(p["exposure"]) / 12,
                           "premium": by_year(p["premium"]), "claims": by_year(p["claims"]),
                           "commission": by_year(p["commission"]), "premium_tax": by_year(p["premium_tax"]),
                           "maintenance": by_year(p["maintenance"]), "acquisition": acq})
        df["profit"] = df.premium - df.claims - df.commission - df.premium_tax - df.maintenance - df.acquisition
        df["pv_profit"] = by_year(p["pv_cash"]) - acq
        df["pv_premium"] = by_year(p["premium"] * p["v_prem"])
        df["pv_claims"] = by_year(p["claims"] * p["v_mid"])
        if "in_force_shoppers" in p:
            df["shoppers_share_start"] = p["in_force_shoppers"][::12] / start
            df["claim_relativity_month1"] = p["claim_relativity"][::12]
    else:
        y = p["yearly"]
        Y = len(y["year"])
        start = p["members_start"][::12]
        end = np.r_[start[1:], p["members_start"][-1] - p["leavers"][-1] + p["joiners"][-1]]
        df = pd.DataFrame({"year": y["year"], "members_start": start, "members_end": end,
                           "member_months": by_year(p["member_months"]), "revenue": y["revenue"], "medical": y["medical"],
                           "mlr_remittance": y["mlr_remittance"], "premium_tax": by_year(p["premium_tax"]),
                           "admin": by_year(p["admin"]), "startup": np.r_[p["startup_t0"], np.zeros(Y - 1)]})
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
        for c in p["cells"]:
            if c != "ALL":
                df[f"members_start_{c}"] = p[f"members_{c}"][::12]
    df["cumulative_pv_profit"] = df["pv_profit"].cumsum()
    if "premium" in df:
        df["loss_ratio"] = df.claims / df.premium
    return df


def units(lob: str) -> float:
    return A[lob]["starting_members"] if lob == "MEDICAID" else A[lob]["cohort_size"]


def summarize(lob: str, tab: pd.DataFrame, ptab: pd.DataFrame | None = None) -> dict:
    """Headline metrics from the annual table (and the profit-test table when given).

    PV loss ratio and NPV margin are ratios of present values (not averages of annual ratios).
    Break-even year is the first year in which cumulative PV profit turns positive (discounted
    payback); None if never.
    """
    pv_rev = tab["pv_premium" if "pv_premium" in tab else "pv_revenue"].sum()
    pv_clm = tab["pv_claims"].sum()
    npv_ = tab["pv_profit"].sum()
    cum = tab["cumulative_pv_profit"]
    be = int(tab.loc[cum > 0, "year"].min()) if (cum > 0).any() else None
    out = dict(lob=lob, npv=npv_, pv_revenue=pv_rev, pv_claims=pv_clm, pv_loss_ratio=pv_clm / pv_rev,
               npv_margin=npv_ / pv_rev, breakeven_year=be, npv_per_unit=npv_ / units(lob))
    if ptab is not None:
        de = ptab["pv_distributable_earnings"].sum() - ptab.attrs.get("pv_dac_tax", 0.0)
        cde = ptab["cumulative_pv_de"]
        out.update(de_npv=de, de_margin=de / pv_rev, de_per_unit=de / units(lob),
                   de_breakeven_year=int(ptab.loc[cde > 0, "year"].min()) if (cde > 0).any() else None,
                   peak_required_capital=float(ptab["required_capital_end"].max()))
    return out


# ----------------------------------------------------------------------------
# Sensitivities (tornado): one assumption shocked at a time
#
# Each shock deep-copies the assumptions and reruns the production projection, so a sensitivity
# is the same model with one input changed. Keys may be nested ("antiselection.x"). The Excel
# Sens_Calc sheet runs the same scenarios and the reconcile script ties them out.
# ----------------------------------------------------------------------------
def _apply(a: dict, g: dict, key: str, how: str, val: float):
    # Global keys: the discount rate and the profit-test block (e.g. profit_test.investment_earned_rate).
    target = g if (key == "discount_rate" or key.startswith("profit_test.")) else a
    *path, leaf = key.split(".")
    for pth in path:
        target = target[pth]
    if how == "mult_list":
        target[leaf] = [x * val for x in target[leaf]]
    elif how == "add":
        target[leaf] = target[leaf] + val
    elif how == "mult":
        target[leaf] = target[leaf] * val
    elif how == "set":
        target[leaf] = val


def _has(a: dict, key: str) -> bool:
    for k_ in key.split("."):
        if not isinstance(a, dict) or k_ not in a:
            return False
        a = a[k_]
    return True


def shocked(lob: str, key: str, how: str, val: float, measure: str = "cash") -> float:
    """NPV with one assumption shocked: 'mult_list' scales every duration, 'add' / 'mult' / 'set' scalars."""
    a = copy.deepcopy(A[lob]); g = copy.deepcopy(A["global"])
    _apply(a, g, key, how, val)
    return npv(lob, a, g) if measure == "cash" else de_npv(lob, a, g)


# (label, assumption key, shock type, favorable-side value, adverse-side value).
# Shock sizes are illustrative judgment ranges, not calibrated percentiles, so the chart ranks
# exposure to each driver rather than equal-probability outcomes.
# "Claim-cost level" shocks move the STARTING claim cost; trend carries the change into every
# later year, so the swing is a lifetime effect, not a year-1 effect. The only genuinely
# year-1-only driver is the D&V pent-up factor.
SENS = {
    "MEDSUPP": [("Lapse rates x0.8 / x1.2", "lapse_by_duration", "mult_list", 0.8, 1.2),
                ("Claim trend -1% / +1%", "claim_trend", "add", -0.01, 0.01),
                ("Rate increases +1% / -1%", "annual_rate_increase", "add", 0.01, -0.01),
                ("Discount rate -2% / +2%", "discount_rate", "add", -0.02, 0.02),
                ("Claim-cost level -5% / +5% (all years)", "annual_claim_cost_year1_ultimate", "mult", 0.95, 1.05),
                ("Anti-selection: shopper cost 0.95 / 0.75", "antiselection.shopper_relative_claim_cost", "set", 0.95, 0.75)],
    "DENTAL_VISION": [("Lapse rates x0.8 / x1.2", "lapse_by_duration", "mult_list", 0.8, 1.2),
                      ("Pent-up factor 1.15 / 1.40 (year 1 only)", "first_year_pent_up_factor", "add", -0.13, 0.12),
                      ("Claim trend -1% / +1%", "claim_trend", "add", -0.01, 0.01),
                      ("Discount rate -2% / +2%", "discount_rate", "add", -0.02, 0.02),
                      ("Claim-cost level -5% / +5% (all years)", "annual_claim_cost_year1_ultimate", "mult", 0.95, 1.05)],
    "MEDICAID": [("Rate update +1% / -1%", "annual_rate_update", "add", 0.01, -0.01),
                 ("Claim trend -1% / +1%", "claim_trend", "add", -0.01, 0.01),
                 ("Starting medical cost ratio -2pt / +2pt (all years)", "medical_cost_ratio_year1", "add", -0.02, 0.02),
                 ("Renewal probability 100% / 50%", "renewal_probability", "add", 0.25, -0.25),
                 ("Disenrollment x0.8 / x1.2", "annual_disenrollment", "mult", 0.8, 1.2),
                 ("Plan acuity -3% / +3% (80% risk-adjusted)", "risk_adjustment.plan_acuity", "add", -0.03, 0.03)],
}


def tornado() -> pd.DataFrame:
    """Base, favorable and adverse NPV (cash and DE) for every driver; swing = |favorable - adverse|."""
    rows = []
    for lob, items in SENS.items():
        base = npv(lob, A[lob], A["global"])
        base_de = de_npv(lob, A[lob], A["global"])
        for name, key, how, lo, hi in items:
            rows.append(dict(lob=lob, driver=name, base_npv=base,
                             npv_favorable_side=shocked(lob, key, how, lo),
                             npv_adverse_side=shocked(lob, key, how, hi),
                             base_de_npv=base_de,
                             de_npv_favorable_side=shocked(lob, key, how, lo, "de"),
                             de_npv_adverse_side=shocked(lob, key, how, hi, "de")))
    df = pd.DataFrame(rows)
    df["swing"] = (df["npv_favorable_side"] - df["npv_adverse_side"]).abs()
    df["de_swing"] = (df["de_npv_favorable_side"] - df["de_npv_adverse_side"]).abs()
    return df


# ----------------------------------------------------------------------------
# Monte Carlo (parameter uncertainty). Four standard-normal drivers per scenario:
#   lapse level (lognormal multiplier, Med Supp and D&V), claim trend (shared by all lines: one
#   cost environment), Med Supp rate-increase shortfall, Medicaid rate update.
# v3: the drivers are correlated through the matrix in npv_assumptions.json (rate actions partly
# follow emerging trend), and with dynamic shock lapse a rate shortfall also lowers shock lapse.
# With correlated_monte_carlo off the draws are independent and identical to version 2.
#
# Each draw is a level shift held for the whole projection (a wrong assumption), not
# year-to-year volatility. Randomness of outcomes given the assumptions (process risk) is
# measured separately by the backtest's Test A.
# ----------------------------------------------------------------------------
def correlation_factor(g: dict, mc: dict) -> np.ndarray:
    """Cholesky factor of the driver correlation matrix (identity when the feature is off)."""
    if not feature(g, "correlated_monte_carlo"):
        return np.eye(4)
    R = np.array(mc["correlations"]["matrix"], dtype=float)
    assert np.allclose(R, R.T) and np.allclose(np.diag(R), 1.0), "correlation matrix must be symmetric with unit diagonal"
    return np.linalg.cholesky(R)          # raises if the matrix is not positive definite


def mc_draws(g: dict, mc: dict, n: int | None = None):
    """Correlated driver draws. The order of random numbers matches version 2 exactly."""
    rng = np.random.default_rng(mc["seed"])
    n = n or mc["n_sims"]
    Z = rng.standard_normal((4, n))
    Lc = correlation_factor(g, mc)
    if not np.array_equal(Lc, np.eye(4)):
        Z = Lc @ Z
    s = mc["lapse_multiplier_sd"]
    # Lognormal lapse multiplier with mu = -sigma^2 / 2, so E[multiplier] = 1.
    lapse_mult = np.exp(-0.5 * s ** 2 + s * Z[0])
    trend_shock = mc["trend_shock_sd"] * Z[1]
    # Approved Med Supp increase minus the assumed one, with the sign flipped (positive = approved
    # below the assumption). Symmetric: the assumption is the expected approval.
    rate_short = mc["medsupp_rate_increase_deviation_sd"] * Z[2]
    md_rate = mc["medicaid_rate_update_sd"] * Z[3]
    renewed = rng.random(n) < mc["medicaid_renewal_prob"]
    return lapse_mult, trend_shock, rate_short, md_rate, renewed


def process_paths(g: dict, mc: dict, n: int, years: int):
    """v4 year-to-year process risk: deviation paths of shape (years, n), or zeros when off.

    trend       stationary AR(1) deviation of each year's claim trend from the scenario's level:
                d_1 = sd e_1, d_s = phi d_(s-1) + sd sqrt(1 - phi^2) e_s (one cost environment, shared
                by every line). The level shift drawn in mc_draws stays on top (parameter risk).
    ms_approval independent year-to-year noise in the approved Med Supp increase (state approvals).
    md_update   independent year-to-year noise in the Medicaid rate update (rate-setting cycles).
    A separate random stream (seed + seed_offset) keeps the version 2 / v3 draws in mc_draws
    bit-identical, so switching the feature on adds risk without re-drawing the parameter risk.
    """
    z = np.zeros((years, n))
    if not feature(g, "mc_process_risk"):
        return dict(trend=z, ms_approval=z.copy(), md_update=z.copy())
    pr = mc["process_risk"]
    rng = np.random.default_rng(mc["seed"] + pr["seed_offset"])
    e = rng.standard_normal((3, years, n))
    sd, phi = pr["trend_sd_annual"], pr["trend_ar1_phi"]
    tr = np.zeros((years, n))
    tr[0] = sd * e[0, 0]
    for s_ in range(1, years):
        tr[s_] = phi * tr[s_ - 1] + sd * np.sqrt(1 - phi ** 2) * e[0, s_]
    return dict(trend=tr, ms_approval=pr["medsupp_approval_noise_sd"] * e[1],
                md_update=pr["medicaid_rate_update_noise_sd"] * e[2])


def management_rate_path(rate, base_trend, trend_dev, noise, resp: dict | None):
    """Rate-increase deviation path (years, n): partial-adjustment filing response plus noise.

    Without a response the deviation is the noise alone. With one, each filing closes a share h of
    the gap between the claim-cost index deviation the actuary can see (experience lagged L years)
    and the premium index deviation already in force:
        log(1 + r + rho_s) - log(1 + r) = h x (D_(s+1-L) - P_s)  [+ noise_s]
        D_t = sum_(u < t) log((1 + tau + delta_u) / (1 + tau)),   P_s = premium deviations before step s
    tau is the PRICED (assumed) trend and delta_u the scenario's total deviation from it in year u
    (parameter level shift plus process deviation): the filing actuary sees both in experience.
    where step s is the increase applied at the start of year s + 1. With h = 0 the path is the
    noise, so the feature is neutral at h = 0 (tested). This replaces the -0.5 trend / rate
    shortfall correlation of v3, which was a proxy for the same behaviour: both must not be on
    together (checked in monte_carlo()).
    """
    Y = noise.shape[0]
    if resp is None:
        return noise
    h, L = resp["gap_closure_per_filing"], int(resp["lag_years"])
    D = np.zeros((Y + 1,) + noise.shape[1:])                  # D[t-1] = claim deviation of year t
    for t in range(2, Y + 2):
        D[t - 1] = D[t - 2] + np.log((1 + base_trend + trend_dev[t - 2]) / (1 + base_trend))
    rho = np.zeros_like(noise); P = np.zeros(noise.shape[1:])
    for s_ in range(1, Y + 1):
        known = s_ + 1 - L
        gap = (D[known - 1] if known >= 1 else 0.0) - P
        rho[s_ - 1] = (1 + rate) * np.exp(h * gap) - 1 - rate + noise[s_ - 1]
        P = P + np.log((1 + rate + rho[s_ - 1]) / (1 + rate))
    return rho


def monte_carlo(no_floor: bool = False, assumptions: dict | None = None) -> pd.DataFrame:
    """Vectorized NPV distribution by line (cash NPV columns MEDSUPP..., DE NPV columns *_DE).

    no_floor=True reruns Medicaid with the MLR floor removed. The fixed seed gives the no-floor run
    the identical draws (common random numbers), so the difference in mean is the floor's cost.
    """
    AA = assumptions or A
    mc = AA["monte_carlo"]; g = AA["global"]
    lapse_mult, trend_shock, rate_short, md_rate, renewed = mc_draws(g, mc)
    n = len(lapse_mult)
    proc_on, resp_on = feature(g, "mc_process_risk"), feature(g, "mc_management_response")
    if resp_on and feature(g, "correlated_monte_carlo"):
        R = np.array(mc["correlations"]["matrix"], dtype=float)
        # The explicit filing response replaces the trend / Med Supp rate-shortfall correlation.
        assert R[1, 2] == 0.0 and R[2, 1] == 0.0, ("mc_management_response is on: set the trend / Med Supp "
                                                    "rate-shortfall correlation to 0 so the response is not counted twice")
    Ymax = max(AA[l]["projection_years"] for l in PROJECT)
    pp = process_paths(g, mc, n, Ymax)
    resp = mc.get("management_response", {}) if resp_on else {}
    out = {}

    a = copy.deepcopy(AA["MEDSUPP"])
    a["lapse_by_duration"] = [x * lapse_mult for x in a["lapse_by_duration"]]
    a["claim_trend"] = a["claim_trend"] + trend_shock
    if proc_on or resp_on:
        Y = a["projection_years"]
        tdev = pp["trend"][:Y]
        # Level shortfall (parameter risk) plus year-to-year approval noise, then the filing response.
        noise = -rate_short * np.ones((Y, n)) - pp["ms_approval"][:Y]
        a["_paths"] = dict(trend=tdev, rate=management_rate_path(a["annual_rate_increase"], AA["MEDSUPP"]["claim_trend"],
                                                                  tdev + trend_shock, noise, resp.get("MEDSUPP")))
    else:
        a["annual_rate_increase"] = a["annual_rate_increase"] - rate_short
    p = project_medsupp(a, g)
    out["MEDSUPP"] = p["npv"]; out["MEDSUPP_DE"] = profit_test("MEDSUPP", p, a, g)["de_npv"]

    a = copy.deepcopy(AA["DENTAL_VISION"])
    a["lapse_by_duration"] = [np.minimum(x * lapse_mult, 0.95) for x in a["lapse_by_duration"]]
    a["claim_trend"] = a["claim_trend"] + trend_shock
    if proc_on or resp_on:
        Y = a["projection_years"]
        tdev = pp["trend"][:Y]
        a["_paths"] = dict(trend=tdev, rate=management_rate_path(a["annual_rate_increase"], AA["DENTAL_VISION"]["claim_trend"],
                                                                  tdev + trend_shock, np.zeros((Y, n)), resp.get("DENTAL_VISION")))
    p = project_dv(a, g)
    out["DENTAL_VISION"] = p["npv"]; out["DENTAL_VISION_DE"] = profit_test("DENTAL_VISION", p, a, g)["de_npv"]

    a = copy.deepcopy(AA["MEDICAID"])
    a["claim_trend"] = a["claim_trend"] + trend_shock
    a["annual_rate_update"] = a["annual_rate_update"] + md_rate
    if proc_on:
        Y = a["projection_years"]
        a["_paths"] = dict(trend=pp["trend"][:Y], rate=pp["md_update"][:Y])
    if no_floor:
        a["mlr_floor"] = 0.0
        if "contract_terms" in a:
            a["contract_terms"] = copy.deepcopy(a["contract_terms"]); a["contract_terms"]["mlr_rule"]["type"] = "floor"
    p = project_medicaid(a, g, renewed=renewed)
    out["MEDICAID"] = p["npv"]; out["MEDICAID_DE"] = profit_test("MEDICAID", p, a, g)["de_npv"]
    return pd.DataFrame(out)


def mc_attribution() -> pd.DataFrame:
    """v4: how much each Monte Carlo layer changes the distribution (same parameter draws throughout).

    Steps: v3 basis (level shifts, v3 correlations) -> + year-to-year process risk -> + rate-filing
    response (v4 basis), then the response strength at 0 / 0.25 / 0.75 so the judgment value's
    influence is visible. The v3 basis restores the v3 trend / rate-shortfall correlation (-0.5),
    because without the explicit response that correlation is the v3 proxy for it.
    """
    rows = []
    def run(label, AA):
        mc_ = monte_carlo(assumptions=AA)
        for col in mc_:
            rows.append(dict(step=label, measure=col, mean=mc_[col].mean(), P5=np.percentile(mc_[col], 5),
                             P95=np.percentile(mc_[col], 95), prob_negative=(mc_[col] < 0).mean()))
    v3 = copy.deepcopy(A); v3["global"]["features"].update(mc_process_risk=False, mc_management_response=False)
    R = v3["monte_carlo"]["correlations"]["matrix"]; R[1][2] = R[2][1] = V3_TREND_RATE_CORR
    run("1. v3 basis (level shifts, v3 correlations)", v3)
    p_ = copy.deepcopy(v3); p_["global"]["features"]["mc_process_risk"] = True
    run("2. + year-to-year process risk", p_)
    run("3. + rate-filing response (v4 basis)", A)
    for h in (0.0, 0.25, 0.75):
        a_ = copy.deepcopy(A)
        for l in a_["monte_carlo"]["management_response"]:
            if not l.startswith("_"):
                a_["monte_carlo"]["management_response"][l]["gap_closure_per_filing"] = h
        run(f"   sensitivity: gap closure per filing = {h:.2f}", a_)
    return pd.DataFrame(rows)


def medsupp_portfolio(measure_de: bool = True) -> pd.DataFrame:
    """v4 Med Supp new-business portfolio from model points (issue-age band x plan).

    Each model point is the base engine with its issue age and plan relativities, so it carries
    the same lapse, anti-selection, selection and profit-test mechanics. The base cell (Plan G at
    65) must reproduce the headline cohort NPV exactly (asserted). Results are per 1,000 policies
    in each cell and mix-weighted per 1,000 policies of the whole new-business portfolio.
    """
    mp = A["MEDSUPP"]["model_points"]; g = A["global"]
    tot = sum(v for b in mp["mix"].values() for v in b.values())
    assert abs(tot - 1.0) < 1e-6, f"model-point mix sums to {tot}, not 1"
    rows = []
    for b in mp["bands"]:
        for plan, rel in mp["plans"].items():
            w = mp["mix"][b["band"]].get(plan, 0.0)
            a = copy.deepcopy(A["MEDSUPP"])
            x = b["issue_age"]
            a["issue_age"] = x
            a["annual_premium_year1"] = a["annual_premium_year1"] * (1 + a["premium_age_step"]) ** (x - 65) * rel["premium_relativity"]
            a["annual_claim_cost_year1_ultimate"] = a["annual_claim_cost_year1_ultimate"] * (1 + a["claim_age_slope"]) ** (x - 65) * rel["claim_relativity"]
            p = project_medsupp(a, g)
            cash = float(p["npv"])
            de = float(profit_test("MEDSUPP", p, a, g)["de_npv"]) if measure_de else np.nan
            pv_prem = float((p["premium"] * p["v_prem"]).sum()); pv_clm = float((p["claims"] * p["v_mid"]).sum())
            rows.append(dict(band=b["band"], issue_age=x, plan=plan, mix=w, cash_npv_per_1000=cash, de_npv_per_1000=de,
                             pv_loss_ratio=pv_clm / pv_prem, npv_margin=cash / pv_prem))
    df = pd.DataFrame(rows)
    base = df[(df.band == "65") & (df.plan == "G")].iloc[0]
    assert abs(base.cash_npv_per_1000 - npv("MEDSUPP", A["MEDSUPP"], g)) < 1e-6, "base model point must equal the headline cohort"
    tot_row = dict(band="PORTFOLIO", issue_age=float((df.mix * df.issue_age).sum()), plan="mix", mix=df.mix.sum(),
                   cash_npv_per_1000=float((df.mix * df.cash_npv_per_1000).sum()),
                   de_npv_per_1000=float((df.mix * df.de_npv_per_1000).sum()), pv_loss_ratio=np.nan, npv_margin=np.nan)
    return pd.concat([df, pd.DataFrame([tot_row])], ignore_index=True)


def _mp_cell(band: dict, plan_rel: dict, a0: dict, basis: str, prem1=None, rate_inc=None) -> dict:
    """Assumptions of one Med Supp model point under a rating basis (v4.1)."""
    a = copy.deepcopy(a0)
    x = band["issue_age"]
    a["issue_age"] = x
    a["annual_claim_cost_year1_ultimate"] = a0["annual_claim_cost_year1_ultimate"] * (1 + a0["claim_age_slope"]) ** (x - 65) * plan_rel["claim_relativity"]
    if basis == "attained_age":
        a["annual_premium_year1"] = a0["annual_premium_year1"] * (1 + a0["premium_age_step"]) ** (x - 65) * plan_rel["premium_relativity"]
    else:
        # Issue-age and community rating: no premium step with attained age after issue.
        a["premium_age_step"] = 0.0
        a["annual_premium_year1"] = prem1 * plan_rel["premium_relativity"]
        a["annual_rate_increase"] = rate_inc
        a["dynamic_shock_lapse"]["reference_rate_increase"] = rate_inc
    return a


def _pv_lr(a: dict, g: dict) -> tuple[float, float, float]:
    p = project_medsupp(a, g)
    return float((p["claims"] * p["v_mid"]).sum()), float((p["premium"] * p["v_prem"]).sum()), float(p["npv"])


def medsupp_rating_bases() -> pd.DataFrame:
    """v4.1: the Med Supp portfolio under the three rating bases states allow.

    GA, AZ, FL and MO prohibit attained-age rating (KFF); some states require community rating.
      attained_age  the base: premium steps with attained age (premium_age_step), filed increases
                    derived for consistency with trend and aging.
      issue_age     premium fixed by issue age (no attained-age step); aging is pre-funded in the
                    initial premium, so filed increases cover cost trend only (rate = claim trend).
                    The year-1 premium of each issue-age band is solved so the cell's lifetime PV
                    loss ratio equals the attained-age cell's (actuarial equivalence, status D).
      community     one premium for every issue age (no age step); increases = claim trend; the
                    single year-1 premium is solved so the PORTFOLIO PV loss ratio equals the
                    attained-age portfolio's. Young issues subsidize old ones.
    Plan relativities, lapse, mortality, selection and anti-selection as in the base. Equal PV loss
    ratios make the bases comparable: the NPV differences come from timing and persistency (lapse
    removes policies before or after their premium has pre-funded aging).
    """
    from scipy.optimize import brentq
    mp = A["MEDSUPP"]["model_points"]; g = A["global"]; a0 = A["MEDSUPP"]
    cells = [(b, pl, rel, mp["mix"][b["band"]].get(pl, 0.0)) for b in mp["bands"] for pl, rel in mp["plans"].items()]
    att = {(b["band"], pl): _pv_lr(_mp_cell(b, rel, a0, "attained_age"), g) for b, pl, rel, w in cells}
    r_trend = a0["claim_trend"]
    rows = []
    for basis in ("attained_age", "issue_age", "community"):
        prem_by_band = {}
        if basis == "issue_age":
            for b in mp["bands"]:
                rel = mp["plans"]["G"]
                target = att[(b["band"], "G")][0] / att[(b["band"], "G")][1]
                f = lambda P: (lambda c, pr, n: c / pr - target)(*_pv_lr(_mp_cell(b, rel, a0, basis, P, r_trend), g))
                prem_by_band[b["band"]] = brentq(f, 200.0, 20000.0, xtol=1e-6)
        elif basis == "community":
            target = sum(w * att[(b["band"], pl)][0] for b, pl, rel, w in cells) / sum(w * att[(b["band"], pl)][1] for b, pl, rel, w in cells)
            def f(P):
                vals = [(w, _pv_lr(_mp_cell(b, rel, a0, basis, P, r_trend), g)) for b, pl, rel, w in cells]
                return sum(w * v[0] for w, v in vals) / sum(w * v[1] for w, v in vals) - target
            P = brentq(f, 200.0, 20000.0, xtol=1e-6)
            prem_by_band = {b["band"]: P for b in mp["bands"]}
        for b, pl, rel, w in cells:
            if basis == "attained_age":
                c, pr, n = att[(b["band"], pl)]
                prem1 = a0["annual_premium_year1"] * (1 + a0["premium_age_step"]) ** (b["issue_age"] - 65) * rel["premium_relativity"]
                ri = a0["annual_rate_increase"]
            else:
                c, pr, n = _pv_lr(_mp_cell(b, rel, a0, basis, prem_by_band[b["band"]], r_trend), g)
                prem1 = prem_by_band[b["band"]] * rel["premium_relativity"]
                ri = r_trend
            rows.append(dict(basis=basis, band=b["band"], plan=pl, mix=w, year1_premium=prem1, annual_rate_increase=ri,
                             pv_claims=c, pv_premium=pr, pv_loss_ratio=c / pr, cash_npv_per_1000=n))
    df = pd.DataFrame(rows)
    # Portfolio loss ratio = ratio of mix-weighted PV sums (the quantity the community premium is solved on).
    port = df.groupby("basis").apply(lambda d: pd.Series({"cash_npv_per_1000": float((d.mix * d.cash_npv_per_1000).sum()),
                                                          "pv_loss_ratio": float((d.mix * d.pv_claims).sum() / (d.mix * d.pv_premium).sum())}),
                                     include_groups=False).reset_index()
    port["band"] = "PORTFOLIO"; port["plan"] = "mix"; port["mix"] = 1.0
    return pd.concat([df, port], ignore_index=True)


# v3's trend / Med Supp rate-shortfall correlation, restored only for the attribution's v3 basis.
V3_TREND_RATE_CORR = -0.5

# Break-even drivers: (label, key, how, base-relative bracket). Break-even = the value of the driver
# at which NPV = 0, everything else at base. 'add' drivers report the absolute level; 'mult' and
# 'mult_list' report the multiplier.
BREAKEVEN = {
    "MEDSUPP": [("Claim trend", "claim_trend", "add", (-0.10, 0.10)),
                ("Annual rate increase", "annual_rate_increase", "add", (-0.10, 0.10)),
                ("Claim-cost level (multiplier, all years)", "annual_claim_cost_year1_ultimate", "mult", (0.5, 2.0)),
                ("Lapse multiplier", "lapse_by_duration", "mult_list", (0.05, 5.0))],
    "DENTAL_VISION": [("Claim trend", "claim_trend", "add", (-0.10, 0.10)),
                      ("Claim-cost level (multiplier, all years)", "annual_claim_cost_year1_ultimate", "mult", (0.5, 2.0)),
                      ("Year-1 pent-up factor", "first_year_pent_up_factor", "add", (-1.0, 3.0)),
                      ("Lapse multiplier", "lapse_by_duration", "mult_list", (0.05, 2.5)),
                      # v4.1: the premium a loss-making product needs (commission and premium tax
                      # scale with premium; per-policy costs do not).
                      ("Year-1 premium (multiplier)", "annual_premium_year1", "mult", (0.8, 2.0))],
    "MEDICAID": [("Annual rate update", "annual_rate_update", "add", (-0.10, 0.10)),
                 ("Claim trend", "claim_trend", "add", (-0.10, 0.10)),
                 ("Starting medical cost ratio", "medical_cost_ratio_year1", "add", (-0.30, 0.30)),
                 ("Disenrollment multiplier", "annual_disenrollment", "mult", (0.2, 3.9))],
}


def breakeven() -> pd.DataFrame:
    """Reverse stress test: the level of each driver at which cash NPV and DE NPV are zero.

    Root found with Brent's method on the full re-projection (the same functions as the tornado).
    'not reached' means NPV keeps its sign over the whole bracket, which is itself a finding (e.g.
    lapse can hurt or help, so NPV may never reach zero by lapse alone).

    headroom = break-even minus base level (a signed DIRECTION, not a cushion). v4.1: base_npv and
    position say which side of break-even the base sits on: 'below break-even' means the product
    already fails the measure at base and |headroom| is the shortfall, not a margin of safety.
    """
    from scipy.optimize import brentq
    rows = []
    for lob, items in BREAKEVEN.items():
        for label, key, how, (lo, hi) in items:
            base_val = (A["global"] if key == "discount_rate" else A[lob])
            for k_ in key.split("."):
                base_val = base_val[k_]
            base_level = 1.0 if how in ("mult", "mult_list") else base_val
            for measure in ("cash", "de"):
                f = lambda x: shocked(lob, key, how, x, measure)
                base_npv = float(f(0.0 if how == "add" else 1.0))
                position = "above break-even" if base_npv > 0 else "below break-even"
                a_, b_ = (lo, hi)
                fa, fb = f(a_), f(b_)
                if np.sign(fa) == np.sign(fb):
                    rows.append(dict(lob=lob, driver=label, measure=measure, base_level=base_level, breakeven=None,
                                     headroom=None, base_npv=base_npv, position=position,
                                     note=f"not reached in bracket ({lo}, {hi})")); continue
                x0 = brentq(f, a_, b_, xtol=1e-10)
                be = base_val + x0 if how == "add" else x0
                rows.append(dict(lob=lob, driver=label, measure=measure, base_level=base_level, breakeven=be,
                                 headroom=be - base_level, base_npv=base_npv, position=position,
                                 note="NPV = 0 at this level, other assumptions at base"))
    return pd.DataFrame(rows)


# v4.1: JUDGMENT assumptions (status J in config/assumption_basis.json) run across their documented
# sensitivity ranges (ASOP 56 §3.6.1). (line, label, key, how, low, high); the ranges repeat the
# register's 'sensitivity' field. Ranked by DE NPV swing in the output. The other judgment entries
# are run in the tornado (shopper cost, plan acuity), MC_RANGES (Monte Carlo parameters), the
# filing-response attribution and the per-cell model-point table.
JUDGMENT_RANGES = [
    ("MEDSUPP", "Premium age step 2% / 4% (rate increase re-derived)", "premium_age_step", "set", 0.02, 0.04),
    ("MEDSUPP", "Claim age slope 3% / 5% (rate increase re-derived)", "claim_age_slope", "set", 0.03, 0.05),
    ("MEDSUPP", "Year-1 select factor 0.70 / 0.85", "selection_factors", "set", [0.70, 0.90, 1.0], [0.85, 0.90, 1.0]),
    ("MEDSUPP", "Shock lapse 2% / 6%", "shock_lapse_add_from_year2", "set", 0.02, 0.06),
    ("MEDSUPP", "Shopper share 30% / 70%", "antiselection.shopper_share_at_issue", "set", 0.30, 0.70),
    ("MEDSUPP", "Shopper lapse propensity 2x / 6x", "antiselection.shopper_shock_lapse_propensity", "set", 2.0, 6.0),
    ("MEDSUPP", "Acquisition cost -30% / +30%", "acquisition_cost_per_policy", "mult", 0.7, 1.3),
    ("MEDSUPP", "Maintenance cost -30% / +30%", "maintenance_cost_per_policy", "mult", 0.7, 1.3),
    ("MEDSUPP", "Required capital 6% / 15%", "required_capital_pct_of_annual_claims", "set", 0.06, 0.15),
    ("DENTAL_VISION", "Lapse -10 pts / +10 pts", "lapse_by_duration", "add_list", -0.10, 0.10),
    ("DENTAL_VISION", "Pent-up factor 1.10 / 1.40", "first_year_pent_up_factor", "set", 1.10, 1.40),
    ("DENTAL_VISION", "Annual rate increase 2% / 5%", "annual_rate_increase", "set", 0.02, 0.05),
    ("DENTAL_VISION", "First-year commission 10% / 20%", "first_year_commission", "set", 0.10, 0.20),
    ("DENTAL_VISION", "Acquisition cost -30% / +30%", "acquisition_cost_per_policy", "mult", 0.7, 1.3),
    ("DENTAL_VISION", "Required capital 4% / 10%", "required_capital_pct_of_annual_claims", "set", 0.04, 0.10),
    ("MEDICAID", "New-state acuity load 0% / 6%", "new_state_acuity_load_year1", "set", 0.0, 0.06),
    ("MEDICAID", "Risk-adjustment capture 60% / 100% (acuity +3%)", "risk_adjustment.revenue_capture", "set", 0.6, 1.0),
    ("MEDICAID", "Start-up cost $4M / $10M", "startup_cost_year1", "set", 4e6, 10e6),
    ("MEDICAID", "Renewal probability 50% / 90%", "renewal_probability", "set", 0.5, 0.9),
    ("MEDICAID", "Required capital 10% / 25%", "required_capital_pct_of_annual_claims", "set", 0.10, 0.25),
    ("MEDICAID", "Members -30% / +30%", "starting_members", "mult", 0.7, 1.3),
    # v4.1 (review): the remaining judgment entries with a documented range.
    ("MEDSUPP", "Claim payment pattern 1 month faster / slower", "claim_payment_pattern", "shift_pattern", -1, 1),
    ("DENTAL_VISION", "Claim payment pattern 1 month faster / slower", "claim_payment_pattern", "shift_pattern", -1, 1),
    ("MEDICAID", "Claim payment pattern 1 month faster / slower", "claim_payment_pattern", "shift_pattern", -1, 1),
    ("DENTAL_VISION", "Renewal commission 5% / 10%", "renewal_commission", "set", 0.05, 0.10),
    ("DENTAL_VISION", "DAC tax classification 0% / 9.2% (if guaranteed renewable)", "profit_test.dac_tax_rate_by_line.DENTAL_VISION", "set", 0.0, 0.092),
    ("MEDICAID", "Contract term 3 / 7 years", "contract_term_years", "set", 3, 7),
    ("MEDICAID", "Churn by cell: equal (base) / MACPAC relative 19:28:9, scaled to the 25% blend", "rate_cells.disenrollment_relativity", "set", [1.0, 1.0, 1.0], [0.19, 0.28, 0.09]),
    # Cell mix and loss-ratio relativities are normalized to the contract-level year-1 values, so at
    # equal churn by cell they cannot move the result; they are swept at MACPAC churn by cell, where
    # the mix drifts, and their base is that same case.
    ("MEDICAID", "Rate-cell mix: contract / MACPAC national (at MACPAC churn by cell)", "rate_cells.member_share", "set_at_macpac_churn", [0.524, 0.358, 0.118], [0.357, 0.448, 0.195]),
    ("MEDICAID", "ABD loss-ratio relativity 0.95 / 1.05 (at MACPAC churn by cell)", "rate_cells.loss_ratio_relativity", "set_at_macpac_churn", [1.0, 1.0, 0.95], [1.0, 1.0, 1.05]),
    ("MEDICAID", "Acuity path after redeterminations: none / 1.00, 1.04, 1.03 (RA lag 1 year)", "contract_terms.acuity_shift.medical_factor_by_year", "set", [], [1.0, 1.04, 1.03]),
    ("MEDSUPP", "Dynamic lapse slope 0.25 / 1.0 (at a filed increase 2 pt above 7.3%)", "dynamic_shock_lapse.shock_lapse_per_unit_rate_increase", "set_at_rate_plus_2pt", 0.25, 1.0),
]
MACPAC_CHURN_REL = (0.19, 0.28, 0.09)     # MACPAC churn by eligibility group (child, adult, disabled)
# The dynamic-lapse slope has no effect when the filed increase equals the reference (the base by
# construction), so it is swept at a filed increase 2 pt above the reference, and its base is the
# same +2 pt case. Category shares, model-point mix and the Medicaid churn / loss-ratio relativities
# at 1.0 have no effect on the base results; the per-cell tables and these rows show where they would.


def judgment_sensitivity() -> pd.DataFrame:
    rows = []
    for lob, label, key, how, lo, hi in JUDGMENT_RANGES:
        res = {}
        for side, v in (("low", lo), ("high", hi)):
            a = copy.deepcopy(A[lob]); g = copy.deepcopy(A["global"])
            if how == "add_list":
                a[key] = [min(max(x + v, 0.0), 0.95) for x in a[key]]
            elif how == "shift_pattern":
                pat = list(a[key])
                a[key] = [pat[0] + pat[1]] + pat[2:] if v < 0 else [0.0] + pat     # one month faster / slower
            elif how == "set_at_macpac_churn":
                a["rate_cells"]["disenrollment_relativity"] = list(MACPAC_CHURN_REL)
                _apply(a, g, key, "set", v)
            elif how == "set_at_rate_plus_2pt":
                a["annual_rate_increase"] = a["annual_rate_increase"] + 0.02
                _apply(a, g, key, "set", v)
            else:
                _apply(a, g, key, how, v)
            if lob == "MEDSUPP" and key in ("premium_age_step", "claim_age_slope"):
                # The filed rate increase is DERIVED from trend and the age curves (assumption basis,
                # status D), so it is re-derived here: the sweep tests the age curves on a consistent basis.
                a["annual_rate_increase"] = round((1 + a["claim_trend"]) * (1 + a["claim_age_slope"]) / (1 + a["premium_age_step"]) - 1, 4)
                a["dynamic_shock_lapse"]["reference_rate_increase"] = a["annual_rate_increase"]
            if lob == "MEDICAID" and key == "starting_members":
                a["annual_new_members"] = a["annual_new_members"] * v
            if lob == "MEDICAID" and key == "risk_adjustment.revenue_capture":
                a["risk_adjustment"]["plan_acuity"] = 1.03          # capture only matters when acuity differs
            res[side] = (npv(lob, a, g), de_npv(lob, a, g))
        if how == "set_at_rate_plus_2pt":
            ab = copy.deepcopy(A[lob]); ab["annual_rate_increase"] += 0.02
            base = (npv(lob, ab, A["global"]), de_npv(lob, ab, A["global"]))
        elif how == "set_at_macpac_churn":
            ab = copy.deepcopy(A[lob]); ab["rate_cells"]["disenrollment_relativity"] = list(MACPAC_CHURN_REL)
            base = (npv(lob, ab, A["global"]), de_npv(lob, ab, A["global"]))
        else:
            base = (npv(lob, A[lob], A["global"]), de_npv(lob, A[lob], A["global"]))
        rows.append(dict(lob=lob, judgment_assumption=label, base_cash=base[0], base_de=base[1],
                         de_low_setting=res["low"][1], de_high_setting=res["high"][1],
                         de_swing=abs(res["high"][1] - res["low"][1]),
                         sign_changes=bool(min(res["low"][1], res["high"][1]) < 0 < max(res["low"][1], res["high"][1]) or
                                           (base[1] > 0) != (min(res["low"][1], res["high"][1]) > 0))))
    return pd.DataFrame(rows).sort_values(["lob", "de_swing"], ascending=[True, False])


# v4.1 (review): Monte Carlo parameters are judgment until calibrated (estimate_mc_parameters.py), and
# they drive the P(NPV < 0) results, so each is run at the ends of its documented range.
MC_RANGES = [
    ("Trend level-shift SD 0.5 / 1.5 pt", ("trend_shock_sd",), 0.005, 0.015),
    ("Lapse multiplier SD 0.10 / 0.25", ("lapse_multiplier_sd",), 0.10, 0.25),
    ("Med Supp approval deviation SD 0.5 / 1.5 pt", ("medsupp_rate_increase_deviation_sd",), 0.005, 0.015),
    ("Medicaid rate-update SD 0.5 / 1.5 pt", ("medicaid_rate_update_sd",), 0.005, 0.015),
    ("Process risk: yearly trend SD 0.5 / 1.5 pt", ("process_risk", "trend_sd_annual"), 0.005, 0.015),
    ("Process risk: AR(1) phi 0.25 / 0.75", ("process_risk", "trend_ar1_phi"), 0.25, 0.75),
    ("Process risk: Med Supp approval noise SD 0.5 / 1.5 pt", ("process_risk", "medsupp_approval_noise_sd"), 0.005, 0.015),
    ("Process risk: Medicaid update noise SD 0.5 / 1.5 pt", ("process_risk", "medicaid_rate_update_noise_sd"), 0.005, 0.015),
    ("Trend / Medicaid update correlation 0 / 0.8", ("correlations", "matrix"), 0.0, 0.8),
]


def mc_parameter_sensitivity() -> pd.DataFrame:
    """P(NPV < 0) and mean NPV with each Monte Carlo parameter at the ends of its range."""
    rows = []
    base = monte_carlo()
    for label, path, lo, hi in [("Base", None, None, None)] + MC_RANGES:
        for side, v in ((("base", None),) if path is None else (("low", lo), ("high", hi))):
            if path is None:
                r = base
            else:
                AA = copy.deepcopy(A); t = AA["monte_carlo"]
                for k_ in path[:-1]:
                    t = t[k_]
                if path[-1] == "matrix":
                    t["matrix"][1][3] = t["matrix"][3][1] = v
                else:
                    t[path[-1]] = v
                r = monte_carlo(assumptions=AA)
            for col in r.columns:
                rows.append(dict(parameter=label, setting=side, value=v, measure=col, mean=float(r[col].mean()),
                                 prob_negative=float((r[col] < 0).mean())))
    return pd.DataFrame(rows)


def dv_product_options() -> pd.DataFrame:
    """D&V product options on the base assumptions: waiting periods (pent-up deferred) and the premium
    that breaks even. Waiting-period values are upper bounds: lapse and anti-selection do not respond."""
    a0, g0 = A["DENTAL_VISION"], A["global"]
    cats = a0["benefit_categories"]["categories"]
    rows = [dict(option="Base (no waiting periods)", cash_npv=npv("DENTAL_VISION", a0, g0), de_npv=de_npv("DENTAL_VISION", a0, g0))]
    for label, waits in [("12-month wait on MAJOR", {"MAJOR": 12}), ("6-month BASIC, 12-month MAJOR (market norm)", {"BASIC": 6, "MAJOR": 12})]:
        a = copy.deepcopy(a0)
        a["benefit_categories"]["waiting_period_months"] = [waits.get(c_, 0) for c_ in cats]
        rows.append(dict(option=label, cash_npv=npv("DENTAL_VISION", a, g0), de_npv=de_npv("DENTAL_VISION", a, g0)))
    df = pd.DataFrame(rows)
    df["de_vs_base"] = df["de_npv"] - df["de_npv"].iloc[0]
    df["major_cost_share"] = float(a0["benefit_categories"]["cost_share"][cats.index("MAJOR")])
    return df


# =============================================================================
# v4.2 RISK-BASED PRICING SOLVE (config/pricing_targets.json)
# Each line is priced so that P(DE NPV < 0) in the Monte Carlo is at most the risk tolerance: the
# tolerance-quantile of DE NPV is solved to zero with Brent's method on the full Monte Carlo (common
# random numbers, so the quantile moves smoothly with the lever). Trend stays at its best estimate.
# =============================================================================
PRICING = json.loads((ROOT / "config" / "pricing_targets.json").read_text())


def _mc_de(lob: str, mutate) -> np.ndarray:
    AA = copy.deepcopy(A)
    mutate(AA)
    return np.asarray(monte_carlo(assumptions=AA)[f"{lob}_DE"], dtype=float)


def _risk_stats(de: np.ndarray, tol: float) -> tuple[float, float]:
    return float(np.quantile(de, tol)), float((de < 0).mean())


def _set_key(AA: dict, lob: str, key: str, v):
    t = AA[lob]
    *path, leaf = key.split(".")
    for k_ in path:
        t = t[k_]
    t[leaf] = v


def _solve_quantile_zero(lob: str, key: str, lo: float, hi: float, tol: float, scale: float = 1.0, extra=None):
    """Lever value (key = scale x m) at which the tol-quantile of DE NPV is 0; None if not in [lo, hi]."""
    from scipy.optimize import brentq
    base = A[lob]
    for k_ in key.split("."):
        base = base[k_]
    def f(m):
        def mut(AA):
            if extra:
                extra(AA)
            _set_key(AA, lob, key, (base * m) if scale else m)
        return _risk_stats(_mc_de(lob, mut), tol)[0]
    flo, fhi = f(lo), f(hi)
    if np.sign(flo) == np.sign(fhi):
        return None
    return brentq(f, lo, hi, xtol=1e-6)


def _det(lob: str, a: dict) -> dict:
    g = A["global"]
    p = project_medicaid(a, g) if lob == "MEDICAID" else PROJECT[lob](a, g)
    pt = profit_test(lob, p, a, g)
    out = dict(cash_npv=float(p["npv"]), de_npv=float(pt["de_npv"]))
    if lob == "MEDICAID":
        y = p["yearly"]
        w = y["prob_in_force"] if "prob_in_force" in y else np.ones(len(y["revenue"]))
        out["pv_mlr"] = float(np.sum(np.asarray(y["medical"]) * w) / np.sum((np.asarray(y["revenue"]) + np.asarray(y["withhold_earned"])) * w))
        out["pv_loss_ratio"] = float(np.sum(p["medical"] * p["v_mid"]) / np.sum(p["revenue"] * p["v_mid"])) if "medical" in p else np.nan
    else:
        out["pv_premium"] = float((p["premium"] * p["v_prem"]).sum())
        out["pv_loss_ratio"] = float((p["claims"] * p["v_mid"]).sum()) / out["pv_premium"]
        out["de_margin"] = out["de_npv"] / out["pv_premium"]
    return out


def pricing_solve() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(line table, Med Supp model-point table) at the risk-based target. See config/pricing_targets.json."""
    from scipy.optimize import brentq
    tol = float(PRICING["risk_tolerance"])
    rows = []
    # ---- premium lines: Med Supp and D&V
    for lob in ("MEDSUPP", "DENTAL_VISION"):
        c = PRICING["lines"][lob]
        cur = float(A[lob]["annual_premium_year1"])
        q_cur, p_cur = _risk_stats(_mc_de(lob, lambda AA: None), tol)
        m = _solve_quantile_zero(lob, "annual_premium_year1", 0.5, 2.0, tol)
        assert m is not None, f"{lob}: no premium in [0.5, 2.0] x current meets the risk target"
        req = cur * m
        rec = max(cur, np.ceil(req / 12 * 100) / 100 * 12)        # never cut a price that already meets it; whole cents a month
        a = copy.deepcopy(A[lob]); a["annual_premium_year1"] = rec
        d = _det(lob, a)
        q_rec, p_rec = _risk_stats(_mc_de(lob, lambda AA: AA[lob].__setitem__("annual_premium_year1", rec)), tol)
        lr_ok = c["min_pv_loss_ratio"] is None or d["pv_loss_ratio"] >= c["min_pv_loss_ratio"]
        # A recommendation above the minimum-loss-ratio cap could not be filed: never report it as a price.
        assert lr_ok, f"{lob}: the risk target needs a PV loss ratio below the {c['min_pv_loss_ratio']:.0%} minimum; reprice by cell"
        mkt = "at or below the public reference" if rec <= c["market_reference"] * (1 + 1e-9) else f"REVIEW: {rec / c['market_reference'] - 1:+.1%} above the public reference"
        status = ("MEETS TARGET at the current premium" if rec == cur else "REPRICE") if lr_ok else "CONSTRAINT FAILS"
        rows.append(dict(lob=lob, item="Premium (risk-based target)", lever=c["lever_label"], current=cur, required_minimum=req,
                         recommended=rec, change=rec / cur - 1, p_de_negative_current=p_cur, p_de_negative_recommended=p_rec,
                         de_quantile_recommended=q_rec, cash_npv_recommended=d["cash_npv"], de_npv_recommended=d["de_npv"],
                         de_margin_recommended=d["de_margin"], pv_loss_ratio_recommended=d["pv_loss_ratio"],
                         constraint=(f"PV loss ratio >= {c['min_pv_loss_ratio']:.0%}: {'met' if lr_ok else 'FAILS'}" if c["min_pv_loss_ratio"]
                                     else "state rate filing (no federal minimum loss ratio)"),
                         market_reference=c["market_reference"], market_check=mkt, status=status,
                         note=("current premium is " + f"{cur / req - 1:.1%}" + " above the minimum that meets the target") if rec == cur
                              else f"monthly ${rec / 12:,.2f} vs ${cur / 12:,.2f}"))
        if lob == "DENTAL_VISION" and c.get("price_response_lapse_multiplier"):
            k = float(c["price_response_lapse_multiplier"])
            bump = lambda AA, k=k: AA["DENTAL_VISION"].__setitem__("lapse_by_duration", [min(x * k, 0.95) for x in AA["DENTAL_VISION"]["lapse_by_duration"]])
            m2 = _solve_quantile_zero(lob, "annual_premium_year1", 0.5, 2.0, tol, extra=bump)
            assert m2 is not None, "D&V robustness solve: no premium in [0.5, 2.0] x current meets the target"
            rows.append(dict(lob=lob, item=f"Robustness: premium if lapse is {k - 1:.0%} higher (price response)", lever=c["lever_label"],
                             current=cur, required_minimum=cur * m2 if m2 else None, recommended=None,
                             change=(m2 - 1) if m2 else None, status="SENSITIVITY",
                             note="lapse does not respond to price in the model; this shows how much a price-driven lapse response would add"))
    # ---- Medicaid: price is fixed by the state; report the lever levels that would meet the target
    c = PRICING["lines"]["MEDICAID"]
    q_cur, p_cur = _risk_stats(_mc_de("MEDICAID", lambda AA: None), tol)
    d0 = _det("MEDICAID", A["MEDICAID"])
    any_ok = False
    for key, L in c["levers"].items():
        lo, hi = L["bracket"]
        cur_v = float(A["MEDICAID"][key])
        lo_p, hi_p = L["public_range"]
        if q_cur >= 0:
            v = cur_v                                   # the target is already met at the current level
        else:
            v = _solve_quantile_zero("MEDICAID", key, lo, hi, tol, scale=0)
        d = {}
        if v is not None:
            a = copy.deepcopy(A["MEDICAID"]); _set_key({"MEDICAID": a}, "MEDICAID", key, v); d = _det("MEDICAID", a)
        # Feasibility is judged on the measure the public figure describes: the PV MLR for medical cost
        # (Milliman reports MLRs, not starting cost ratios), the admin PMPM for admin. A required level
        # below the public range's low end is not achievable (better than any MCO reports).
        measure = d.get("pv_mlr") if L.get("compare") == "pv_mlr" and v is not None else v
        feasible = v is not None and measure >= lo_p * (1 - 1e-9)
        below_floor = v is not None and d.get("pv_mlr", 1.0) < c["min_mlr"]
        any_ok |= feasible
        mc_txt = ("not reachable in the bracket" if v is None else
                  f"{'inside' if feasible else 'OUTSIDE'} the public range {lo_p:g}-{hi_p:g}"
                  + (f" (PV MLR {measure:.1%})" if L.get("compare") == "pv_mlr" else "") + f"; {L['public_basis']}"
                  + ("; below the 85% MLR floor, so the excess is remitted" if below_floor else ""))
        rows.append(dict(lob="MEDICAID", item=f"Required {L['label']} (one lever at a time)", lever=L["label"],
                         current=cur_v, required_minimum=v, recommended=None,
                         change=(v / cur_v - 1) if v is not None else None,
                         p_de_negative_current=p_cur, de_npv_recommended=d.get("de_npv"), cash_npv_recommended=d.get("cash_npv"),
                         pv_loss_ratio_recommended=d.get("pv_mlr"),
                         constraint=f"MLR floor {c['min_mlr']:.0%} (remittance modelled)",
                         market_reference=None, market_check=mc_txt,
                         status="ACHIEVABLE" if feasible else "NOT ACHIEVABLE inside public ranges"))
    rows.append(dict(lob="MEDICAID", item="Bid recommendation", lever=c["lever_label"], p_de_negative_current=p_cur,
                     de_npv_recommended=d0["de_npv"], cash_npv_recommended=d0["cash_npv"],
                     status=("BID: a lever inside public ranges meets the target" if any_ok else
                             "DO NOT BID AT THESE TERMS: no single lever inside public ranges meets the risk target"),
                     note="Capitation is state-set; negotiate terms, reduce capital need, or decline. Churn by rate cell (MACPAC) "
                          "materially improves the economics: confirm with the state data book before deciding."))
    lines = pd.DataFrame(rows)

    # ---- Med Supp model points priced to the base cell's DE margin at the recommended premium
    ms = lines[(lines.lob == "MEDSUPP") & (lines.item == "Premium (risk-based target)")].iloc[0]
    target = float(ms.de_margin_recommended)
    scale = float(ms.recommended) / float(A["MEDSUPP"]["annual_premium_year1"])
    mp = A["MEDSUPP"]["model_points"]; g = A["global"]; a0 = copy.deepcopy(A["MEDSUPP"]); a0["annual_premium_year1"] *= scale
    cells = []
    for b in mp["bands"]:
        for plan, rel in mp["plans"].items():
            w = mp["mix"][b["band"]].get(plan, 0.0)
            a = _mp_cell(b, rel, a0, "attained_age")
            cur = _det("MEDSUPP", a)
            prem0 = a["annual_premium_year1"]
            def f(k):
                aa = copy.deepcopy(a); aa["annual_premium_year1"] = prem0 * k
                return _det("MEDSUPP", aa)["de_margin"] - target
            k = max(1.0, brentq(f, 0.3, 4.0, xtol=1e-9))      # never cut a price that already meets the margin
            aa = copy.deepcopy(a); aa["annual_premium_year1"] = prem0 * k; new = _det("MEDSUPP", aa)
            # The 65% minimum loss ratio caps the premium: if the target margin needs a lower loss ratio,
            # price at the cap and report the margin that the cap allows (the target is NOT met).
            min_lr = PRICING["lines"]["MEDSUPP"]["min_pv_loss_ratio"]
            capped = new["pv_loss_ratio"] < min_lr
            if capped:
                def flr(kk):
                    a2 = copy.deepcopy(a); a2["annual_premium_year1"] = prem0 * kk
                    return _det("MEDSUPP", a2)["pv_loss_ratio"] - min_lr
                k = brentq(flr, 0.3, k, xtol=1e-9)
                aa = copy.deepcopy(a); aa["annual_premium_year1"] = prem0 * k; new = _det("MEDSUPP", aa)
            cells.append(dict(band=b["band"], plan=plan, mix=w, year1_premium_current=prem0, year1_premium_required=prem0 * k,
                              status=("CAPPED by the 65% minimum loss ratio: target margin not reachable" if capped
                                      else ("no change" if abs(k - 1) < 1e-6 else "REPRICE up")),
                              change=k - 1, plan_relativity_current=rel["premium_relativity"],
                              plan_relativity_required=rel["premium_relativity"] * k,
                              de_margin_current=cur["de_margin"], de_margin_required=new["de_margin"],
                              de_npv_per_1000_current=cur["de_npv"], de_npv_per_1000_required=new["de_npv"],
                              pv_loss_ratio_required=new["pv_loss_ratio"],
                              min_loss_ratio_met=new["pv_loss_ratio"] >= min_lr - 1e-9))
    cp = pd.DataFrame(cells)
    # Competitiveness against public filed rates: plan relativity (vs Plan G in the same band) and the
    # implied attained-age step of Plan G from age 65.
    pr, ar = PRICING["lines"]["MEDSUPP"]["market_plan_relativity_range"], PRICING["lines"]["MEDSUPP"]["market_age_step_range"]
    g65 = float(cp[(cp.band == "65") & (cp.plan == "G")].year1_premium_required.iloc[0])
    ages = {b["band"]: b["issue_age"] for b in mp["bands"]}
    chk = []
    for r in cp.itertuples():
        gb = float(cp[(cp.band == r.band) & (cp.plan == "G")].year1_premium_required.iloc[0])
        rel = r.year1_premium_required / gb
        lo, hi = pr[r.plan]
        msg = []
        if not (lo - 1e-6 <= rel <= hi + 1e-6):
            msg.append(f"REVIEW: plan relativity {rel:.3f} outside public {lo:g}-{hi:g}")
        if r.plan == "G" and ages[r.band] > 65:
            step = (gb / g65) ** (1 / (ages[r.band] - 65)) - 1
            if not (ar[0] - 1e-6 <= step <= ar[1] + 1e-6):
                msg.append(f"REVIEW: implied age step {step:.2%} outside public {ar[0]:.1%}-{ar[1]:.1%}")
        chk.append("; ".join(msg) if msg else "within public ranges")
    cp["market_check"] = chk
    base = cp[(cp.band == "65") & (cp.plan == "G")].iloc[0]
    assert abs(base.change) < 1e-6, "the base cell must need no change at its own margin"
    tot = dict(band="PORTFOLIO", plan="mix", mix=cp.mix.sum(),
               de_npv_per_1000_current=float((cp.mix * cp.de_npv_per_1000_current).sum()),
               de_npv_per_1000_required=float((cp.mix * cp.de_npv_per_1000_required).sum()))
    # Portfolio risk: the Monte Carlo per model point (same draws for every cell: common random numbers),
    # mix-weighted, at current and at recommended cell prices.
    def port_de(prem_col):
        tot_de = None
        for r in cp.itertuples():
            b = next(bb for bb in mp["bands"] if bb["band"] == r.band)
            a = _mp_cell(b, mp["plans"][r.plan], a0, "attained_age")
            a["annual_premium_year1"] = getattr(r, prem_col)
            de = _mc_de("MEDSUPP", lambda AA, a=a: AA.__setitem__("MEDSUPP", a)) * r.mix
            tot_de = de if tot_de is None else tot_de + de
        return tot_de
    pc_, pr_ = port_de("year1_premium_current"), port_de("year1_premium_required")
    tot.update(p_de_negative_current=float((pc_ < 0).mean()), p_de_negative_required=float((pr_ < 0).mean()),
               mc_mean_de_current=float(pc_.mean()), mc_mean_de_required=float(pr_.mean()))
    lines = pd.concat([lines, pd.DataFrame([dict(
        lob="MEDSUPP", item="Portfolio risk check (all model points)", lever="cell premiums",
        p_de_negative_current=tot["p_de_negative_current"], p_de_negative_recommended=tot["p_de_negative_required"],
        de_npv_recommended=tot["de_npv_per_1000_required"],
        status=("MEETS TARGET at recommended cell prices" if tot["p_de_negative_required"] <= tol else "BELOW TARGET"),
        note="Monte Carlo per model point with common random numbers, mix-weighted")])], ignore_index=True)
    cp = pd.concat([cp, pd.DataFrame([tot])], ignore_index=True)
    return lines, cp


def planning_scenarios() -> pd.DataFrame:
    """Named multi-driver planning scenarios (config/planning_scenarios.json), cash and DE NPV.

    Each scenario applies several shocks at once (the tornado moves one at a time), so the result
    includes interactions. Scenario definitions are Finance / planning inputs in production.
    """
    cfg = json.loads((ROOT / "config" / "planning_scenarios.json").read_text())
    rows = []
    for sc in cfg["scenarios"]:
        for lob in PROJECT:
            a = copy.deepcopy(A[lob]); g = copy.deepcopy(A["global"])
            # Line-specific shocks must exist (a typo fails loudly); shocks under "ALL" apply to every
            # line that has the assumption (e.g. claim_trend, discount_rate).
            for sh in sc["shocks"].get(lob, []):
                _apply(a, g, sh["key"], sh["how"], sh["value"])
            for sh in sc["shocks"].get("ALL", []):
                if sh["key"] == "discount_rate" or sh["key"].startswith("profit_test.") or _has(a, sh["key"]):
                    _apply(a, g, sh["key"], sh["how"], sh["value"])
            rows.append(dict(scenario=sc["name"], description=sc["description"], lob=lob,
                             cash_npv=npv(lob, a, g), de_npv=de_npv(lob, a, g)))
    df = pd.DataFrame(rows)
    base = df[df.scenario == cfg["scenarios"][0]["name"]].set_index("lob")
    df["cash_vs_base"] = df.cash_npv - df.lob.map(base.cash_npv)
    df["de_vs_base"] = df.de_npv - df.lob.map(base.de_npv)
    return df


# ----------------------------------------------------------------------------
# Bridge from the version 2 NPV to the v3 cash and DE NPVs (analysis of change).
# Features and profit-test layers are switched on one at a time, in a fixed order; interaction
# effects are assigned to whichever step comes later.
# ----------------------------------------------------------------------------
def bridge() -> pd.DataFrame:
    g3 = A["global"]
    rows = []
    for lob in PROJECT:
        a = A[lob]
        g = without_features(g3)
        prev = npv(lob, a, g)
        rows.append(dict(lob=lob, step="Version 2 cash NPV (all v3 features off)", value=prev, change=None))
        # Deterministic features relevant to each line (correlated Monte Carlo has no
        # deterministic effect; D&V has no v3 cash-flow change).
        relevant = {"MEDSUPP": ["antiselective_shock_lapse", "dynamic_shock_lapse"],
                    "DENTAL_VISION": ["dv_benefit_categories"],
                    "MEDICAID": ["medicaid_rate_cells", "medicaid_contract_terms"]}[lob]
        for f_ in relevant:
            g = copy.deepcopy(g); g["features"][f_] = g3["features"].get(f_, False)
            cur = npv(lob, a, g)
            rows.append(dict(lob=lob, step=f"+ {f_.replace('_', ' ')}", value=cur, change=cur - prev)); prev = cur
        g = copy.deepcopy(g3)
        cash = npv(lob, a, g)
        assert abs(cash - prev) < 1e-6
        rows.append(dict(lob=lob, step="= v4 cash NPV (pre-tax, incurred timing)", value=cash, change=None))
        i = g3["discount_rate"]
        steps = [("+ claims paid on lag pattern (reserves earn the hurdle rate)", dict(federal_income_tax_rate=0.0, investment_earned_rate=i, required_capital_pct_of_annual_claims=0.0)),
                 ("+ assets earn the investment rate, not the hurdle rate", dict(federal_income_tax_rate=0.0, required_capital_pct_of_annual_claims=0.0)),
                 ("+ required capital held (cost of capital)", dict(federal_income_tax_rate=0.0)),
                 ("+ federal income tax", dict(dac_tax_rate=0.0)),
                 ("+ DAC tax (IRC §848 capitalization, timing)", {})]
        for label, ov in steps:
            cur = de_npv(lob, a, g, overrides=ov)
            rows.append(dict(lob=lob, step=label, value=cur, change=cur - prev)); prev = cur
        rows.append(dict(lob=lob, step="= v4 DE NPV (after tax, after cost of capital)", value=prev, change=None))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Run: deterministic projections and tie-out, profit test, tornado, Monte Carlo, bridge.
# ----------------------------------------------------------------------------
def main() -> None:
    # Defence in depth: the engine refuses an unapproved assumption set in production even when it
    # is run on its own, outside run_all.py / the Databricks job.
    st = _ac.require_approved_in_production(A, "04_npv_engine")
    OUT.mkdir(parents=True, exist_ok=True)
    g = A["global"]
    print(f"Assumption set {st['sha256'][:16]}... approved={st['approved']}" + (f" ({st.get('approval_id')})" if st["approved"] else ""))
    print(f"NPV engine version {g.get('model_version', '?')}; assumptions: {ASSUMPTION_FILE}")
    print("Features: " + ", ".join(f"{f_}={'on' if feature(g, f_) else 'off'}" for f_ in FEATURES))
    if project_medsupp(A["MEDSUPP"], g)["shopper_cap_months"]:
        print("WARNING: Med Supp shopper lapse rate capped at 100% in some months; block lapse is below the lapse study there.")
    summaries = []
    for lob, fn in PROJECT.items():
        p = fn(A[lob], g)
        K = 12 * A[lob]["projection_years"]
        monthly = {k: v for k, v in p.items() if isinstance(v, np.ndarray) and v.ndim == 1 and len(v) == K}
        pd.DataFrame(monthly).to_csv(OUT / f"npv_monthly_{lob}.csv", index=False)
        tab = annual_table(lob, p)
        tab.to_csv(OUT / f"npv_projection_{lob}.csv", index=False)
        pt = profit_test(lob, p, A[lob], g)
        ptab = profit_table(lob, pt)
        ptab.to_csv(OUT / f"profit_test_{lob}.csv", index=False)
        if pt["dac"]["rate"]:
            d_ = pt["dac"]
            pd.DataFrame({"tax_year": np.arange(1, len(d_["extra_tax"]) + 1), "capitalized": d_["capitalized"],
                          "amortized": d_["amortized"], "extra_tax": d_["extra_tax"]}).to_csv(OUT / f"dac_tax_{lob}.csv", index=False)
        pd.DataFrame({k: np.asarray(pt[k]) for k in ["incurred", "paid_claims", "claim_reserve", "mlr_liability", "required_capital",
                                                     "inv_income", "book_profit", "tax", "de", "pv_de"]}).to_csv(OUT / f"profit_test_monthly_{lob}.csv", index=False)
        s = summarize(lob, tab, ptab)
        # Controls: the annual presentations must add back to the monthly results.
        assert abs(s["npv"] - float(p["npv"])) < 1e-6, "annual table does not add up to the monthly NPV"
        # The DE NPV includes the DAC tax PV, which summarize() deducts from the profit-test table total.
        assert abs(s["de_npv"] - float(pt["de_npv"])) < 1e-6, "profit-test table (less DAC tax) does not add up to the DE NPV"
        assert abs(float(pt["claim_reserve"][-1])) < 1e-6 and abs(float(pt["mlr_liability"][-1])) < 1e-6, "liabilities not run off"
        summaries.append(s)
    s = pd.DataFrame(summaries)
    s.to_csv(OUT / "npv_summary.csv", index=False)
    print("\nDeterministic NPV summary (version 4.2; public-basis assumptions, docs/ASSUMPTION_BASIS.md)")
    print(s.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))

    br = bridge()
    br.to_csv(OUT / "npv_bridge_v2_to_v4.csv", index=False)
    _v4 = br[br.step.str.startswith("+ ") & br.step.str.contains("medicaid contract terms|dv benefit categories", case=False)]
    _nn = sorted(set(_v4.loc[_v4.change.abs() > 0.01, "step"].str[2:]))
    print("\nBridge: version 2 NPV -> v4 cash NPV -> v4 DE NPV ("
          + (f"non-neutral at the approved settings: {', '.join(_nn)}; the other v4 deterministic features are neutral)" if _nn
             else "v4 deterministic features are neutral at the approved settings)"))
    print(br.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.0f}"))

    tor = tornado()
    tor.to_csv(OUT / "npv_tornado.csv", index=False)
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))
    for ax, lob in zip(axes, PROJECT):
        d = tor[tor.lob == lob].sort_values("swing")
        base = d["base_npv"].iloc[0] / 1e3
        for j, r in enumerate(d.itertuples()):
            ax.barh(j, r.npv_favorable_side / 1e3 - base, left=base, color="#2A9D8F")
            ax.barh(j, r.npv_adverse_side / 1e3 - base, left=base, color="#D1495B")
        ax.axvline(base, color="black", lw=1)
        ax.set_yticks(range(len(d))); ax.set_yticklabels(d["driver"], fontsize=7.5)
        ax.set_title(f"{lob}: cash NPV sensitivity ($000)", fontsize=10)
        ax.set_xlabel("NPV ($000)  green = favorable, red = adverse")
    plt.tight_layout(); plt.savefig(OUT / "npv_tornado.png", dpi=130); plt.close()
    print("\nTop driver by line (cash NPV):")
    print(tor.loc[tor.groupby("lob")["swing"].idxmax(), ["lob", "driver", "swing", "de_swing"]].to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.0f}"))

    print(f"\nRunning Monte Carlo ({A['monte_carlo']['n_sims']:,} scenarios, cash and DE NPV) ...")
    mc = monte_carlo()
    mc.to_csv(OUT / "npv_monte_carlo.csv", index=False)
    mc_nf = monte_carlo(no_floor=True)
    stats = pd.DataFrame({col: {"P5": np.percentile(mc[col], 5), "P50": np.percentile(mc[col], 50),
                                "P95": np.percentile(mc[col], 95), "mean": mc[col].mean(),
                                "prob_npv_negative": (mc[col] < 0).mean()} for col in mc}).T
    # The no-floor rerun removes the whole MLR settlement. Under the floor rule that is the cost of the
    # floor; under a risk corridor it is the net value of the corridor (both sides), so it is
    # labelled by rule (v4 review fix: it was always called the floor).
    rule = (A["MEDICAID"].get("contract_terms", {}).get("mlr_rule", {}).get("type", "floor")
            if feature(g, "medicaid_contract_terms") else "floor")
    col = "mean_without_mlr_floor" if rule == "floor" else "mean_without_mlr_settlement"
    stats.loc["MEDICAID", col] = mc_nf["MEDICAID"].mean()
    stats.loc["MEDICAID_DE", col] = mc_nf["MEDICAID_DE"].mean()
    stats.to_csv(OUT / "npv_monte_carlo_stats.csv")
    print(stats.to_string(na_rep="n/a", float_format=lambda x: f"{x:,.3f}" if abs(x) < 1 else f"{x:,.0f}"))
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.2))
    for ax, lob in zip(axes, PROJECT):
        for col, colr, lab in [(lob, "#12343B", "cash"), (lob + "_DE", "#E9C46A", "DE (after tax)")]:
            ax.hist(mc[col] / 1e3, bins=60, color=colr, alpha=0.6, label=lab)
        ax.axvline(0, color="grey", lw=0.8)
        ax.legend(fontsize=8)
        ax.set_title(f"{lob}: NPV distribution ($000)\nP(cash<0) = {(mc[lob] < 0).mean():.1%}, P(DE<0) = {(mc[lob + '_DE'] < 0).mean():.1%}", fontsize=9)
    plt.tight_layout(); plt.savefig(OUT / "npv_monte_carlo.png", dpi=130); plt.close()

    # ---------------- v4 outputs
    att = mc_attribution()
    att.to_csv(OUT / "npv_monte_carlo_attribution.csv", index=False)
    print("\nMonte Carlo attribution (P(NPV < 0), cash):")
    piv = att[att.measure.isin(list(PROJECT))].pivot(index="step", columns="measure", values="prob_negative")[list(PROJECT)]
    print(piv.to_string(na_rep="n/a", float_format=lambda x: f"{x:6.1%}"))

    be = breakeven()
    be.to_csv(OUT / "npv_breakeven.csv", index=False)
    print("\nBreak-even (reverse stress) - level at which NPV = 0, everything else at base:")
    print(be.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}"))

    ps = planning_scenarios()
    ps.to_csv(OUT / "npv_planning_scenarios.csv", index=False)
    print("\nPlanning scenarios (DE NPV):")
    print(ps.pivot(index="scenario", columns="lob", values="de_npv")[list(PROJECT)].to_string(na_rep="n/a", float_format=lambda x: f"{x:,.0f}"))

    js = judgment_sensitivity()
    js.to_csv(OUT / "npv_judgment_sensitivity.csv", index=False)
    print("\nJudgment assumptions across their documented ranges (DE NPV; ASOP 56 §3.6.1):")
    print(js[["lob", "judgment_assumption", "base_de", "de_low_setting", "de_high_setting", "de_swing", "sign_changes"]].to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.0f}"))

    mcs = mc_parameter_sensitivity()
    mcs.to_csv(OUT / "npv_mc_parameter_sensitivity.csv", index=False)
    print("\nMonte Carlo parameters across their documented ranges, P(DE NPV < 0):")
    print(mcs[mcs.measure.str.endswith("_DE")].pivot_table(index=["parameter", "setting"], columns="measure", values="prob_negative",
                                                          sort=False).to_string(na_rep="n/a", float_format=lambda x: f"{x:.2%}"))

    prc, cellp = pricing_solve()
    prc.to_csv(OUT / "npv_pricing_solve.csv", index=False)
    cellp.to_csv(OUT / "medsupp_cell_pricing.csv", index=False)
    print(f"\nv4.2 risk-based pricing: each line priced so P(DE NPV < 0) <= {PRICING['risk_tolerance']:.0%} (config/pricing_targets.json)")
    print(prc[["lob", "item", "current", "required_minimum", "recommended", "change", "p_de_negative_recommended", "de_npv_recommended", "status"]]
          .to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))
    print(cellp[["band", "plan", "change", "status", "market_check"]].to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:+.2%}"))

    dvo = dv_product_options()
    dvo.to_csv(OUT / "dv_product_options.csv", index=False)
    print("\nD&V product options (waiting periods defer pent-up; upper bounds):")
    print(dvo.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.0f}" if abs(x) >= 5 else f"{x:.3f}"))

    rb = medsupp_rating_bases()
    rb.to_csv(OUT / "medsupp_rating_bases.csv", index=False)
    print("\nMed Supp portfolio by rating basis (GA, AZ, FL, MO prohibit attained-age rating):")
    print(rb[rb.band == "PORTFOLIO"][["basis", "cash_npv_per_1000", "pv_loss_ratio"]].to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))

    pf = medsupp_portfolio()
    pf.to_csv(OUT / "medsupp_model_points.csv", index=False)
    port = pf[pf.band == "PORTFOLIO"].iloc[0]
    print(f"\nMed Supp new-business portfolio (model points): cash NPV per 1,000 {port.cash_npv_per_1000:,.0f}, "
          f"DE {port.de_npv_per_1000:,.0f} (base cell Plan G at 65: {npv('MEDSUPP', A['MEDSUPP'], g):,.0f})")
    print(pf.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))
    print(f"\nOutputs written to {OUT}")


if __name__ == "__main__":
    main()

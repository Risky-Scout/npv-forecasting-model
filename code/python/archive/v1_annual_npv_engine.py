"""
ARCHIVED VERSION 1 (annual model). Kept so the validation story is reproducible:
the backtest (06_npv_backtest.py) compares this version with realized outcomes
and attributes its biases. Superseded by 04_npv_engine.py (monthly, version 2).
Do not use for current results.

Why it is retained: model governance. The superseded model, the test that
rejected it and the attribution of its errors form the change history for
version 2; keeping the code runnable lets a reviewer reproduce that finding
rather than take it on trust. Outputs go to output/npv_v1/ so they cannot
overwrite version 2 results.

Conventions found biased by the backtest (Test A attribution, same random numbers):
  [BIASED] Medicaid joiners never churn in the year they join: members_end =
           start x (1 - annual churn) + joiners. A year's joiners escape that
           year's disenrollment, so member months are overstated and membership
           drifts toward joiners / annual churn, above the true steady state.
           The most material error (about -12% on Medicaid NPV).
  [BIASED] Acquisition and start-up cost discounted to mid-year 1, although
           they are paid at issue / before go-live (time 0). Overstates NPV.
  [BIASED] Premium treated as a mid-year cash flow on average exposure, rather
           than collected monthly in advance from everyone in force.
  [BIASED] Lapses at mid-period (exposure = average of start and end), rather
           than effective at the end of the paid month; understates the
           premium and claims of lapsing members.
  [BIASED] Med Supp shock lapse spread over the year, rather than concentrated
           at the rate-increase anniversary.
  [TIMING] MLR remittance discounted at mid-year with the operating result,
           rather than paid six months after the contract year closes; this
           sits in the backtest's "monthly simulation, version 1 timing" step
           rather than a switch of its own.
Unchanged in version 2: assumption file, product mechanics (aging, trend,
selection, pent-up demand, commissions, MLR floor, renewal weighting),
sensitivity set and Monte Carlo design.

The original version 1 docstring follows, updated only for file references and
the reconciliation tolerance.

04_npv_engine.py
-------------------------------------------------------------------------------
Net Present Value engine for three lines of business:

    Medicare Supplement   - policy-cohort NPV (1,000 policies issued at 65)
    Dental & Vision       - policy-cohort NPV (1,000 policies)
    Medicaid              - contract NPV (new state launch, 10,000 members)

What it produces (../../output/npv/; the archived copy writes to ../../output/npv_v1/):
    npv_projection_<LOB>.csv   year-by-year projection (in force, premium,
                               claims, expenses, profit, discounted profit)
    npv_summary.csv            NPV, PV premium, PV loss ratio, margin,
                               break-even year, NPV per policy/member
    npv_tornado.csv + .png     one-at-a-time sensitivities on key drivers
    npv_monte_carlo.csv + .png 10,000-scenario distribution of NPV
                               (P5 / P50 / P95, probability NPV < 0)

Design principles a reviewer will look for:
  * One assumption file (npv_assumptions.json) drives everything.
  * The same mechanics are built in the Excel workbook; the reconcile script
    (now 05_reconcile_python_to_excel.py) proves Python and Excel agree to the
    cent (an independent check of both).
  * Every projection is a pure function of its assumptions, so sensitivities
    and Monte Carlo just call the same function with shocked inputs.

Mechanics (annual steps, cash flows at mid-year):
    in force end   = in force start x (1 - q_death) x (1 - q_lapse)
    exposure       = average of start and end in force
    premium        = exposure x premium per policy (age step x rate increases)
    claims         = exposure x claim cost per policy (age, trend, selection)
    expenses       = commissions + premium tax + maintenance + acquisition
    NPV            = sum of profit x (1 + i) ^ -(t - 0.5)
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
# Archived copy: separate output folder; reads the same assumption file as version 2.
OUT = OUTPUT_DIR / "npv_v1"
OUT.mkdir(parents=True, exist_ok=True)
A = json.loads((HERE.parent / "npv_assumptions.json").read_text())


def disc_factors(n: int, i: float) -> np.ndarray:
    """Mid-year discount factors v^(t - 0.5). Applied to EVERY cash flow in version 1,
    including time-0 acquisition / start-up and the MLR remittance (see module docstring)."""
    t = np.arange(1, n + 1)
    return (1 + i) ** -(t - 0.5)


# ----------------------------------------------------------------------------
# Projections
# ----------------------------------------------------------------------------
def project_medsupp(a: dict, g: dict) -> pd.DataFrame:
    """Version 1 Med Supp cohort, annual. Biased conventions: mid-year premium and lapse,
    acquisition discounted half a year, shock lapse spread evenly over the policy year."""
    n = a["projection_years"]
    t = np.arange(1, n + 1)
    age = a["issue_age"] + t - 1
    dur_idx = np.minimum(t, 3) - 1
    q_death = np.minimum(a["mortality_q65"] * np.exp(a["mortality_growth"] * (age - 65)), 0.35)
    # Annual lapse including the rate-increase shock; its timing within the year is not modeled.
    q_lapse = np.minimum(np.array(a["lapse_by_duration"])[dur_idx]
                         + np.where(t >= 2, a["shock_lapse_add_from_year2"], 0.0), 1.0)
    surv = (1 - q_death) * (1 - q_lapse)
    if_start = a["cohort_size"] * np.r_[1.0, np.cumprod(surv)[:-1]]
    if_end = if_start * surv
    # [BIASED] Mid-period decrements: lapsers count for half a year of exposure.
    expo = (if_start + if_end) / 2

    prem_pp = a["annual_premium_year1"] * (1 + a["premium_age_step"]) ** (t - 1) * (1 + a["annual_rate_increase"]) ** (t - 1)
    sel = np.array(a["selection_factors"])[dur_idx]
    clm_pp = a["annual_claim_cost_year1_ultimate"] * (1 + a["claim_age_slope"]) ** (t - 1) * (1 + a["claim_trend"]) ** (t - 1) * sel

    # [BIASED] Premium earned on average exposure, not collected in advance.
    premium = expo * prem_pp
    claims = expo * clm_pp
    commission = premium * np.where(t == 1, a["first_year_commission"], a["renewal_commission"])
    prem_tax = premium * a["premium_tax"]
    maint = expo * a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1)
    # [BIASED] Acquisition booked in year 1 and discounted at mid-year 1 with everything else.
    acq = np.where(t == 1, a["cohort_size"] * a["acquisition_cost_per_policy"], 0.0)
    profit = premium - claims - commission - prem_tax - maint - acq
    df = disc_factors(n, g["discount_rate"])
    return pd.DataFrame(dict(year=t, attained_age=age, q_death=q_death, q_lapse=q_lapse,
                             in_force_start=if_start, in_force_end=if_end, exposure=expo,
                             premium_per_policy=prem_pp, claim_cost_per_policy=clm_pp,
                             premium=premium, claims=claims, commission=commission,
                             premium_tax=prem_tax, maintenance=maint, acquisition=acq,
                             profit=profit, discount_factor=df, pv_profit=profit * df))


def project_dv(a: dict, g: dict) -> pd.DataFrame:
    """Version 1 Dental & Vision cohort, annual; same timing conventions as project_medsupp."""
    n = a["projection_years"]
    t = np.arange(1, n + 1)
    dur_idx = np.minimum(t, 3) - 1
    q_lapse = np.array(a["lapse_by_duration"])[dur_idx]
    surv = 1 - q_lapse
    if_start = a["cohort_size"] * np.r_[1.0, np.cumprod(surv)[:-1]]
    if_end = if_start * surv
    # [BIASED] Mid-period lapse; with D&V's high early lapse this distorts year 1 most.
    expo = (if_start + if_end) / 2
    prem_pp = a["annual_premium_year1"] * (1 + a["annual_rate_increase"]) ** (t - 1)
    clm_pp = a["annual_claim_cost_year1_ultimate"] * (1 + a["claim_trend"]) ** (t - 1) \
        * np.where(t == 1, a["first_year_pent_up_factor"], 1.0)
    premium = expo * prem_pp
    claims = expo * clm_pp
    commission = premium * np.where(t == 1, a["first_year_commission"], a["renewal_commission"])
    prem_tax = premium * a["premium_tax"]
    maint = expo * a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1)
    acq = np.where(t == 1, a["cohort_size"] * a["acquisition_cost_per_policy"], 0.0)
    profit = premium - claims - commission - prem_tax - maint - acq
    df = disc_factors(n, g["discount_rate"])
    return pd.DataFrame(dict(year=t, q_lapse=q_lapse, in_force_start=if_start, in_force_end=if_end,
                             exposure=expo, premium_per_policy=prem_pp, claim_cost_per_policy=clm_pp,
                             premium=premium, claims=claims, commission=commission, premium_tax=prem_tax,
                             maintenance=maint, acquisition=acq, profit=profit,
                             discount_factor=df, pv_profit=profit * df))


def project_medicaid(a: dict, g: dict, renewed: bool | None = None) -> pd.DataFrame:
    """renewed=None -> expected value using renewal probability;
       True/False   -> a single scenario (used by Monte Carlo).

    Version 1 Medicaid contract, annual. Contains the most material version 1 bias:
    joiners are added after the year's churn, so they never disenroll in their first year.
    Start-up and the MLR remittance are also discounted at mid-year.
    """
    n = a["projection_years"]
    t = np.arange(1, n + 1)
    m_start = np.zeros(n); m_end = np.zeros(n)
    m = a["starting_members"]
    for k in range(n):
        m_start[k] = m
        # [BIASED] Churn applies to the opening membership only; the year's joiners escape it.
        m = m * (1 - a["annual_disenrollment"]) + a["annual_new_members"]
        m_end[k] = m
    mm = (m_start + m_end) / 2 * 12
    cap = a["capitation_pmpm_year1"] * (1 + a["annual_rate_update"]) ** (t - 1)
    med = a["capitation_pmpm_year1"] * a["medical_cost_ratio_year1"] * (1 + a["claim_trend"]) ** (t - 1) \
        * np.where(t == 1, 1 + a["new_state_acuity_load_year1"], 1.0)
    revenue = mm * cap
    medical = mm * med
    # MLR floor settled on the contract year (as in version 2), but [TIMING] discounted at
    # mid-year with the operating result instead of paid six months after year end.
    remit = np.maximum(0.0, a["mlr_floor"] * revenue - medical)
    prem_tax = revenue * a["premium_tax"]
    admin = mm * a["admin_pmpm_year1"] * (1 + g["expense_inflation"]) ** (t - 1)
    # [BIASED] Start-up spent before go-live, but discounted at mid-year 1.
    startup = np.where(t == 1, a["startup_cost_year1"], 0.0)
    profit = revenue - medical - remit - prem_tax - admin - startup
    if renewed is None:
        p_in = np.where(t <= a["contract_term_years"], 1.0, a["renewal_probability"])
    else:
        p_in = np.where(t <= a["contract_term_years"], 1.0, 1.0 if renewed else 0.0)
    exp_profit = profit * p_in
    df = disc_factors(n, g["discount_rate"])
    return pd.DataFrame(dict(year=t, members_start=m_start, members_end=m_end, member_months=mm,
                             capitation_pmpm=cap, medical_pmpm=med, revenue=revenue, medical=medical,
                             mlr_remittance=remit, premium_tax=prem_tax, admin=admin, startup=startup,
                             profit=profit, prob_contract_in_force=p_in, expected_profit=exp_profit,
                             discount_factor=df, pv_profit=exp_profit * df))


PROJECT = {"MEDSUPP": project_medsupp, "DENTAL_VISION": project_dv, "MEDICAID": project_medicaid}


def summarize(lob: str, p: pd.DataFrame) -> dict:
    """Headline metrics: NPV, PV revenue / claims (renewal-weighted for Medicaid), PV loss
    ratio, NPV margin, discounted break-even year and NPV per policy or starting member."""
    df = p["discount_factor"]
    if lob == "MEDICAID":
        pv_rev = (p["revenue"] * p["prob_contract_in_force"] * df).sum()
        pv_clm = (p["medical"] * p["prob_contract_in_force"] * df).sum()
        units = A[lob]["starting_members"]
    else:
        pv_rev = (p["premium"] * df).sum()
        pv_clm = (p["claims"] * df).sum()
        units = A[lob]["cohort_size"]
    cum = p["pv_profit"].cumsum()
    be = int(p.loc[cum > 0, "year"].min()) if (cum > 0).any() else None
    npv = p["pv_profit"].sum()
    return dict(lob=lob, npv=npv, pv_revenue=pv_rev, pv_claims=pv_clm,
                pv_loss_ratio=pv_clm / pv_rev, npv_margin=npv / pv_rev,
                breakeven_year=be, npv_per_unit=npv / units)


# ----------------------------------------------------------------------------
# Sensitivities (tornado). Same driver set and shock sizes as version 2, so the
# two versions' tornados are directly comparable.
# ----------------------------------------------------------------------------
def shocked(lob: str, key: str, how: str, val: float) -> float:
    """NPV with one assumption shocked, rerunning the same projection function as the base."""
    a = copy.deepcopy(A[lob]); g = copy.deepcopy(A["global"])
    target = g if key == "discount_rate" else a
    if how == "mult_list":
        target[key] = [x * val for x in target[key]]
    elif how == "add":
        target[key] = target[key] + val
    elif how == "mult":
        target[key] = target[key] * val
    return PROJECT[lob](a, g)["pv_profit"].sum()


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
        base = PROJECT[lob](A[lob], A["global"])["pv_profit"].sum()
        for name, key, how, lo, hi in items:
            rows.append(dict(lob=lob, driver=name, base_npv=base,
                             npv_favorable_side=shocked(lob, key, how, lo),
                             npv_adverse_side=shocked(lob, key, how, hi)))
    df = pd.DataFrame(rows)
    df["swing"] = (df["npv_favorable_side"] - df["npv_adverse_side"]).abs()
    return df


# ----------------------------------------------------------------------------
# Monte Carlo. Each driver gets its own independent random shock, but the SAME
# trend shock is applied to all three lines (one shared cost environment) and
# the same lapse multiplier to Med Supp and D&V. Correlations between drivers
# (e.g. higher trend -> bigger rate increases -> more shock lapse) are not
# modeled; that is a stated simplification.
# Finding to know: the Medicaid mean NPV comes out ~$4M below the
# deterministic NPV. The cause is the MLR floor: when claims run low, the gain
# is remitted to the state; when they run high, the plan keeps the loss. That
# asymmetry (a short option) is invisible in a single best-estimate run.
# (Version 1 inferred the floor's cost from mean vs deterministic NPV; version 2
# isolates it with a no-floor rerun on the same random numbers.)
# Version 1 loops scenario by scenario; version 2 vectorizes the same draws.
# ----------------------------------------------------------------------------
def monte_carlo() -> pd.DataFrame:
    """Parameter-uncertainty NPV distribution, one scenario at a time (version 1)."""
    mc = A["monte_carlo"]
    rng = np.random.default_rng(mc["seed"])
    n = mc["n_sims"]
    out = {"MEDSUPP": np.zeros(n), "DENTAL_VISION": np.zeros(n), "MEDICAID": np.zeros(n)}
    # Lognormal lapse multiplier with mean one (mu = -sigma^2 / 2); normal additive rate shocks.
    lapse_mult = rng.lognormal(mean=-0.5 * mc["lapse_multiplier_sd"] ** 2, sigma=mc["lapse_multiplier_sd"], size=n)
    trend_shock = rng.normal(0, mc["trend_shock_sd"], size=n)
    # Archive compatibility (kit v3.0.0): the key was renamed medsupp_rate_increase_deviation_sd.
    rate_short = rng.normal(0, mc.get("rate_increase_shortfall_sd", mc.get("medsupp_rate_increase_deviation_sd")), size=n)
    md_rate = rng.normal(0, mc["medicaid_rate_update_sd"], size=n)
    renewed = rng.random(n) < mc["medicaid_renewal_prob"]
    g = A["global"]
    for k in range(n):
        a = copy.deepcopy(A["MEDSUPP"])
        a["lapse_by_duration"] = [x * lapse_mult[k] for x in a["lapse_by_duration"]]
        a["claim_trend"] += trend_shock[k]
        a["annual_rate_increase"] -= rate_short[k]
        out["MEDSUPP"][k] = project_medsupp(a, g)["pv_profit"].sum()

        a = copy.deepcopy(A["DENTAL_VISION"])
        a["lapse_by_duration"] = [min(x * lapse_mult[k], 0.95) for x in a["lapse_by_duration"]]
        a["claim_trend"] += trend_shock[k]
        out["DENTAL_VISION"][k] = project_dv(a, g)["pv_profit"].sum()

        a = copy.deepcopy(A["MEDICAID"])
        a["claim_trend"] += trend_shock[k]
        a["annual_rate_update"] += md_rate[k]
        out["MEDICAID"][k] = project_medicaid(a, g, renewed=bool(renewed[k]))["pv_profit"].sum()
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------
# Run: deterministic projections, tornado, Monte Carlo (version 1 outputs only).
# ----------------------------------------------------------------------------
def main() -> None:
    g = A["global"]
    summaries = []
    for lob, fn in PROJECT.items():
        p = fn(A[lob], g)
        p.to_csv(OUT / f"npv_projection_{lob}.csv", index=False)
        summaries.append(summarize(lob, p))
    s = pd.DataFrame(summaries)
    s.to_csv(OUT / "npv_summary.csv", index=False)
    print("\nDeterministic NPV summary (illustrative assumptions)")
    print(s.to_string(index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))

    tor = tornado()
    tor.to_csv(OUT / "npv_tornado.csv", index=False)
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    for ax, lob in zip(axes, PROJECT):
        d = tor[tor.lob == lob].sort_values("swing")
        base = d["base_npv"].iloc[0] / 1e3
        for i, r in enumerate(d.itertuples()):
            lo, hi = r.npv_adverse_side / 1e3, r.npv_favorable_side / 1e3
            ax.barh(i, hi - base, left=base, color="#4E7D2A")
            ax.barh(i, lo - base, left=base, color="#B03A2E")
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
    stats = pd.DataFrame({lob: {"P5": np.percentile(mc[lob], 5), "P50": np.percentile(mc[lob], 50),
                                "P95": np.percentile(mc[lob], 95), "mean": mc[lob].mean(),
                                "prob_npv_negative": (mc[lob] < 0).mean()} for lob in mc}).T
    stats.to_csv(OUT / "npv_monte_carlo_stats.csv")
    print(stats.to_string(float_format=lambda x: f"{x:,.3f}" if abs(x) < 1 else f"{x:,.0f}"))
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.2))
    for ax, lob in zip(axes, mc.columns):
        ax.hist(mc[lob] / 1e3, bins=60, color="#1F3864", alpha=0.85)
        for q, c in [(5, "#B03A2E"), (50, "black"), (95, "#4E7D2A")]:
            ax.axvline(np.percentile(mc[lob], q) / 1e3, color=c, ls="--", lw=1)
        ax.axvline(0, color="grey", lw=0.8)
        ax.set_title(f"{lob}: NPV distribution ($000)\nP(NPV<0) = {(mc[lob] < 0).mean():.1%}", fontsize=10)
    plt.tight_layout(); plt.savefig(OUT / "npv_monte_carlo.png", dpi=130); plt.close()
    print(f"\nOutputs written to {OUT}")


if __name__ == "__main__":
    main()

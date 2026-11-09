"""
06_npv_backtest.py
-------------------------------------------------------------------------------
How good are the model's NPV predictions?

An NPV is a 10-20 year projection, so it cannot be compared with an actual
outcome on the day it is made. On synthetic data it can: the true process is
known, so the cohort can be "sold" thousands of times and its realized
earnings observed. Three tests:

  TEST A - Realized-outcome backtest (process risk + model approximations)
      Simulate each cohort MONTH BY MONTH at the level of individual
      events: binomial deaths/lapses/disenrollments, Poisson claim counts,
      gamma claim sizes, monthly premium in advance, acquisition and start-up
      paid at issue, Medicaid new members who can themselves churn, the MLR
      remittance settled on each year's realized results, and a coin-flip on
      contract renewal.
        -> bias  = mean(realized) - model NPV, its standard error, and z-score
           (z = bias / SE of the simulated mean; |z| < 2 is consistent with no
           bias at roughly 95% confidence, |z| well above 2 is a real bias)
        -> range = P5 / P95 of realized NPV under true assumptions
      Run against BOTH model versions:
        * version 3 (04_npv_engine.py, monthly + anti-selective shock lapse and
          Medicaid rate cells): the test of the current model. The simulator tracks
          shoppers and non-shoppers (Med Supp) and each rate cell (Medicaid) as
          separate binomial populations, so the v3 mechanics are tested at event level.
          An unbiased model should land within about 2 standard errors.
        * version 2 (archive/v2_monthly_npv_engine.py): reported at the step of the
          waterfall where the simulator has every v2 mechanic and no v3 mechanic.
        * version 1 (archive/v1_annual_npv_engine.py, annual, mid-year
          conventions): the history. Each realistic feature is a switch; with
          all switches off the simulation reproduces version 1's conventions,
          and switching them on one at a time (same random numbers) attributes
          version 1's bias to its causes. This is how the v1 flaws were found.

  TEST B - Estimation-error test (how much NPV error comes from estimating
      assumptions from 4 years of experience)
      Re-run the NPV engine with the assumptions the experience studies
      actually estimated (lapse from the GLM, shock lapse from the January
      study, trend from the normalized trend fit) instead of the true ones.
      Each estimate is swapped in alone, then all together; the combined error
      need not equal the sum, since estimation errors can offset or compound.
      Medicaid is excluded: the experience block (an existing book) and the
      NPV contract (a new state) use deliberately different parameters.

  TEST C - Process risk vs parameter risk
      Compare the spread from Test A (randomness with KNOWN assumptions)
      with the Monte Carlo spread from 04_npv_engine.py (uncertainty ABOUT
      the assumptions). A parameter-to-process ratio above 1 means assumption
      uncertainty dominates, so effort is best spent on experience studies and
      assumption setting; below 1, outcome volatility dominates, which more
      analysis cannot reduce (it is managed through scale, margin or risk
      transfer). Process risk diversifies with cohort size; parameter risk
      does not, so the ratio grows for larger blocks.

What the backtest cannot detect: Test A simulates from the same assumptions the
model uses, so it validates the projection mechanics and timing conventions, not
the assumptions. An assumption that is wrong in the real world (trend, lapse,
morbidity) passes Test A untouched; Test B gives only the error from estimating
from a finite sample of the synthetic process, not from a misspecified process.
Assumption risk in practice is covered by actual-to-expected monitoring and
assumption reviews.

Outputs: ../../output/backtest/
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = OUTPUT_DIR / "backtest"
OUT.mkdir(parents=True, exist_ok=True)

# Both engine versions are loaded by path (file names start with a digit / sit in archive/).
spec = importlib.util.spec_from_file_location("engine", HERE / "04_npv_engine.py")
eng = importlib.util.module_from_spec(spec); spec.loader.exec_module(eng)
A, G = eng.A, eng.A["global"]
spec2 = importlib.util.spec_from_file_location("engine_v2", HERE / "archive" / "v2_monthly_npv_engine.py")
eng2 = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(eng2)
spec1 = importlib.util.spec_from_file_location("engine_v1", HERE / "archive" / "v1_annual_npv_engine.py")
eng1 = importlib.util.module_from_spec(spec1); spec1.loader.exec_module(eng1)
# Parameters of the synthetic data-generating process; Test A uses its claim frequencies.
TRUE = json.loads((DATA_DIR / "true_assumptions.json").read_text())

S = 4000                                   # simulated cohorts per line
# Monthly effective rate equivalent to the annual discount rate.
i_m = (1 + G["discount_rate"]) ** (1 / 12) - 1
SHAPE = 0.9                                # gamma shape for claim size (as in the data)


def a2m(q):
    """Annual decrement rate -> monthly rate with the same annual survival (as in the engine)."""
    return 1 - (1 - np.asarray(q, dtype=float)) ** (1 / 12)


# ----------------------------------------------------------------------------
# Realistic features vs the annual model's conventions. Each simulator takes
# switches; with every switch OFF it reproduces the annual model's timing, so
# (sim mean with switches OFF) - (model NPV) measures pure approximation error,
# and turning switches ON one at a time attributes the rest of the gap.
# All variants reuse the same random-number seed (common random numbers), so
# differences between variants are effects, not noise.
#
# Simulators start the month counter at k = 1, so k - 1 is the start of month k
# and k - 0.5 its midpoint (the engine's k/12 and (k + 0.5)/12 with k from 0).
# Claim frequency only splits the expected claim cost into count x severity, so it
# changes the volatility of claims, not their mean.
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# v3 mechanics, written here independently of the engine (read straight from the
# assumption file): the simulator must not reuse the engine's helper functions, or
# Test A would only check the engine against itself.
# ----------------------------------------------------------------------------
FEAT = G.get("features", {})


def sim_classes(a, on):
    """Shopper share, shopper and non-shopper claim relativities, shock propensity (one class when off)."""
    if not on:
        return 1.0, 1.0, 1.0, 1.0
    s = a["antiselection"]
    p, rs = s["shopper_share_at_issue"], s["shopper_relative_claim_cost"]
    return p, rs, (1 - p * rs) / (1 - p), s["shopper_shock_lapse_propensity"]


def sim_shock(a, on_dynamic):
    """Annual shock lapse at the assumed rate increase (dynamic form when on)."""
    if not on_dynamic:
        return a["shock_lapse_add_from_year2"]
    d = a["dynamic_shock_lapse"]
    return max(0.0, a["shock_lapse_add_from_year2"] + d["shock_lapse_per_unit_rate_increase"] * (a["annual_rate_increase"] - d["reference_rate_increase"]))


def sim_cells(a, on):
    """Rate-cell names, shares and relativities, normalized to the contract-level blend (one cell when off)."""
    if not on:
        return ["ALL"], [1.0], [1.0], [1.0], [1.0], 1.0, 1.0
    rc = a["rate_cells"]
    s = [x / sum(rc["member_share"]) for x in rc["member_share"]]
    cap = rc["capitation_relativity"]; cap = [c / sum(si * ci for si, ci in zip(s, cap)) for c in cap]
    lr = rc["loss_ratio_relativity"]; lr = [m / sum(si * ci * mi for si, ci, mi in zip(s, cap, lr)) for m in lr]
    ch = rc["disenrollment_relativity"]; ch = [q / sum(si * qi for si, qi in zip(s, ch)) for q in ch]
    ra = a["risk_adjustment"]
    return rc["cells"], s, cap, lr, ch, 1 + ra["revenue_capture"] * (ra["plan_acuity"] - 1), ra["plan_acuity"]


REALISTIC = dict(acq_at_issue=True, prem_in_advance=True, lapse_end_of_month=True, shock_on_anniversary=True,
                 startup_at_issue=True, new_members_churn=True,
                 antiselective_lapse=True, rate_cells=True,          # v3 mechanics
                 contract_terms=True)                                # v4.1: Medicaid contract terms
V3_SWITCHES = {"antiselective_lapse", "rate_cells", "contract_terms"}     # mechanics version 2 does not have
MODEL_LIKE = {k: False for k in REALISTIC}


def realize_medsupp(sw, seed, a=A["MEDSUPP"], g=G, freq=TRUE["MEDSUPP"]["claim_frequency_pm"]):
    """S realized NPVs for the Med Supp cohort; sw selects realistic vs version 1 conventions."""
    rng = np.random.default_rng(seed)
    # v3: shoppers and non-shoppers are separate binomial populations; only shoppers take the
    # extra (shock) lapse, and each class claims at its own cost relativity. With the switch off
    # there is one class at relativity 1.0 (the version 2 process).
    share, rs, rn, kappa = sim_classes(a, sw["antiselective_lapse"] and FEAT.get("antiselective_shock_lapse", False))
    dyn = sw["antiselective_lapse"] and FEAT.get("dynamic_shock_lapse", False)
    nS = np.round(np.full(S, a["cohort_size"] * share))
    nN = np.full(S, float(a["cohort_size"])) - nS
    acq = a["cohort_size"] * a["acquisition_cost_per_policy"]
    # Realistic: acquisition paid at issue (no discount). Version 1: discounted half a year.
    npv = -acq * (1.0 if sw["acq_at_issue"] else (1 + G["discount_rate"]) ** -0.5) * np.ones(S)
    k = 0
    for t in range(1, a["projection_years"] + 1):
        age = a["issue_age"] + t - 1
        d = min(t, 3) - 1
        qd = a2m(min(a["mortality_q65"] * np.exp(a["mortality_growth"] * (age - 65)), 0.35))
        base = a["lapse_by_duration"][d]
        shock = sim_shock(a, dyn) if t >= 2 else 0.0
        q_year = min(base + shock, 1.0)
        if sw["shock_on_anniversary"] and t >= 2:
            # rate increase hits on the anniversary: base lapse spread through the year,
            # shock concentrated in month 1, sized so the full-year lapse still equals base + shock
            base_m = a2m(base)
            q1 = 1 - (1 - q_year) / (1 - base_m) ** 11
            qm = [q1] + [base_m] * 11
        else:
            # Version 1 convention: the combined annual lapse spread evenly over the year.
            qm = [a2m(q_year)] * 12
        prem_pp = a["annual_premium_year1"] * (1 + a["premium_age_step"]) ** (t - 1) * (1 + a["annual_rate_increase"]) ** (t - 1)
        clm_pp = (a["annual_claim_cost_year1_ultimate"] * (1 + a["claim_age_slope"]) ** (t - 1)
                  * (1 + a["claim_trend"]) ** (t - 1) * a["selection_factors"][d])
        comm = a["first_year_commission"] if t == 1 else a["renewal_commission"]
        maint = a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1) / 12
        base_m = a2m(base)
        for m in range(12):
            k += 1
            n = nS + nN
            # Event-level decrements: integer deaths, then integer lapses among survivors.
            dS = rng.binomial(nS.astype(np.int64), qd); dN = rng.binomial(nN.astype(np.int64), qd)
            sS, sN = nS - dS, nN - dN
            # Block lapse rate this month qm[m]. Both classes take the base rate; the extra lapse
            # is split with shoppers kappa times as likely as non-shoppers to take it, solved on
            # the realized survivors so the block's expected lapses equal qm[m]. With one class
            # this is a single binomial at qm[m], as in version 2.
            if share < 1:
                bm = min(base_m, qm[m])
                x = (sS + sN) * (qm[m] - bm) / np.maximum(kappa * sS + sN, 1.0)
                qS = np.minimum(1.0, bm + kappa * x); qN = np.minimum(1.0, bm + x)
                lS = rng.binomial(sS.astype(np.int64), qS); lN = rng.binomial(sN.astype(np.int64), qN)
            else:
                lS = rng.binomial(sS.astype(np.int64), qm[m]); lN = np.zeros(S)
            deaths, lapses = dS + dN, lS + lN
            # lapse takes effect at the end of the paid month (covered all month) or mid-month
            eoff = 0.0 if sw["lapse_end_of_month"] else 0.5
            expoS = nS - 0.5 * dS - eoff * lS; expoN = nN - 0.5 * dN - eoff * lN
            expo = expoS + expoN
            if sw["prem_in_advance"]:
                prem, tp = n * prem_pp / 12, k - 1          # whole month's premium collected up front
            else:
                prem, tp = expo * prem_pp / 12, k - 0.5
            # Severity mean = monthly claim cost x class relativity / frequency, so expected
            # claims equal the model's.
            claims = compound_claims(rng, expoS, freq, clm_pp * rs / 12 / freq)
            if share < 1:
                claims = claims + compound_claims(rng, expoN, freq, clm_pp * rn / 12 / freq)
            npv += prem * (1 - comm - a["premium_tax"]) * (1 + i_m) ** -tp
            npv -= (claims + expo * maint) * (1 + i_m) ** -(k - 0.5)
            nS, nN = sS - lS, sN - lN
    return npv


def realize_dv(sw, seed, a=A["DENTAL_VISION"], g=G, freq=TRUE["DENTAL_VISION"]["claim_frequency_pm"]):
    """S realized NPVs for the Dental & Vision cohort (no mortality decrement)."""
    rng = np.random.default_rng(seed)
    n = np.full(S, float(a["cohort_size"]))
    acq = a["cohort_size"] * a["acquisition_cost_per_policy"]
    npv = -acq * (1.0 if sw["acq_at_issue"] else (1 + G["discount_rate"]) ** -0.5) * np.ones(S)
    k = 0
    for t in range(1, a["projection_years"] + 1):
        ql = a2m(a["lapse_by_duration"][min(t, 3) - 1])
        prem_pp = a["annual_premium_year1"] * (1 + a["annual_rate_increase"]) ** (t - 1)
        clm_pp = a["annual_claim_cost_year1_ultimate"] * (1 + a["claim_trend"]) ** (t - 1) * (a["first_year_pent_up_factor"] if t == 1 else 1)
        comm = a["first_year_commission"] if t == 1 else a["renewal_commission"]
        maint = a["maintenance_cost_per_policy"] * (1 + g["expense_inflation"]) ** (t - 1) / 12
        for m in range(12):
            k += 1
            lapses = rng.binomial(n.astype(np.int64), ql)
            expo = n - (0.0 if sw["lapse_end_of_month"] else 0.5) * lapses
            if sw["prem_in_advance"]:
                prem, tp = n * prem_pp / 12, k - 1
            else:
                prem, tp = expo * prem_pp / 12, k - 0.5
            claims = compound_claims(rng, expo, freq, clm_pp / 12 / freq)
            npv += prem * (1 - comm - a["premium_tax"]) * (1 + i_m) ** -tp
            npv -= (claims + expo * maint) * (1 + i_m) ** -(k - 0.5)
            n = n - lapses
    return npv


def realize_medicaid(sw, seed, a=A["MEDICAID"], g=G, freq=0.55):
    """S realized contract NPVs for the Medicaid launch.

    Joiners arrive as Poisson counts; renewal is one Bernoulli draw per simulated contract
    applied to every year after the initial term. The MLR remittance is computed on each
    year's realized revenue and claims, so the floor's asymmetric payoff is captured
    path by path.
    v3 (rate_cells switch): each rate cell is its own binomial population with its own churn,
    joiner mix, capitation and medical cost, and its own claim frequency (from the experience
    data: CHILD / ADULT / ABD), with risk adjustment applied to revenue and medical. With the
    switch off there is one blended cell with the ADULT frequency, as in version 2.
    """
    rng = np.random.default_rng(seed)
    names, share, cap_rel, med_rel, chn_rel, ra_rev, ra_med = sim_cells(a, sw["rate_cells"] and FEAT.get("medicaid_rate_cells", False))
    fq = TRUE["MEDICAID"]["claim_frequency_pm"]
    freqs = [fq.get(c, freq) if sw["rate_cells"] else freq for c in names]
    C = len(names)
    n = [np.round(np.full(S, a["starting_members"] * share[c])) for c in range(C)]   # members who can churn
    npv = -a["startup_cost_year1"] * (1.0 if sw["startup_at_issue"] else (1 + G["discount_rate"]) ** -0.5) * np.ones(S)
    renewed = rng.random(S) < a["renewal_probability"]
    q = [a2m(min(a["annual_disenrollment"] * chn_rel[c], 1.0)) for c in range(C)]
    # v4.1: contract terms, implemented here from the assumption file (not the engine's helpers):
    # quality withhold with earn-back, directed-payment pass-through, acuity path with lagged risk
    # adjustment, and the MLR floor or a symmetric risk corridor on the year's realized results.
    ct_on = sw.get("contract_terms", False) and FEAT.get("medicaid_contract_terms", False) and "contract_terms" in a
    ctm = a.get("contract_terms", {})
    wh = ctm["quality_withhold"]["withhold_pct"] if ct_on else 0.0
    eb = ctm["quality_withhold"]["expected_earn_back"] if ct_on else 0.0
    dp1 = ctm["directed_payments"]["pmpm_year1"] if ct_on else 0.0
    dpg = ctm["directed_payments"]["annual_trend"] if ct_on else 0.0
    acu_path = (list(ctm["acuity_shift"]["medical_factor_by_year"]) if ct_on else []) + [1.0] * a["projection_years"]
    lag = int(ctm["acuity_shift"]["risk_adjustment_lag_years"]) if ct_on else 0
    capture = a["risk_adjustment"]["revenue_capture"]
    rule = ctm["mlr_rule"]["type"] if ct_on else "floor"
    co = ctm.get("mlr_rule", {}).get("corridor", {})

    def settle(base, medical):
        if rule == "floor":
            return np.maximum(0.0, a["mlr_floor"] * base - medical)
        mlr = medical / base
        T_, b1, b2 = co["target_mlr"], co["band_1"], co["band_2"]
        gain = (1 - co["plan_share_band_2"]) * np.clip(T_ - b1 - mlr, 0, b2 - b1) + (1 - co["plan_share_beyond"]) * np.maximum(T_ - b2 - mlr, 0)
        loss = (1 - co["plan_share_band_2"]) * np.clip(mlr - T_ - b1, 0, b2 - b1) + (1 - co["plan_share_beyond"]) * np.maximum(mlr - T_ - b2, 0)
        return base * (gain - loss)
    k = 0
    for t in range(1, a["projection_years"] + 1):
        acu_t = acu_path[t - 1]
        ra_t = 1.0 + capture * (acu_path[t - 1 - lag] - 1.0) if t - 1 - lag >= 0 else 1.0
        dp = dp1 * (1 + dpg) ** (t - 1)
        cap = a["capitation_pmpm_year1"] * (1 + a["annual_rate_update"]) ** (t - 1)
        med = (a["capitation_pmpm_year1"] * a["medical_cost_ratio_year1"] * (1 + a["claim_trend"]) ** (t - 1)
               * (1 + a["new_state_acuity_load_year1"] if t == 1 else 1))
        admin = a["admin_pmpm_year1"] * (1 + g["expense_inflation"]) ** (t - 1)
        rev_y = np.zeros(S); med_y = np.zeros(S); pv_y = np.zeros(S); gross_y = np.zeros(S)
        held = [np.zeros(S) for _ in range(C)]       # model convention: this year's joiners do not churn until next year
        for m in range(12):
            k += 1
            rev = np.zeros(S); clm = np.zeros(S); e_tot = np.zeros(S); gross = np.zeros(S)
            for c in range(C):
                leave = rng.binomial(n[c].astype(np.int64), q[c])
                new = rng.poisson(a["annual_new_members"] / 12 * share[c], size=S).astype(float)
                expo = n[c] - 0.5 * leave + held[c] + 0.5 * new
                gross += expo * cap * cap_rel[c] * ra_rev * ra_t
                mean_c = med * cap_rel[c] * med_rel[c] * ra_med * acu_t
                clm += compound_claims(rng, expo, freqs[c], mean_c / freqs[c])
                e_tot += expo
                if sw["new_members_churn"]:
                    n[c] = n[c] - leave + new        # joiners churn from the month after they arrive
                else:
                    # Version 1 convention: joiners are exposed but shielded from churn until year end.
                    n[c] = n[c] - leave; held[c] = held[c] + new
            dpm = e_tot * dp                                  # directed payments: revenue and medical alike
            rev = gross * (1 - wh) + dpm
            clm = clm + dpm
            rev_y += rev; med_y += clm; gross_y += gross
            pv_y += (rev - (gross + dpm) * a["premium_tax"] - clm - e_tot * admin) * (1 + i_m) ** -(k - 0.5)
        n = [n[c] + held[c] for c in range(C)]
        earned = wh * eb * gross_y                                    # withhold earned back with the settlement
        remit = settle(rev_y + earned, med_y) - earned                # settled on the year's realized results
        pv_y -= remit * (1 + G["discount_rate"]) ** -(t + 0.5)        # paid six months after the year closes
        active = np.ones(S, bool) if t <= a["contract_term_years"] else renewed
        npv += np.where(active, pv_y, 0.0)
    return npv


def compound_claims(rng, expo, freq, sev_mean):
    """Total claims for `expo` member-months: Poisson count, gamma sizes.
    Sum of N gammas(shape k) = gamma(N*k), so this is exact and vectorized.

    Given the count N, the total of N iid Gamma(k, theta) claims is Gamma(N k, theta); one gamma
    draw per cohort-month replaces N individual draws without approximation. Scale
    theta = sev_mean / k keeps the mean claim at sev_mean; shape k = 0.9 < 1 gives a
    coefficient of variation per claim slightly above 1 (right-skewed sizes).
    """
    nn = rng.poisson(np.maximum(freq * expo, 0))
    out = np.zeros_like(expo, dtype=float)
    pos = nn > 0
    out[pos] = rng.gamma(nn[pos] * SHAPE, sev_mean / SHAPE)
    return out


def summarize(name, model_npv, real):
    """Bias of a model NPV against S realized NPVs, plus the realized distribution.

    bias_se is the standard error of the simulated mean (sd / sqrt(S)): the sampling noise in
    the realized mean, against which the bias is judged.
    """
    se = real.std(ddof=1) / np.sqrt(len(real))
    return dict(lob=name, model_npv=model_npv, realized_mean=real.mean(), bias=real.mean() - model_npv,
                bias_pct=(real.mean() - model_npv) / abs(model_npv), bias_se=se,
                realized_p5=np.percentile(real, 5), realized_p50=np.percentile(real, 50),
                realized_p95=np.percentile(real, 95), realized_sd=real.std(ddof=1),
                prob_loss=(real < 0).mean())


# Simulator and the ordered switches for each line; the order sets the attribution waterfall.
SIM = {"MEDSUPP": (realize_medsupp, ["acq_at_issue", "prem_in_advance", "lapse_end_of_month", "shock_on_anniversary", "antiselective_lapse"]),
       "DENTAL_VISION": (realize_dv, ["acq_at_issue", "prem_in_advance", "lapse_end_of_month"]),
       "MEDICAID": (realize_medicaid, ["startup_at_issue", "new_members_churn", "rate_cells", "contract_terms"])}
LABEL = {"acq_at_issue": "Acquisition cost paid at issue (model: mid-year 1)",
         "prem_in_advance": "Premium collected monthly in advance (model: mid-year)",
         "lapse_end_of_month": "Lapse effective at end of paid month (model: mid-period)",
         "shock_on_anniversary": "Shock lapse right at the anniversary (model: spread over year)",
         "startup_at_issue": "Start-up cost spent before go-live (model: mid-year 1)",
         "new_members_churn": "New members churn in their first year (model: they don't until next year)",
         "antiselective_lapse": "v3: only healthier shoppers take the shock lapse (two-class population)",
         "rate_cells": "v3: Child / Adult / ABD rate cells with their own claim frequency and risk adjustment",
         "contract_terms": "v4.1: quality withhold and earn-back, directed payments, MLR rule, acuity path"}

# ----------------------------------------------------------------------------
# TEST A: realized outcomes vs model NPV, with bias attribution for version 1.
#
# Waterfall per line: v1 NPV -> simulation under v1 conventions (approximation error of
# the annual discretization) -> one realistic feature at a time -> fully realistic mean.
# The final row compares the version 2 NPV with that realized mean (residual bias,
# expected to be within sampling error). Interaction effects are assigned to whichever
# switch is turned on later, so the order in SIM matters for individual steps, not the total.
# ----------------------------------------------------------------------------
model_v1 = {lob: eng1.PROJECT[lob](A[lob], G)["pv_profit"].sum() for lob in eng1.PROJECT}
model_v2 = {lob: eng2.npv(lob, eng2.A[lob], eng2.A["global"]) for lob in eng2.PROJECT}
# v4.1: the simulator implements the Medicaid contract terms itself (realize_medicaid). D&V benefit
# categories must still be neutral here (the D&V simulator has one claim cost), so stop otherwise.
_bc = A["DENTAL_VISION"].get("benefit_categories", {})
if FEAT.get("dv_benefit_categories") and not (all(x == 0 for x in _bc["trend_add_on"]) and all(x == 0 for x in _bc["waiting_period_months"])
                                               and len(set(_bc["pent_up_relativity"])) == 1):
    raise SystemExit("06_npv_backtest: D&V benefit categories are non-neutral; the D&V simulator has one claim cost. "
                     "Extend it before running the backtest on this assumption set.")
model = {lob: eng.npv(lob, A[lob], G) for lob in eng.PROJECT}          # current model (v4; v3 mechanics + neutral v4 terms)
print(f"TEST A: simulating {S:,} realized cohorts per line (monthly, event level) ...")
real, rows_attr, real_v2 = {}, [], {}
for lob, (fn, switches) in SIM.items():
    # One seed per line, reused for every switch setting (common random numbers).
    seed = 1000 + list(SIM).index(lob)
    sw = dict(MODEL_LIKE)
    prev = fn(sw, seed).mean()
    rows_attr.append(dict(lob=lob, step="Version 1 NPV (annual model)", value=model_v1[lob], change=None))
    rows_attr.append(dict(lob=lob, step="Monthly simulation, version 1 timing conventions", value=prev, change=prev - model_v1[lob]))
    v2_done = False
    for s_ in switches:
        if s_ in V3_SWITCHES and not v2_done:
            # Every v2 mechanic on, no v3 mechanic: the realized mean version 2 is judged against.
            real_v2[lob] = fn(sw, seed)
            rows_attr.append(dict(lob=lob, step="Version 2 NPV (monthly model) vs the simulation above", value=model_v2[lob], change=model_v2[lob] - prev))
            v2_done = True
        sw[s_] = True
        cur = fn(sw, seed).mean()
        rows_attr.append(dict(lob=lob, step="+ " + LABEL[s_], value=cur, change=cur - prev))
        prev = cur
    if not v2_done:
        real_v2[lob] = fn(sw, seed)
        rows_attr.append(dict(lob=lob, step="Version 2 NPV (monthly model) vs the simulation above", value=model_v2[lob], change=model_v2[lob] - prev))
    # All switches on: the realistic simulation the current version is judged against.
    real[lob] = fn(sw, seed)
A_res = pd.DataFrame([summarize(l, model[l], real[l]) for l in real])          # version 3 vs realized
A_res["z_score"] = A_res["bias"] / A_res["bias_se"]
A_res["version2_npv"] = [model_v2[l] for l in real]
A_res["version2_bias_pct"] = [(real_v2[l].mean() - model_v2[l]) / abs(model_v2[l]) for l in real]
A_res["version2_z_score"] = [(real_v2[l].mean() - model_v2[l]) / (real_v2[l].std(ddof=1) / np.sqrt(S)) for l in real]
A_res["version1_npv"] = [model_v1[l] for l in real]
A_res["version1_bias_pct"] = [(real_v2[l].mean() - model_v1[l]) / abs(model_v1[l]) for l in real]
attrib_end = pd.DataFrame([dict(lob=l, step="Current model NPV (v4)", value=model[l], change=model[l] - real[l].mean()) for l in real])
attrib = pd.concat([pd.DataFrame(rows_attr), attrib_end], ignore_index=True)
pd.DataFrame(real).to_csv(OUT / "realized_npv_draws.csv", index=False)
print(A_res.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.3f}" if abs(x) < 10 else f"{x:,.0f}"))
print("\nWaterfall from model NPV to realized mean:")
print(attrib.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.0f}"))

# ----------------------------------------------------------------------------
# TEST B: NPV with ESTIMATED assumptions vs TRUE assumptions
#
# TRUTH is the synthetic data-generating process (data/true_assumptions.json): the NPV under the
# true lapse, shock lapse and trend, everything else at the approved values. v3 used the approved
# assumption set as the truth, which was only valid while the approved trends equalled the
# generator's; in v4 the approved trend is the credibility-weighted indication, so it is measured
# against the truth like any other estimate.
# Rows: each raw experience estimate swapped in alone, all together, and the v4 approved trend
# (credibility blend) - the error the assumption-setting PROCESS makes on this book. D&V shows
# both the v3 raw fit and the v4 normalized fit.
# ----------------------------------------------------------------------------
lapse = pd.read_csv(OUTPUT_DIR / "lapse" / "lapse_assumption_table.csv")
trend = pd.read_csv(OUTPUT_DIR / "sql_results" / "04_lapse_persistency_study.csv")
ms_l = lapse[lapse.lob == "MEDSUPP"].set_index("duration_year")
dv_l = lapse[lapse.lob == "DENTAL_VISION"].set_index("duration_year")
# Excess lapse in the anniversary (January) month over the base monthly rate. Because the shock
# is concentrated in that one month, the monthly excess is the estimate of the annual shock add.
jan_excess_monthly = float(trend.loc[trend.lob == "MEDSUPP", "january_excess_monthly_lapse"].iloc[0])

tf = pd.read_csv(OUTPUT_DIR / "trend" / "fitted_trends.csv").set_index("lob")["fitted_annual_trend"]
# Raw Med Supp block cost growth mixes aging and selection wear-off of a closed block with cost
# trend; the engine applies age slope and selection separately, so only the normalized trend applies.
# v4: the same holds for D&V (year-1 pent-up share and product mix), so the normalized fit is the estimate.
trend_fit = {"MEDSUPP": float(tf["MEDSUPP_NORMALIZED"]),
             "DENTAL_VISION": float(tf["DENTAL_VISION_NORMALIZED"])}
TRUTH = {"MEDSUPP": {"lapse_by_duration": TRUE["MEDSUPP"]["annual_lapse_by_duration"],
                     "shock_lapse_add_from_year2": TRUE["MEDSUPP"]["shock_lapse_add_january"],
                     "claim_trend": TRUE["MEDSUPP"]["annual_claim_trend"]},
         "DENTAL_VISION": {"lapse_by_duration": TRUE["DENTAL_VISION"]["annual_lapse_by_duration"],
                           "claim_trend": TRUE["DENTAL_VISION"]["annual_claim_trend"]}}

rowsB = []
def npv_with(lob, changes):
    # JSON round trip = deep copy of the base assumptions before overriding.
    a = json.loads(json.dumps(A[lob])); a.update(changes)
    return eng.npv(lob, a, G)
# Base lapse estimates exclude the January shock so the shock is not counted twice.
est_ms = {"lapse_by_duration": [float(ms_l.loc[k, "fitted_annual_lapse_excl_jan_shock"]) for k in ["1", "2", "3+"]],
          "shock_lapse_add_from_year2": jan_excess_monthly,
          "claim_trend": trend_fit["MEDSUPP"]}
est_dv = {"lapse_by_duration": [float(dv_l.loc[k, "fitted_annual_lapse_excl_jan_shock"]) for k in ["1", "2", "3+"]],
          "claim_trend": trend_fit["DENTAL_VISION"]}
for lob, est in [("MEDSUPP", est_ms), ("DENTAL_VISION", est_dv)]:
    tru = TRUTH[lob]
    base = npv_with(lob, tru)
    for key, val in est.items():
        v_ = npv_with(lob, {**tru, key: val})
        rowsB.append(dict(lob=lob, assumption=key, true=tru[key], estimated=val, npv_true=base,
                          npv_with_estimate=v_, npv_error=v_ - base))
    allv = npv_with(lob, est)
    rowsB.append(dict(lob=lob, assumption="ALL estimated together", true=None, estimated=None,
                      npv_true=base, npv_with_estimate=allv, npv_error=allv - base))
    v_ = npv_with(lob, {**tru, "claim_trend": A[lob]["claim_trend"]})
    rowsB.append(dict(lob=lob, assumption="claim_trend: approved (public benchmark, v4.1)", true=tru["claim_trend"],
                      estimated=A[lob]["claim_trend"], npv_true=base, npv_with_estimate=v_, npv_error=v_ - base))
    if lob == "DENTAL_VISION":
        v_ = npv_with(lob, {**tru, "claim_trend": float(tf["DENTAL_VISION"])})
        rowsB.append(dict(lob=lob, assumption="claim_trend: v3 raw fit (not normalized)", true=tru["claim_trend"],
                          estimated=float(tf["DENTAL_VISION"]), npv_true=base, npv_with_estimate=v_, npv_error=v_ - base))
B_res = pd.DataFrame(rowsB)
print("\nTEST B: NPV error from estimating assumptions from 4 years of experience")
print(B_res[["lob", "assumption", "true", "estimated", "npv_true", "npv_with_estimate", "npv_error"]].to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 1 else f"{x:,.0f}"))

# ----------------------------------------------------------------------------
# TEST C: process risk vs parameter risk
#
# Process width: P5-P95 of realized NPV with assumptions known (Test A).
# Parameter width: P5-P95 of the engine's Monte Carlo (npv_monte_carlo.csv, so
# 04_npv_engine.py must have been run first). Medicaid's renewal coin flip sits in
# both distributions, so part of its spread is common to the two measures.
# ----------------------------------------------------------------------------
mc = pd.read_csv(OUTPUT_DIR / "npv" / "npv_monte_carlo.csv")
C_res = pd.DataFrame([dict(lob=l,
                           process_p5=np.percentile(real[l], 5), process_p95=np.percentile(real[l], 95),
                           process_width=np.percentile(real[l], 95) - np.percentile(real[l], 5),
                           parameter_p5=np.percentile(mc[l], 5), parameter_p95=np.percentile(mc[l], 95),
                           parameter_width=np.percentile(mc[l], 95) - np.percentile(mc[l], 5)) for l in real])
C_res["parameter_to_process"] = C_res["parameter_width"] / C_res["process_width"]
print("\nTEST C: width of 90% range - randomness (known assumptions) vs uncertainty about assumptions")
print(C_res.to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.1f}" if abs(x) < 100 else f"{x:,.0f}"))

A_res.to_csv(OUT / "test_a_realized_vs_model.csv", index=False)
attrib.to_csv(OUT / "test_a_bias_attribution.csv", index=False)
B_res.to_csv(OUT / "test_b_estimation_error.csv", index=False)
C_res.to_csv(OUT / "test_c_process_vs_parameter.csv", index=False)
print(f"\nOutputs written to {OUT}")

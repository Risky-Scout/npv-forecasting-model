"""
03_lapse_glm_study.py
-------------------------------------------------------------------------------
Lapse (persistency) assumption study using a Poisson GLM - the standard way
actuaries turn termination counts into a rate table while controlling for
several drivers at once.

    lapses ~ Poisson( exposure_months x exp(b0 + b_duration + b_jan + b_plan + b_age) )

Why a GLM instead of a simple ratio table: a raw table by duration mixes
together the effect of duration, age and plan. The GLM separates them, gives
standard errors (showing which effects are statistically supported), and
produces a smooth, defensible assumption table.

Why Poisson with a log(exposure) offset: the response is a count of lapses
in a cell observed over a known number of member-months. The log link makes
effects multiplicative, so exp(coefficient) is a relativity to the base cell,
and the offset fixes the exposure coefficient at 1 so the model estimates a
rate per member-month rather than a raw count. With monthly lapse rates of a
few percent, Poisson and binomial give practically the same answer; Poisson
is preferred because it handles exposure directly and fits on grouped cells.

Exposure convention: one unit per member-month in force, including the month
of termination (decrements are applied at month end in the data). Deaths end
exposure but are not counted as lapses, which treats death as a competing
decrement. Medicaid DISENROLL is treated as the lapse event.

Limitations: synthetic data; the "true" rates exist only because the data
were simulated. Fitted rates are averaged over this block's in-force mix, so
they are specific to that mix. Calendar-year effects (e.g., the Medicaid
redetermination wave) are not modeled and therefore load into the fitted
rates - see the note printed at the end.

Outputs (../../output/lapse/):
    lapse_glm_coefficients_<LOB>.csv   relativities with 95% intervals
    lapse_assumption_table.csv         fitted annual lapse by LOB x duration
                                       vs the "true" simulated rates
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parents[2]
OUT = OUTPUT_DIR / "lapse"
OUT.mkdir(parents=True, exist_ok=True)
# Synthetic mode only: the generating parameters, used solely for the fitted-vs-true comparison.
_TRUE_FILE = DATA_DIR / "true_assumptions.json"
TRUE = json.loads(_TRUE_FILE.read_text()) if _TRUE_FILE.exists() else None

# ----------------------------------------------------------------------------
# 1. Exposure base: de-duplicated member-months with termination information
# Duplicate eligibility rows would add exposure with no matching lapse and
# understate every rate, so they are removed before anything is counted.
# ----------------------------------------------------------------------------
mem = pd.read_csv(DATA_DIR / "members.csv", dtype={"issue_month": str, "term_month": str})
mm = pd.read_csv(DATA_DIR / "member_months.csv", dtype={"month": str, "rate_cell": str})
mm = mm.drop_duplicates(["member_id", "month"])                 # data-quality fix (see SQL 01)
mm = mm.merge(mem[["member_id", "issue_month", "issue_age", "term_month", "term_reason"]], on="member_id")

# ----------------------------------------------------------------------------
# 2. Rating variables
#   duration  policy year 1 / 2 / 3+ (months 0-11, 12-23, 24+); year 3+ is
#             treated as ultimate, which matches how the data were simulated
#             and is typical of where individual-product lapse levels off
#   january   calendar January, when Med Supp annual rate increases land
#   lapse     1 in the member's termination month if the reason is voluntary
#             lapse or Medicaid disenrollment; deaths are excluded
#   age_band  attained age band, meaningful for Med Supp only
# ----------------------------------------------------------------------------
per = pd.PeriodIndex(mm["month"], freq="M")
iss = pd.PeriodIndex(mm["issue_month"], freq="M")
mm["dur_m"] = (per.year - iss.year) * 12 + (per.month - iss.month)
mm["duration"] = pd.cut(mm["dur_m"], [-1, 11, 23, 10_000], labels=["1", "2", "3+"]).astype(str)
mm["january"] = (per.month == 1).astype(int)
mm["lapse"] = ((mm["term_month"] == mm["month"]) & mm["term_reason"].isin(["LAPSE", "DISENROLL"])).astype(int)
mm["age_band"] = pd.cut(mm["issue_age"] + mm["dur_m"] / 12, [0, 69.999, 74.999, 200],
                        labels=["65-69", "70-74", "75+"]).astype(str)

# ----------------------------------------------------------------------------
# 3. Fit one GLM per line of business
# Data are aggregated to one row per combination of rating variables before
# fitting. For a Poisson model the grouped fit gives the same coefficients as
# a member-month-level fit (cell totals are sufficient statistics) at a
# fraction of the size, which is also how this scales on Databricks.
# Base levels: policy year 1, non-January, the alphabetically first plan
# (F for Med Supp, DENTAL for D&V), and age 65-69 for Med Supp.
# ----------------------------------------------------------------------------
rows = []
for lob, d in mm.groupby("lob"):
    by = ["duration", "january", "plan"] + (["age_band"] if lob == "MEDSUPP" else [])
    g = d.groupby(by, observed=True).agg(exposure=("lapse", "size"), lapses=("lapse", "sum")).reset_index()
    g = g[g["exposure"] > 0]                                     # log(0) offset is undefined
    terms = "C(duration, Treatment('1')) + january + C(plan)" + (" + C(age_band)" if lob == "MEDSUPP" else "")
    # Med Supp shock lapse is a reaction to a renewal rate increase, which a
    # policy in its first year has not yet received; the interaction restricts
    # the January effect to policy years 2+. For D&V and Medicaid the plain
    # January term is a specification check: no January effect was simulated,
    # so it should come out near 1.0 and not significant.
    if lob == "MEDSUPP":
        terms = terms.replace("january", "january:I(duration != '1')")   # rate-increase shock only after yr 1
    model = smf.glm(f"lapses ~ {terms}", data=g, family=sm.families.Poisson(),
                    offset=np.log(g["exposure"])).fit()
    # Relativity = exp(coefficient): the multiplicative change in the monthly
    # lapse rate relative to the base cell, holding the other variables fixed.
    # Intervals are Wald 95% intervals on the log scale, then exponentiated.
    # A wide interval or a high p-value signals limited credibility for that
    # level (ASOP 25); such levels are candidates for combining with a
    # neighbour or credibility-weighting toward a prior or industry table.
    coef = pd.DataFrame({"coef": model.params, "se": model.bse})
    coef["relativity"] = np.exp(coef["coef"])
    coef["rel_low_95"] = np.exp(coef["coef"] - 1.96 * coef["se"])
    coef["rel_high_95"] = np.exp(coef["coef"] + 1.96 * coef["se"])
    coef["p_value"] = model.pvalues
    coef.to_csv(OUT / f"lapse_glm_coefficients_{lob}.csv")
    # Deviance / residual df is a goodness-of-fit check on the Poisson variance
    # assumption. Materially above 1 indicates overdispersion (an omitted driver
    # or clustering), in which case standard errors are understated and should
    # be scaled by sqrt(dispersion) (quasi-Poisson) or the model extended.
    # With sparse cells the Pearson statistic is a useful cross-check.
    print(f"\n{lob}  (deviance / df = {model.deviance / model.df_resid:.2f}; ~1.0 means Poisson fits)")
    print(coef[["relativity", "rel_low_95", "rel_high_95", "p_value"]].round(3).to_string())

    # Fitted annual lapse by duration, averaging over the in-force mix of other drivers
    # Predicted lapse counts are summed across all cells in the duration and
    # divided by exposure, giving an exposure-weighted monthly rate. Two
    # versions are kept: all months (what the block actually experiences,
    # including the January shock) and non-January months only (the base rate
    # to which a separate shock assumption is added in the NPV model).
    # Annualization 1 - (1 - q_monthly)^12 is the inverse of the monthly
    # conversion used in the simulation and assumes a constant monthly rate.
    true_rates = TRUE[lob]["annual_lapse_by_duration"] if (TRUE and lob != "MEDICAID") else [None] * 3
    for i, dur in enumerate(["1", "2", "3+"]):
        sub = g[g["duration"] == dur].copy()
        fitted_monthly = model.predict(sub, offset=np.log(sub["exposure"])).sum() / sub["exposure"].sum()
        non_jan = sub[sub["january"] == 0]
        base_monthly = model.predict(non_jan, offset=np.log(non_jan["exposure"])).sum() / non_jan["exposure"].sum()
        rows.append(dict(lob=lob, duration_year=dur,
                         fitted_annual_lapse_all_months=1 - (1 - fitted_monthly) ** 12,
                         fitted_annual_lapse_excl_jan_shock=1 - (1 - base_monthly) ** 12,
                         true_simulated_base_lapse=true_rates[i]))

# ----------------------------------------------------------------------------
# 4. Assumption table: fitted vs true
# Medicaid was simulated with one monthly disenrollment rate for all
# durations, so its "true" annual rate is the same in every duration row.
# ----------------------------------------------------------------------------
tab = pd.DataFrame(rows)
if TRUE and "MEDICAID" in set(tab.lob):
    md = TRUE["MEDICAID"]["monthly_disenrollment"]
    tab.loc[tab.lob == "MEDICAID", "true_simulated_base_lapse"] = 1 - (1 - md) ** 12
tab.to_csv(OUT / "lapse_assumption_table.csv", index=False)
print("\nLapse assumption table (fitted vs true):")
print(tab.to_string(index=False, float_format=lambda x: f"{x:6.2%}"))
# Medicaid fitted churn exceeds the base rate because the experience window
# contains the post-PHE redetermination period (Apr-2023 to Jun-2024 in the
# simulation), when eligibility reviews resumed after the continuous-coverage
# requirement ended. Projecting the fitted rate forward would overstate
# future disenrollment and understate member-months. A production study would
# add a calendar-period indicator to the GLM, or exclude those months, and set
# the forward assumption from post-unwinding experience.
print("\nNote: Medicaid 'true' is the base rate; the 2023-24 redetermination wave"
      " (1.8x churn) sits inside the experience, so fitted > base. That is the kind"
      " of one-off you must remove or model explicitly before projecting forward.")

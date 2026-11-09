"""
02_claims_trend_forecast.py
-------------------------------------------------------------------------------
Forecasts completed claims PMPM by line of business 36 months ahead and picks
the method by out-of-sample accuracy, not by judgment alone.

Steps
  1. Read the completed monthly experience built by the SQL kit
     (gold_monthly_experience in output/practice.duckdb). Run
     01_run_sql_kit.py first.
  2. Hold out the last 12 months. Fit three candidate models on the rest:
       a. Log-linear trend + month-of-year seasonality (the classic actuarial
          trend model: the slope IS the annual trend rate)
       b. Exponential smoothing (Holt-Winters, damped, multiplicative season)
       c. SARIMA(1,1,1)(0,1,1)12 on log PMPM
  3. Score each on the holdout (MAPE and bias). Bias matters as much as MAPE
     for pricing: a model that is consistently low under-prices.
  4. Refit the winner on all data and forecast 36 months with 80% intervals.
  5. Compare the fitted annual trend to the "true" trend used to simulate the
     data (only possible with synthetic data - it's how a method is tested).

Caution built in: the most recent months are partly IBNR (estimated, not
paid). The script down-weights nothing silently - it prints how much of the
latest experience month is estimate at the paid-through date, so it is
visible how much of the right-hand end of the series is estimate rather than fact.

Assumptions and limitations
  * Input claims are pooled (capped at the line's pooling point in SQL 02),
    so the fitted trend is a trend in capped PMPM. Excess claims are priced
    separately as a pooling charge; leveraging of trend above a fixed pooling
    point is not reflected here.
  * Four years of monthly data on blocks of this size support a trend
    estimate but not a fully credible one (ASOP 25). A production trend
    assumption would blend the fitted rate with external benchmarks (e.g.,
    published Medicare Part B cost trend, state Medicaid rate-setting trend)
    and document the weight given to each.
  * The forecast is used month by month in the NPV model, so no separate
    midpoint-to-midpoint trending step is needed here. If the fitted trend is
    instead applied to a base-period PMPM, it should run from the midpoint of
    the experience period to the midpoint of the projection or rating period.
  * Model selection on a single 12-month holdout is a light-touch validation
    (ASOP 56). Rolling-origin backtests would give a more stable ranking.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

# Convergence and frequency warnings from statsmodels are suppressed so the
# console output stays readable. A production run should log them instead:
# a non-converged fit is a model-risk finding, not noise.
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
OUT = OUTPUT_DIR / "trend"
OUT.mkdir(parents=True, exist_ok=True)
# The synthetic book's "true" parameters exist only in synthetic mode; they are used solely to
# report how close each fit came. On production data the comparison is reported as n/a.
_TRUE_FILE = DATA_DIR / "true_assumptions.json"
TRUE = json.loads(_TRUE_FILE.read_text()) if _TRUE_FILE.exists() else None
TRUE_TREND = ({"MEDSUPP": TRUE["MEDSUPP"]["annual_claim_trend"],
               "DENTAL_VISION": TRUE["DENTAL_VISION"]["annual_claim_trend"],
               "MEDICAID": TRUE["MEDICAID"]["annual_claim_trend"]} if TRUE else {})
# Claim-cost curves for Med Supp normalization come from config, not from the synthetic truth:
# in production they are Humana's own age / duration / plan curves.
CURVES_ALL = json.loads((ROOT / "config" / "claim_cost_curves.json").read_text())
CURVES = CURVES_ALL["MEDSUPP"]
# 12-month holdout covers one full seasonal cycle, so a model cannot score well
# merely by getting a few favourable months right.
HOLDOUT, HORIZON = 12, 36
# v4 rolling-origin evaluation: training lengths (months) for each forecast origin, most recent first.
ROLLING_TRAIN_LENGTHS = [36, 33, 30, 27, 24]
# A forecast more than 5% off on average is a review trigger (same threshold as the single-holdout flag).
BIAS_TRIGGER = 0.05
# Member-level bootstrap for the trend standard error (v4): resamples and seed.
BOOT_B, BOOT_SEED = 400, 20260504

# ----------------------------------------------------------------------------
# 1. Inputs: completed monthly experience and chain-ladder completion factors
# completed_claims = paid-to-date / completion factor at the month's lag, so
# the latest months already contain an IBNR estimate. Their PMPMs are less
# reliable than older, fully run-out months.
# ----------------------------------------------------------------------------
con = duckdb.connect(str(OUTPUT_DIR / "practice.duckdb"), read_only=True)
gold = con.execute("""
    SELECT lob, month, member_months, completed_claims
    FROM gold_monthly_experience ORDER BY lob, month
""").fetchdf()
cf = con.execute("SELECT lob, lag, completion_factor FROM completion_factors").fetchdf()
# Paid-through date of the claims extract: sets how many months of run-out the latest month has.
# Paid-through month from run_config.json: the same value the SQL kit and the data contract use
# (the contract stops the run unless the latest paid month in the feed equals it).
_RUN = json.loads(Path(os.environ.get("RUN_CONFIG", ROOT / "config" / "run_config.json")).read_text())
paid_through = pd.Period(_RUN["paid_through"], freq="M")
con.close()


# ----------------------------------------------------------------------------
# 2. Candidate models. Each returns (mean, low_80, high_80, annual_trend or None)
#    indexed by forecast month.
# ----------------------------------------------------------------------------
def loglinear_fit(y: pd.Series):
    """OLS of log PMPM on time (in years) plus month-of-year dummies.

    Fitting in logs makes trend multiplicative: exp(slope) - 1 is the
    compound annual trend rate, the quantity an assumption memo states.
    Seasonal dummies (January as base) keep seasonality from leaking into the
    slope when the series starts or ends mid-year.
    """
    t = np.arange(len(y)) / 12.0
    X = pd.get_dummies(pd.Series(y.index.month, index=y.index), prefix="m", drop_first=True).astype(float)
    X.insert(0, "t", t)
    X = sm.add_constant(X)
    model = sm.OLS(np.log(y.values), X)   # ordinary least squares on log PMPM
    return model.fit(), X


def loglinear_trend_with_se(y: pd.Series, maxlags: int = 3):
    """(annual trend, standard error of the annual trend, months) from the log-linear model.

    v4: the trend assumption is set by a credibility blend of this fit with an external
    benchmark (03b), and the blend weight depends on how precisely the data pin the slope down.
    Monthly residuals of a PMPM series are autocorrelated (claims settle, seasonality is not
    perfectly fixed), so the ordinary OLS standard error is too small. A heteroskedasticity-
    and autocorrelation-consistent (Newey-West, 3 lags) standard error is used instead. The
    annual trend is exp(b) - 1, so its standard error is exp(b) x se(b) (delta method).
    """
    t = np.arange(len(y)) / 12.0
    X = pd.get_dummies(pd.Series(y.index.month, index=y.index), prefix="m", drop_first=True).astype(float)
    X.insert(0, "t", t)
    X = sm.add_constant(X)
    res = sm.OLS(np.log(y.values), X).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    b, se_b = float(res.params["t"]), float(res.bse["t"])
    return float(np.exp(b) - 1), float(np.exp(b) * se_b), int(len(y))


def loglinear_forecast(y: pd.Series, h: int):
    """Extrapolate the log-linear model h months and return the fitted annual trend."""
    res, X = loglinear_fit(y)
    idx = pd.period_range(y.index[-1] + 1, periods=h, freq="M")
    t = (np.arange(len(y), len(y) + h)) / 12.0
    # Rebuild the forecast design matrix with exactly the training dummy columns,
    # so January stays the omitted base and column order matches the fit.
    Xf = pd.get_dummies(pd.Series(idx.month, index=idx), prefix="m").astype(float)
    Xf = Xf.reindex(columns=[c for c in X.columns if c.startswith("m_")], fill_value=0.0)
    Xf.insert(0, "t", t)
    Xf = sm.add_constant(Xf, has_constant="add")
    # alpha = 0.20 gives 80% intervals; obs_ci_* are prediction intervals for a
    # single month (parameter + residual uncertainty), not for the mean line.
    pr = res.get_prediction(Xf).summary_frame(alpha=0.20)
    # exp(E[log y]) estimates the median, not the mean, of a lognormal. Scaling
    # by exp(sigma^2 / 2) restores the mean; without it the forecast is biased
    # low, which in pricing means under-pricing.
    smear = np.exp(res.mse_resid / 2)   # bias correction when back-transforming logs
    return (pd.Series(np.exp(pr["mean"].values) * smear, index=idx),
            pd.Series(np.exp(pr["obs_ci_lower"].values) * smear, index=idx),
            pd.Series(np.exp(pr["obs_ci_upper"].values) * smear, index=idx),
            float(np.exp(res.params["t"]) - 1))


def ets_forecast(y: pd.Series, h: int):
    """Damped-trend Holt-Winters with multiplicative seasonality.

    Damping makes the trend flatten over the horizon, a more cautious shape
    than straight-line extrapolation over 36 months. The interval is a
    heuristic (residual SD widening with h^0.25, z = 1.28 for 80% two-sided),
    not a model-based interval, and will understate long-horizon uncertainty.
    """
    m = ExponentialSmoothing(y.values, trend="add", damped_trend=True, seasonal="mul",
                             seasonal_periods=12, initialization_method="estimated").fit()
    idx = pd.period_range(y.index[-1] + 1, periods=h, freq="M")
    f = m.forecast(h)
    resid_sd = np.std(m.resid) * np.sqrt(np.arange(1, h + 1) ** 0.5)
    return (pd.Series(f, index=idx), pd.Series(f - 1.28 * resid_sd, index=idx),
            pd.Series(f + 1.28 * resid_sd, index=idx), None)


def sarima_forecast(y: pd.Series, h: int):
    """SARIMA(1,1,1)(0,1,1)12 on log PMPM (airline-type seasonal model).

    Regular and seasonal differencing remove trend and fixed seasonality, so
    no explicit trend rate is returned. The back-transform exp(mean) is a
    median forecast with no lognormal correction, so it tends slightly low;
    the holdout bias score is where that shows up.
    """
    m = SARIMAX(np.log(y.values), order=(1, 1, 1), seasonal_order=(0, 1, 1, 12)).fit(disp=False)
    idx = pd.period_range(y.index[-1] + 1, periods=h, freq="M")
    fc = m.get_forecast(h)
    ci = fc.conf_int(alpha=0.20)
    return (pd.Series(np.exp(fc.predicted_mean), index=idx), pd.Series(np.exp(ci[:, 0]), index=idx),
            pd.Series(np.exp(ci[:, 1]), index=idx), None)


MODELS = {"log-linear trend": loglinear_forecast, "Holt-Winters damped": ets_forecast,
          "SARIMA": sarima_forecast}

# ---------------------------------------------------------------------------
# 3. Normalizing Med Supp PMPM: the block's raw PMPM rises because the block
# ages and underwriting selection wears off, not only because unit costs rise.
# To get a PURE cost trend, divide by a demographic/duration factor first.
# The age curve, selection factors and plan relativities come from
# config/claim_cost_curves.json: the synthetic book's factors here, the
# company's own claim-cost curves in production.
#
# Mechanically, each member-month is weighted by its expected cost relative to
# a standard risk (age 65, ultimate duration, Plan G). Completed claims divided
# by the sum of those weights is a PMPM per standard member, whose trend
# excludes aging, wear-off of selection and plan mix. Aging and selection are
# then projected explicitly in the NPV model rather than buried in trend,
# which avoids double counting them.
# ---------------------------------------------------------------------------
mem = pd.read_csv(DATA_DIR / "members.csv", dtype={"issue_month": str})
mmx = pd.read_csv(DATA_DIR / "member_months.csv", dtype={"month": str, "rate_cell": str})
# De-duplicate to match the silver exposure that sits behind the gold claims.
mmx = mmx[mmx.lob == "MEDSUPP"].drop_duplicates(["member_id", "month"]).merge(
    mem[["member_id", "issue_month", "issue_age"]], on="member_id")
_p, _i = pd.PeriodIndex(mmx["month"], freq="M"), pd.PeriodIndex(mmx["issue_month"], freq="M")
_dur = (_p.year - _i.year) * 12 + (_p.month - _i.month)
mmx["factor"] = ((1 + CURVES["claim_age_slope"]) ** (mmx["issue_age"] + _dur / 12 - CURVES["base_age"])
                 * np.array(CURVES["selection_factor_by_duration"])[np.minimum(_dur // 12, 2)]
                 * mmx["plan"].map(CURVES["plan_relativity"]))
# A plan missing from the curve file would silently drop exposure; fail loudly instead.
assert mmx["factor"].notna().all(), "plan in experience without a relativity in config/claim_cost_curves.json"
ms_factor = mmx.groupby("month")["factor"].sum()
norm = gold[gold.lob == "MEDSUPP"].set_index("month")
gold_norm = pd.DataFrame({"lob": "MEDSUPP_NORMALIZED", "month": norm.index,
                          "member_months": ms_factor.reindex(norm.index).values,   # factor-weighted exposure
                          "completed_claims": norm["completed_claims"].values})
# v4: Dental & Vision normalized the same way, for the year-1 pent-up load and product mix
# (config/claim_cost_curves.json -> DENTAL_VISION). The raw D&V series mixes cost trend with the
# changing share of first-year (pent-up) exposure: as a block of flat new sales matures, the
# year-1 share falls and the raw PMPM grows more slowly than cost. The v4 seed study measured
# the raw fit at about 2.1% on average against a true 3.5%.
DVC = CURVES_ALL["DENTAL_VISION"]
dvx = pd.read_csv(DATA_DIR / "member_months.csv", dtype={"month": str, "rate_cell": str})
dvx = dvx[dvx.lob == "DENTAL_VISION"].drop_duplicates(["member_id", "month"]).merge(mem[["member_id", "issue_month"]], on="member_id")
_p, _i = pd.PeriodIndex(dvx["month"], freq="M"), pd.PeriodIndex(dvx["issue_month"], freq="M")
_dur = (_p.year - _i.year) * 12 + (_p.month - _i.month)
dvx["factor"] = np.array(DVC["duration_factor_by_policy_year"])[np.minimum(_dur // 12, 2)] * dvx["plan"].map(DVC["plan_relativity"])
assert dvx["factor"].notna().all(), "D&V product in experience without a relativity in config/claim_cost_curves.json"
dv_factor = dvx.groupby("month")["factor"].sum()
dnorm = gold[gold.lob == "DENTAL_VISION"].set_index("month")
gold = pd.concat([gold, gold_norm, pd.DataFrame({"lob": "DENTAL_VISION_NORMALIZED", "month": dnorm.index,
                                                 "member_months": dv_factor.reindex(dnorm.index).values,
                                                 "completed_claims": dnorm["completed_claims"].values})], ignore_index=True)
if TRUE_TREND:
    TRUE_TREND["MEDSUPP_NORMALIZED"] = TRUE_TREND["MEDSUPP"]
    TRUE_TREND["DENTAL_VISION_NORMALIZED"] = TRUE_TREND["DENTAL_VISION"]
# Exposure weights per member-month for the bootstrap (same normalization as the series above).
WEIGHTS = {"MEDSUPP_NORMALIZED": mmx[["member_id", "month", "factor"]], "DENTAL_VISION_NORMALIZED": dvx[["member_id", "month", "factor"]]}


# ---------------------------------------------------------------------------
# v4: member-level bootstrap standard error of the fitted trend.
# The fitted slope's sampling error comes mostly from WHICH members are in the block: the same
# members appear in every month, so a member with persistently high claims raises the whole
# series. Residual-based standard errors (OLS, even HAC) treat months as the sampling unit and
# miss this; on the synthetic book HAC understated the true sampling SD by 1.4-1.8x (seed study).
# Resampling MEMBERS with replacement keeps each member's whole claim history together, which is
# the source of the dependence, and works on real data where the generating process is unknown.
# Point estimates still come from the gold series above; the bootstrap provides the SE only.
# ---------------------------------------------------------------------------
def bootstrap_trend_se(series: str, B: int = BOOT_B, seed: int = BOOT_SEED):
    base_lob = series.replace("_NORMALIZED", "")
    con_ = duckdb.connect(str(OUTPUT_DIR / "practice.duckdb"), read_only=True)
    mmb = con_.execute("SELECT DISTINCT member_id, month FROM silver_member_months WHERE lob = ?", [base_lob]).fetchdf()
    clb = con_.execute("SELECT member_id, incurred_month AS month, SUM(paid_capped) AS paid FROM silver_claims "
                       "WHERE lob = ? AND paid_month <= ? GROUP BY 1, 2", [base_lob, str(paid_through)]).fetchdf()
    con_.close()
    months = sorted(gold[gold.lob == series]["month"].astype(str))
    if series in WEIGHTS:
        mmb = mmb.merge(WEIGHTS[series].drop_duplicates(["member_id", "month"]), on=["member_id", "month"], how="left")
        mmb["factor"] = mmb["factor"].fillna(1.0)
    else:
        mmb["factor"] = 1.0
    mmb = mmb[mmb.month.isin(months)]
    clb = clb[clb.month.isin(months)]
    # Completion: scale each member's paid-to-date by the month's gold completion ratio, so the
    # bootstrap's full-sample PMPM equals the gold series exactly in claims.
    g_ = gold[gold.lob == series].assign(month=lambda d: d["month"].astype(str)).set_index("month")
    ratio = (g_["completed_claims"] / clb.groupby("month")["paid"].sum().reindex(g_.index)).fillna(1.0)
    ids = pd.Index(sorted(set(mmb.member_id) | set(clb.member_id)))
    ti = {m_: j for j, m_ in enumerate(months)}
    E = np.zeros((len(ids), len(months))); C = np.zeros_like(E)
    np.add.at(E, (ids.get_indexer(mmb.member_id), mmb.month.map(ti).to_numpy()), mmb.factor.to_numpy())
    np.add.at(C, (ids.get_indexer(clb.member_id), clb.month.map(ti).to_numpy()), clb.paid.to_numpy() * clb.month.map(ratio).to_numpy())
    rng_ = np.random.default_rng(seed)
    W = np.stack([np.bincount(rng_.integers(0, len(ids), len(ids)), minlength=len(ids)) for _ in range(B)], axis=1)
    pm = (C.T @ W) / (E.T @ W)                                   # months x B bootstrap PMPM series
    idx = pd.PeriodIndex(months, freq="M")
    X = pd.get_dummies(pd.Series(idx.month), prefix="m", drop_first=True).astype(float).to_numpy()
    X = np.column_stack([np.ones(len(months)), np.arange(len(months)) / 12.0, X])
    beta = np.linalg.lstsq(X, np.log(pm), rcond=None)[0]
    tr_b = np.exp(beta[1]) - 1
    full = np.linalg.lstsq(X, np.log(C.sum(0) / E.sum(0)), rcond=None)[0][1]
    return float(np.std(tr_b, ddof=1)), float(np.exp(full) - 1)

# ---------------------------------------------------------------------------
# 4. Per line: holdout test, model selection, full refit and forecast
# ---------------------------------------------------------------------------
scores, forecasts, fitted_trends, rolling, bias_diag = [], [], [], [], []
fig, axes = plt.subplots(5, 1, figsize=(12, 17))
for ax, (lob, d) in zip(axes, gold.groupby("lob")):
    d = d.copy()
    d.index = pd.PeriodIndex(d["month"], freq="M")
    y = d["completed_claims"] / d["member_months"]
    # The holdout is the most recent year, which is also the least complete;
    # completion-factor error is therefore part of what the test measures.
    train, test = y.iloc[:-HOLDOUT], y.iloc[-HOLDOUT:]

    for name, fn in MODELS.items():
        pred = fn(train, HOLDOUT)[0]
        # Signed relative error: positive = forecast above actual (conservative
        # for pricing), negative = forecast below actual (under-pricing risk).
        err = (pred.values - test.values) / test.values
        scores.append(dict(lob=lob, model=name, mape=np.mean(np.abs(err)), bias=np.mean(err)))
    sc = pd.DataFrame([s for s in scores if s["lob"] == lob])
    # MAPE alone can favour a model that is close month to month but
    # systematically off in one direction. A persistent bias compounds through
    # every projection year of an NPV, so it is weighted equally with MAPE.
    sc["rank_score"] = sc["mape"] + np.abs(sc["bias"])     # penalize bias as much as error

    # v4: rolling-origin evaluation. One 12-month holdout is a single draw: a model can look
    # biased because that one year was unusual. Re-score every model from several forecast
    # origins (training ends 36, 33, 30, 27 and 24 months into the series, each forecasting the
    # next 12 months) and select on the AVERAGE rank score. The last origin is the classic holdout
    # above. Origins overlap, so the standard error across origins understates the true one; the
    # persistence test therefore also requires the bias to have the same sign at most origins.
    for k, train_len in enumerate(ROLLING_TRAIN_LENGTHS):
        if train_len + HOLDOUT > len(y):
            continue
        tr, te = y.iloc[:train_len], y.iloc[train_len:train_len + HOLDOUT]
        for name, fn in MODELS.items():
            try:
                pr = fn(tr, HOLDOUT)[0]
                e = (pr.values - te.values) / te.values
                rolling.append(dict(lob=lob, model=name, origin=str(tr.index[-1]), train_months=train_len,
                                    mape=float(np.mean(np.abs(e))), bias=float(np.mean(e)), fit_ok=True))
            except Exception as exc:          # a model that cannot be fitted from this origin is recorded, not hidden
                rolling.append(dict(lob=lob, model=name, origin=str(tr.index[-1]), train_months=train_len,
                                    mape=np.nan, bias=np.nan, fit_ok=False, error=str(exc)[:120]))
    ro = pd.DataFrame([r for r in rolling if r["lob"] == lob and r["fit_ok"]])
    agg = ro.groupby("model").agg(mean_mape=("mape", "mean"), mean_bias=("bias", "mean"),
                                  sd_bias=("bias", "std"), n_origins=("bias", "size"),
                                  share_bias_negative=("bias", lambda s: float((s < 0).mean())))
    agg["rank_score"] = agg["mean_mape"] + agg["mean_bias"].abs()
    # Only models that fitted from every origin are eligible, so the comparison is like for like.
    eligible = agg[agg["n_origins"] == agg["n_origins"].max()]
    best = eligible.sort_values("rank_score").index[0]
    ab = agg.loc[best]
    se_ab = ab["sd_bias"] / np.sqrt(ab["n_origins"])
    same_sign = max(ab["share_bias_negative"], 1 - ab["share_bias_negative"])
    persistent = bool(abs(ab["mean_bias"]) > BIAS_TRIGGER and abs(ab["mean_bias"]) > 2 * se_ab and same_sign >= 0.8)
    last_b = float(sc.set_index("model").loc[best, "bias"])
    bias_diag.append(dict(lob=lob, selected_model=best, last_holdout_bias=last_b,
                          rolling_mean_bias=float(ab["mean_bias"]), rolling_se_bias=float(se_ab),
                          origins=int(ab["n_origins"]), same_sign_share=float(same_sign),
                          persistent_bias=persistent,
                          verdict=("PERSISTENT - review the forecast basis" if persistent else
                                   ("ONE-YEAR - last holdout biased, not persistent across origins" if abs(last_b) > BIAS_TRIGGER
                                    else "OK"))))
    tr_, se_, n_ = loglinear_trend_with_se(y)

    # Refit the chosen model on all months so the forecast starts from the
    # latest level. The annual trend is always reported from the log-linear
    # model because it is the only candidate with an explicit, explainable
    # trend parameter.
    mean, lo, hi, _ = MODELS[best](y, HORIZON)
    _, _, _, trend = loglinear_forecast(y, HORIZON)        # trend rate for the assumption memo
    assert abs(tr_ - trend) < 1e-9, "HAC refit must give the same point estimate as the forecast fit"
    se_boot, tr_check = bootstrap_trend_se(lob)
    print(f"   trend SE: member bootstrap {se_boot:.2%} (HAC {se_:.2%}); bootstrap full-sample fit {tr_check:.2%}")
    fitted_trends.append(dict(lob=lob, fitted_annual_trend=trend, trend_se_bootstrap=se_boot, trend_se_hac=se_, months=n_,
                              true_cost_trend=TRUE_TREND.get(lob)))
    fc = pd.DataFrame({"lob": lob, "month": mean.index.astype(str), "model": best,
                       "pmpm_forecast": mean.values, "pmpm_low_80": lo.values, "pmpm_high_80": hi.values})
    forecasts.append(fc)

    # IBNR share of the latest experience month = 1 - completion factor at its observable lag
    # (months of run-out between that month and the paid-through date). The lag-0 factor is also
    # shown: it is how little of a month is paid in the month it is incurred.
    cf_lob = lob.replace("_NORMALIZED", "")
    cf_l = cf[cf.lob == cf_lob].set_index("lag")["completion_factor"]
    runout = min((paid_through - y.index[-1]).n, int(cf_l.index.max()))
    latest_ibnr_share = 1 - cf_l[runout]
    print(f"\n{lob}: best model = {best}")
    print(f"   fitted annual trend (log-linear) = {trend:6.2%}   true simulated pure cost trend = {TRUE_TREND[lob]:.2%}" if lob in TRUE_TREND else f"   fitted annual trend (log-linear) = {trend:6.2%}")
    print(f"   latest month ({y.index[-1]}) is ~{latest_ibnr_share:.0%} estimated (IBNR) after {runout} months"
          f" of run-out; a month is only ~{cf_l[0]:.0%} paid when incurred - treat recent points with care")
    if lob == "MEDSUPP":
        print("   NOTE: raw block PMPM trend includes aging + selection wear-off + mix, so it runs"
              " well above the pure cost trend. See MEDSUPP_NORMALIZED for the pure trend.")
    # A 5% holdout bias threshold is a review trigger, not an automatic
    # rejection: the cause (outlier, completion, mix shift) decides the action.
    b = sc.set_index("model").loc[best, "bias"]
    print(f"   selected on {int(ab['n_origins'])} rolling origins: mean bias {ab['mean_bias']:+.1%} (SE {se_ab:.1%}), "
          f"last holdout {b:+.1%}; trend SE (HAC) {se_:.2%}")
    if abs(b) > BIAS_TRIGGER:
        print(f"   BIAS FLAG: best model is {b:+.1%} biased on the holdout. Investigate the latest"
              " year (outliers, completion, mix) before using this forecast for pricing.")
    print(f"   bias verdict: {bias_diag[-1]['verdict']}")

    # Dotted vertical line marks the end of the training window.
    ax.plot(y.index.to_timestamp(), y.values, color="#1F3864", label="completed PMPM (actual)")
    ax.plot(mean.index.to_timestamp(), mean.values, color="#4E7D2A", label=f"forecast: {best}")
    ax.fill_between(mean.index.to_timestamp(), lo.values, hi.values, color="#4E7D2A", alpha=0.18, label="80% interval")
    ax.axvline(train.index[-1].to_timestamp(), color="grey", ls=":", lw=1)
    ax.set_title(f"{lob}: claims PMPM - fitted trend {trend:.1%} (true pure cost trend {TRUE_TREND[lob]:.1%})" if lob in TRUE_TREND else f"{lob}: claims PMPM - fitted trend {trend:.1%}", fontsize=10)
    ax.legend(fontsize=8, loc="upper left")

# ---------------------------------------------------------------------------
# 5. Outputs: chart, holdout scores, forecasts and fitted trends
# ---------------------------------------------------------------------------
plt.tight_layout(); plt.savefig(OUT / "claims_pmpm_forecast.png", dpi=130); plt.close()
s = pd.DataFrame(scores)
s.to_csv(OUT / "holdout_scores.csv", index=False)
pd.concat(forecasts).to_csv(OUT / "claims_pmpm_forecast.csv", index=False)
pd.DataFrame(fitted_trends).to_csv(OUT / "fitted_trends.csv", index=False)   # read by 06_npv_backtest.py
pd.DataFrame(rolling).to_csv(OUT / "rolling_origin_scores.csv", index=False)
pd.DataFrame(bias_diag).to_csv(OUT / "forecast_bias_diagnostics.csv", index=False)   # read by 03b (v4)
print("\nForecast bias diagnostics (rolling origins):")
print(pd.DataFrame(bias_diag).to_string(index=False))
print("\nHoldout accuracy (last 12 months):")
print(s.to_string(index=False, float_format=lambda x: f"{x:7.2%}"))
print(f"\nOutputs written to {OUT}")

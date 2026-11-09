"""
estimate_mc_parameters.py  (v4)
-------------------------------------------------------------------------------
Estimates the Monte Carlo volatility and correlation parameters from history, so the judgment
values in npv_assumptions.json -> monte_carlo can be replaced by calibrated ones.

Input: a CSV of annual history (template: config/mc_history_template.csv), one row per year:
    year, medsupp_trend, dv_trend, medicaid_trend      realized pure cost trend by line
    medsupp_rate_assumed, medsupp_rate_approved          filed-plan vs approved Med Supp increase
    medicaid_rate_update                                 certified Medicaid rate update
Columns may be blank where history is missing; each estimate uses the years available.

Output (printed and written as JSON next to the input): proposed values for
    trend_shock_sd            SD of the long-run level of trend around the assumption (parameter risk):
                              the standard error of the mean trend, sd / sqrt(n), inflated for
                              autocorrelation by sqrt((1 + phi) / (1 - phi))
    process_risk.trend_sd_annual, trend_ar1_phi   year-to-year deviation SD and AR(1) coefficient
    process_risk.medsupp_approval_noise_sd   year-to-year SD of (assumed - approved)
    medsupp_rate_increase_deviation_sd       SD of the long-run average shortfall (SE of the mean,
                              inflated for autocorrelation; NOT the yearly SD, or it is counted twice)
    process_risk.medicaid_rate_update_noise_sd    SD of the rate update around its mean
    correlation trend <-> Medicaid rate update    Pearson correlation of the yearly series
with the number of years behind each estimate. Nothing is written into the assumption file:
proposed values go to the assumption committee like any other indication.

Limits: annual series are short (typically 10-15 years), so these estimates are themselves
uncertain; the script reports a standard error for each SD (sd / sqrt(2(n - 1))) and refuses to
estimate from fewer than `MIN_YEARS` years.

Usage:  python estimate_mc_parameters.py path/to/history.csv
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_YEARS = 6


def ar1(x: np.ndarray) -> tuple[float, float]:
    """(SD of deviations from the mean, lag-1 autocorrelation) of a yearly series."""
    d = x - x.mean()
    sd = float(np.std(d, ddof=1))
    phi = float(np.sum(d[1:] * d[:-1]) / np.sum(d * d)) if len(d) > 2 else 0.0
    return sd, float(np.clip(phi, -0.95, 0.95))


def estimate(h: pd.DataFrame) -> dict:
    out, notes = {}, {}
    tcols = [c for c in ("medsupp_trend", "dv_trend", "medicaid_trend") if c in h]
    # One cost environment: pool the lines' deviations from their own means.
    devs = [h[c].dropna().to_numpy(dtype=float) for c in tcols if h[c].notna().sum() >= MIN_YEARS]
    if devs:
        sds, phis = zip(*[ar1(x) for x in devs])
        n = int(np.mean([len(x) for x in devs]))
        sd, phi = float(np.mean(sds)), float(np.mean(phis))
        out["process_risk.trend_sd_annual"] = sd
        out["process_risk.trend_ar1_phi"] = phi
        out["trend_shock_sd"] = sd / np.sqrt(n) * np.sqrt((1 + phi) / (1 - phi))
        notes["trend"] = f"{len(devs)} line(s), about {n} years each; SE of the SD ~ {sd / np.sqrt(2 * (n - 1)):.4f}"
    if {"medsupp_rate_assumed", "medsupp_rate_approved"} <= set(h):
        d = (h["medsupp_rate_assumed"] - h["medsupp_rate_approved"]).dropna().to_numpy(dtype=float)
        if len(d) >= MIN_YEARS:
            sd, phi_r = ar1(d)
            # Year-to-year noise SD and level SD are different things: the level shift is the
            # uncertainty in the LONG-RUN average shortfall (standard error of the mean, inflated
            # for autocorrelation), as for trend. v4 review fix: the same SD was proposed for both,
            # which the engine then applies twice.
            out["process_risk.medsupp_approval_noise_sd"] = sd
            out["medsupp_rate_increase_deviation_sd"] = sd / np.sqrt(len(d)) * np.sqrt((1 + phi_r) / (1 - phi_r))
            notes["medsupp_rate"] = f"{len(d)} years; mean shortfall {d.mean():+.4f} (a non-zero mean means the plan is biased, fix the plan, not the SD)"
    if "medicaid_rate_update" in h:
        x = h["medicaid_rate_update"].dropna().to_numpy(dtype=float)
        if len(x) >= MIN_YEARS:
            out["process_risk.medicaid_rate_update_noise_sd"] = float(np.std(x, ddof=1))
            notes["medicaid_rate"] = f"{len(x)} years"
    tc = "medicaid_trend" if "medicaid_trend" in h and h["medicaid_trend"].notna().sum() >= MIN_YEARS else (tcols[0] if tcols else None)
    if "medicaid_rate_update" in h and tc:
        j = h[["medicaid_rate_update", tc]].dropna()
        if len(j) >= MIN_YEARS:
            out["correlation.trend_medicaid_rate_update"] = float(np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1])
            notes["correlation"] = (f"{len(j)} years, {tc} vs medicaid_rate_update. Measured on yearly series; the engine "
                                    "applies it to the level-shift drivers, so treat it as an upper-level guide, not a calibration")
    if not out:
        raise SystemExit(f"Not enough history: every series needs at least {MIN_YEARS} years.")
    return {"proposed": out, "basis": notes}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 2
    f = Path(sys.argv[1])
    res = estimate(pd.read_csv(f))
    (f.with_suffix(".mc_parameters.json")).write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

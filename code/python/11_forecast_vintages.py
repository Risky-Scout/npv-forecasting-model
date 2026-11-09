"""
11_forecast_vintages.py  (v4)
-------------------------------------------------------------------------------
How accurate is the forecast, and is it biased? Every forecast is a vintage
(the as-of month it was made at). Scoring each vintage against what actually
happened, by how far ahead it looked, gives the team's measured track record:
error by horizon and a test for a persistent bias that a single holdout year
cannot tell apart from one noisy year.

Synthetic mode: vintages as of 2023-06, 2023-12, 2024-06, 2024-12 and 2025-06
are rebuilt from the data as it stood then (eligibility to the as-of month,
claims paid to two months later), each forecast with today's code and approved
assumptions. Caveat: the book lapse indications and the claim-cost curves used
by the forecast were estimated on the full experience period, i.e. with
hindsight of later data, so the rebuilt vintages are slightly better informed
than real forecasts would have been. (Med Supp and D&V trend is the public
benchmark, not fitted to the book, so it carries no hindsight.) Medicaid churn,
trend, rate update, completion and the claims base are re-estimated from each cut.

Production mode: this cycle's forecast (09's output) is appended to a
persistent registry (NPV_VINTAGE_DIR, default <output>/vintage_registry; the
clean start never clears it; re-running a cycle replaces its vintage, never
duplicates it), and every stored vintage is scored.

Scoring rule. A month is scored only if it has at least 3 months of claim
run-out at today's paid-through date (completion of roughly 92% or more), so
the score is not dominated by IBNR estimates. Metrics by line: member months,
premium, pooled incurred PMPM and all-in incurred dollars; signed error =
forecast / actual - 1 (positive = forecast too high). Horizon buckets 1-3, 4-6,
7-12 and 13-24 months.

Persistent-bias test: for each vintage, the mean signed error over horizons
1-12; then the mean across vintages with its standard error and z. Vintages
six months apart overlap in the months they score, so their errors are
positively correlated and the SE understates the true uncertainty: |z| > 2 is
a review trigger, not proof.

Outputs (../../output/forecast/): forecast_accuracy_by_horizon.csv,
forecast_bias_test.csv, forecast_vintage_scores.csv (every scored month).
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import OUTPUT_DIR  # noqa: E402
import assumption_control as ac  # noqa: E402
import forecast_lib as fl  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = OUTPUT_DIR / "forecast"
METRICS = ["member_months", "premium", "incurred_pmpm", "incurred"]


def registry_dir() -> Path:
    return Path(os.environ.get("NPV_VINTAGE_DIR", OUTPUT_DIR / "vintage_registry"))


def line_month(frame: pd.DataFrame) -> pd.DataFrame:
    g = frame.groupby(["lob", "month_mi"])[["member_months", "premium", "incurred_pooled", "incurred"]].sum().reset_index()
    g["incurred_pmpm"] = g["incurred_pooled"] / g["member_months"]
    return g


def score(vintages: dict, act: pd.DataFrame, P: int, E: int, cfg: dict) -> pd.DataFrame:
    vc = cfg["vintages"]
    a = line_month(act).set_index(["lob", "month_mi"])
    rows = []
    for as_of, frame in vintages.items():
        Ev = fl.mi(as_of)
        f = line_month(frame)
        f = f[(f["month_mi"] <= min(E, P - vc["min_runout_months_to_score"])) & (f["month_mi"] > Ev)]
        for r in f.itertuples(index=False):
            if (r.lob, r.month_mi) not in a.index:
                continue
            ar = a.loc[(r.lob, r.month_mi)]
            for m in METRICS:
                av, fv = float(ar[m]), float(getattr(r, m))
                if av:
                    rows.append(dict(vintage=as_of, lob=r.lob, month=fl.ms(r.month_mi), horizon=int(r.month_mi - Ev),
                                     metric=m, forecast=fv, actual=av, error=fv / av - 1))
    s = pd.DataFrame(rows, columns=["vintage", "lob", "month", "horizon", "metric", "forecast", "actual", "error"])
    buckets = vc["horizon_buckets"]
    if s.empty:            # first production cycle: no earlier vintage has matured actuals yet
        s["bucket"] = pd.Series(dtype=str)
        return s
    s["bucket"] = pd.cut(s["horizon"], bins=[b[0] - 1 for b in buckets] + [buckets[-1][1]],
                         labels=[f"{b[0]}-{b[1]}" for b in buckets])
    return s


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = fl.load_config()
    run = ac.run_config()
    book = fl.load_book()
    vintages: dict[str, pd.DataFrame] = {}
    if run.get("mode") == "production":
        cur = OUT / "inforce_forecast_monthly.csv"
        if not cur.exists():
            raise SystemExit("run 09_inforce_forecast.py first: this cycle's forecast is the vintage to register")
        frame = pd.read_csv(cur, dtype={"rate_cell": str, "plan": str, "state": str})
        fl.register_vintage(registry_dir(), run["run_id"], frame,
                            dict(as_of=fl.ms(book.elig_as_of), paid_through=fl.ms(book.paid_through),
                                 registered_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        reg = fl.read_registry(registry_dir())
        for r in reg.itertuples(index=False):
            vintages[r.as_of] = pd.read_csv(registry_dir() / r.file)
        print(f"Registry {registry_dir()}: {len(reg)} vintage(s); this cycle registered as {run['run_id']}")
    else:
        for as_of in cfg["vintages"]["synthetic_as_of"]:
            E, P = fl.vintage_dates(cfg, as_of)
            vintages[as_of] = fl.forecast(fl.cut_book(book, E, P), cfg=cfg, horizon=24).frame
    act = fl.actuals(book, cfg)
    s = score(vintages, act, book.paid_through, book.elig_as_of, cfg)
    if s.empty:
        # Expected on the first production cycle(s): the registry holds this cycle's vintage only.
        # Write the files with their headers so downstream readers (12, export) see "no rows", not
        # an unreadable file.
        print("No vintage has a scoreable month yet (needs a prior vintage and 3 months of run-out).")
        s.to_csv(OUT / "forecast_vintage_scores.csv", index=False)
        pd.DataFrame(columns=["lob", "metric", "bucket", "n", "vintages", "mape", "bias", "worst"]).to_csv(
            OUT / "forecast_accuracy_by_horizon.csv", index=False)
        pd.DataFrame(columns=["lob", "metric", "vintages", "mean_bias", "sd", "se", "z", "flag", "caveat"]).to_csv(
            OUT / "forecast_bias_test.csv", index=False)
        return 0
    s.to_csv(OUT / "forecast_vintage_scores.csv", index=False)
    acc = (s.groupby(["lob", "metric", "bucket"], observed=True)
           .agg(n=("error", "size"), vintages=("vintage", "nunique"), mape=("error", lambda e: float(np.mean(np.abs(e)))),
                bias=("error", "mean"), worst=("error", lambda e: float(e.iloc[np.argmax(np.abs(e.to_numpy()))])))
           .reset_index())
    acc.to_csv(OUT / "forecast_accuracy_by_horizon.csv", index=False)
    per_v = s[s["horizon"] <= 12].groupby(["lob", "metric", "vintage"])["error"].mean().reset_index()
    bt = per_v.groupby(["lob", "metric"]).agg(vintages=("vintage", "nunique"), mean_bias=("error", "mean"),
                                              sd=("error", "std")).reset_index()
    bt["se"] = bt["sd"] / np.sqrt(bt["vintages"])
    bt["z"] = bt["mean_bias"] / bt["se"]
    bt["flag"] = np.where(bt["z"].abs() > 2, "REVIEW: persistent bias", "no persistent bias")
    bt["caveat"] = "overlapping vintages: errors are positively correlated, so SE is understated"
    bt.to_csv(OUT / "forecast_bias_test.csv", index=False)

    print(f"Vintages scored: {', '.join(vintages)}; months with >= {cfg['vintages']['min_runout_months_to_score']} months run-out at {fl.ms(book.paid_through)}")
    show = acc[acc["metric"].isin(["member_months", "premium", "incurred_pmpm"])].pivot_table(
        index=["lob", "metric"], columns="bucket", values="mape", observed=True)
    print("MAPE by horizon (months ahead):")
    print(show.to_string(float_format=lambda x: f"{x:.1%}"))
    print("Persistent-bias test (mean signed error, horizons 1-12):")
    print(bt[["lob", "metric", "vintages", "mean_bias", "se", "z", "flag"]].to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"{time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

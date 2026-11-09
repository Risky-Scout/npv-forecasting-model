"""
09_inforce_forecast.py  (v4)
-------------------------------------------------------------------------------
In-force financial forecast of the existing book: 36 calendar months from the
month after the last eligibility month, by line x state x plan / rate cell x
duration band x cohort (in-force at the as-of date vs new sales), on the
approved assumptions (Medicaid on its own book experience). Method:
forecast_lib.py.

This is the plan / outlook deliverable of a forecasting team: membership,
premium, incurred claims (completed, all-in), paid claims and the run-off of the
IBNR held at the as-of date, with the loss ratio by month, quarter and year.

Outputs (../../output/forecast/):
    inforce_forecast_monthly.csv          tidy monthly frame (every segment)
    inforce_forecast_summary_annual.csv   by line x calendar year
    inforce_forecast_summary_quarterly.csv
    completion_cl_vs_bf.csv               latest incurred months: chain ladder vs Bornhuetter-Ferguson
    segment_credibility.csv               segment A/E, credibility Z and relativity used
    forecast_diagnostics.json             every estimated parameter and its source
    inforce_forecast.png                  history (completed) and forecast by line
All data is the synthetic practice book; not Humana data.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import OUTPUT_DIR  # noqa: E402
import assumption_control as ac  # noqa: E402
import forecast_lib as fl  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = OUTPUT_DIR / "forecast"
LABEL = {"MEDSUPP": "Medicare Supplement", "DENTAL_VISION": "Dental & Vision", "MEDICAID": "Medicaid (existing book)"}


def summarize(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Financial summary by line and calendar year ('Y') or quarter ('Q')."""
    f = frame.copy()
    per = pd.PeriodIndex(f["month"], freq="M")
    f["period"] = (per.year.astype(str) if period == "Y" else per.year.astype(str) + "Q" + per.quarter.astype(str))
    s = f.groupby(["lob", "period"]).agg(months=("month", "nunique"), member_months=("member_months", "sum"),
                                          premium=("premium", "sum"), incurred_pooled=("incurred_pooled", "sum"),
                                          pooling_charge=("pooling_charge", "sum"), incurred=("incurred", "sum"),
                                          paid=("paid", "sum"), ibnr_runoff=("ibnr_runoff", "sum"),
                                          new_members=("new_members", "sum"), terminations=("lapses", "sum"),
                                          deaths=("deaths", "sum")).reset_index()
    last = f[f["month"] == f.groupby("lob")["month"].transform("max")]
    end = (f.groupby(["lob", "period", "month"])["members_end"].sum().reset_index()
           .sort_values("month").groupby(["lob", "period"]).tail(1).set_index(["lob", "period"])["members_end"])
    s = s.join(end.rename("members_end"), on=["lob", "period"])
    s["average_members"] = s["member_months"] / s["months"]
    s["premium_pmpm"] = s["premium"] / s["member_months"]
    s["incurred_pmpm"] = s["incurred"] / s["member_months"]
    s["loss_ratio"] = s["incurred"] / s["premium"]
    del last
    return s


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = fl.load_config()
    run = ac.run_config()
    book = fl.load_book()
    if fl.ms(book.paid_through) != run["paid_through"]:
        raise SystemExit(f"paid-through in the data {fl.ms(book.paid_through)} differs from run_config {run['paid_through']}")
    gap = book.paid_through - book.elig_as_of
    if gap != cfg["eligibility_lag_months"]["value"]:
        print(f"NOTE: eligibility-to-paid-through gap is {gap} months; config says {cfg['eligibility_lag_months']['value']} "
              "(used only to reconstruct vintages).")
    res = fl.forecast(book, cfg=cfg)
    f = res.frame
    f.to_csv(OUT / "inforce_forecast_monthly.csv", index=False)
    ann, qtr = summarize(f, "Y"), summarize(f, "Q")
    ann.to_csv(OUT / "inforce_forecast_summary_annual.csv", index=False)
    qtr.to_csv(OUT / "inforce_forecast_summary_quarterly.csv", index=False)
    comp = res.completion
    recent = comp[comp["inc_mi"] > book.elig_as_of - 6][["lob", "month", "observable_lag", "member_months", "paid_to_date",
                                                          "completion_factor", "cl_ultimate", "apriori", "bf_ultimate", "method"]].copy()
    recent["bf_minus_cl"] = recent["bf_ultimate"] - recent["cl_ultimate"]
    recent["cl_pmpm"] = recent["cl_ultimate"] / recent["member_months"]
    recent["bf_pmpm"] = recent["bf_ultimate"] / recent["member_months"]
    recent.to_csv(OUT / "completion_cl_vs_bf.csv", index=False)
    res.credibility.to_csv(OUT / "segment_credibility.csv", index=False)
    diag = res.diagnostics | {"ibnr_at_as_of": res.ibnr_at_as_of.to_dict(orient="records"),
                              "rate_actions_inferred_from_data": fl.infer_rate_actions(book, cfg),
                              "run_id": run.get("run_id"), "mode": run.get("mode")}
    (OUT / "forecast_diagnostics.json").write_text(json.dumps(diag, indent=2, default=float) + "\n")

    # Reconciliation: IBNR allocated to segments equals the line IBNR; roll-forward conserves members.
    for r in res.ibnr_at_as_of.itertuples(index=False):
        assert abs(r.ibnr_pooled - r.ibnr_line_check) < 0.01, f"{r.lob}: segment IBNR does not tie to the line"
        assert abs(f.loc[f["lob"] == r.lob, "ibnr_runoff"].sum() - r.ibnr_pooled) < 0.01, f"{r.lob}: IBNR run-off != IBNR held"
    for lob, g in f.groupby("lob"):
        m = g.groupby("month")[["members_start", "new_members", "deaths", "lapses", "members_end"]].sum()
        assert np.allclose(m["members_end"].to_numpy()[:-1], m["members_start"].to_numpy()[1:], atol=1e-6), f"{lob}: roll-forward broken"

    # Chart: completed history (last 24 months) and forecast, premium vs incurred, by line.
    act = fl.actuals(book, cfg)
    fig, axes = plt.subplots(3, 1, figsize=(11, 11))
    for ax, lob in zip(axes, fl.LOBS):
        h = act[(act["lob"] == lob) & (act["month_mi"] > book.elig_as_of - 24)].groupby("month")[["premium", "incurred"]].sum()
        g = f[f["lob"] == lob].groupby("month")[["premium", "incurred"]].sum()
        xh, xg = pd.PeriodIndex(h.index, freq="M").to_timestamp(), pd.PeriodIndex(g.index, freq="M").to_timestamp()
        ax.plot(xh, h["premium"] / 1e3, color="#1F3864", label="premium (actual)")
        ax.plot(xh, h["incurred"] / 1e3, color="#A23B2A", label="incurred, completed (actual)")
        ax.plot(xg, g["premium"] / 1e3, color="#1F3864", ls="--", label="premium (forecast)")
        ax.plot(xg, g["incurred"] / 1e3, color="#A23B2A", ls="--", label="incurred (forecast)")
        ax.axvline(xh[-1], color="grey", ls=":", lw=1)
        lr = ann[ann["lob"] == lob].iloc[0]
        ax.set_title(f"{LABEL[lob]}: forecast {lr['period']} loss ratio {lr['loss_ratio']:.1%}", fontsize=10)
        ax.set_ylabel("$000 per month"); ax.legend(fontsize=7, loc="upper left")
    fig.suptitle(f"In-force forecast as of {fl.ms(book.elig_as_of)} (claims paid through {fl.ms(book.paid_through)}); synthetic book", fontsize=11)
    fig.tight_layout(); fig.savefig(OUT / "inforce_forecast.png", dpi=120); plt.close(fig)

    print(f"In-force forecast as of {fl.ms(book.elig_as_of)}, paid through {fl.ms(book.paid_through)}, {cfg['horizon_months']} months")
    show = ann[["lob", "period", "average_members", "members_end", "premium", "incurred", "loss_ratio", "paid", "ibnr_runoff"]]
    print(show.to_string(index=False, float_format=lambda x: f"{x:,.3f}" if abs(x) < 10 else f"{x:,.0f}"))
    for lob, d in res.diagnostics["lines"].items():
        extra = f", book churn {d['annual_churn']:.1%}/yr (wave x{d['wave_multiplier']:.2f})" if lob == "MEDICAID" else ""
        print(f"  {lob:<14} trend {d['trend']:.2%} ({d['trend_source']}); rate action {d['rate_action']:.2%} ({d['rate_action_source']}); "
              f"pooling charge {d['pooling_charge']:.2%}; new sales {d['new_sales_per_month']:.0f}/month; "
              f"seasonality p = {d['seasonality_p']:.2f}{extra}")
    print(f"Wrote {len(f):,} rows to {OUT / 'inforce_forecast_monthly.csv'} in {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

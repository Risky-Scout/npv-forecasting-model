"""
10_actual_vs_expected.py  (v4)
-------------------------------------------------------------------------------
Actual-vs-expected variance waterfall: why did the year come in different from
the forecast? This is the monthly / quarterly explanation a forecasting team
owes Finance ("own the assumptions and explain A/E").

Expected = the forecast made at an earlier vintage. Synthetic mode rebuilds it
from the data as it stood then (eligibility to 2024-12, claims paid to
2025-02); production takes the prior cycle's vintage from the registry when
there is one, and otherwise rebuilds it the same way. Actual = the same months
on current data, pooled claims completed with the current BF / chain-ladder
basis, plus the large-claim excess actually paid.

Claims waterfall (per line), every step in dollars, summing EXACTLY to the
total variance (asserted):
    expected incurred (forecast, all-in)
  + volume: in-force retention   (actual - expected member months of members
                                  in force at the vintage) x expected average PMPM
  + volume: new sales            same for members issued after the vintage
  + mix                          sum of actual MM x expected PMPM by segment
                                 (state x plan / rate cell x duration x cohort)
                                 - actual MM x expected average PMPM
  + base re-estimate             sum of actual MM x (PMPM forecast from a hindsight
                                 base - PMPM forecast at the vintage): the part of the
                                 miss that came from the vintage's own completion of its
                                 base period (the same forecast rebuilt with today's
                                 run-out of the claims incurred to the vintage)
  + trend and morbidity          sum of actual MM x (actual PMPM - hindsight PMPM): what
                                 the forecast could not have known
  + large claims                 actual excess over the pooling point - pooling charge
  + prior-period development     today's estimate of the vintage's base-year incurred
                                 - the vintage's own estimate (hits reported incurred)
  = actual reported incurred
Premium: expected + volume (in-force, new) + mix + yield = actual premium.
Margin variance = premium variance - claims variance.

Outputs (../../output/forecast/): ae_waterfall.csv, ae_summary.csv, ae_segment_detail.csv,
ae_waterfall.png. All data is the synthetic practice book; not Humana data.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import os
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
SEG = ["state", "plan", "rate_cell", "duration_band", "cohort"]
CLAIM_STEPS = ["volume: in-force retention", "volume: new sales", "mix", "base re-estimate (hindsight completion)",
               "trend and morbidity", "large claims (excess vs pooling charge)", "prior-period development"]


def registry_dir() -> Path:
    return Path(os.environ.get("NPV_VINTAGE_DIR", OUTPUT_DIR / "vintage_registry"))


def expected_vintage(book: fl.Book, cfg: dict, mode: str):
    """(expected frame, vintage E, vintage P, source) - registry in production when available."""
    if mode == "production":
        reg = fl.read_registry(registry_dir())
        prior = reg[reg["as_of"] < fl.ms(book.elig_as_of)] if len(reg) else reg
        if len(prior):
            r = prior.sort_values("as_of").iloc[-1]
            frame = pd.read_csv(registry_dir() / r["file"], dtype={"rate_cell": str, "plan": str, "state": str}).fillna({"rate_cell": "", "plan": ""})
            return frame, fl.mi(r["as_of"]), fl.mi(r["paid_through"]), f"registry vintage {r['run_id']}"
        E = book.elig_as_of - 12
    else:
        E = fl.mi(cfg["ae"]["synthetic_vintage_as_of"])
    E, P = E, E + int(cfg["eligibility_lag_months"]["value"])
    return fl.forecast(fl.cut_book(book, E, P), cfg=cfg).frame, E, P, "rebuilt from the data as it stood at the vintage"


def segment_table(exp_f: pd.DataFrame, rst_f: pd.DataFrame, act: pd.DataFrame, lob: str, months: list[int]) -> pd.DataFrame:
    def agg(f, pre, inc_col="incurred_pooled"):
        g = f[(f["lob"] == lob) & (f["month_mi"].isin(months)) & (f["member_months"] > 0)]
        return g.groupby(SEG)[["member_months", "premium", inc_col]].sum().rename(
            columns={"member_months": f"mm_{pre}", "premium": f"prem_{pre}", inc_col: f"clm_{pre}"})
    t = agg(exp_f, "e").join(agg(rst_f, "r"), how="outer").join(agg(act, "a"), how="outer").fillna(0.0)
    return t.reset_index()


def waterfall(lob: str, t: pd.DataFrame, exp_f, act, months, comp_v, comp_c, E_v) -> tuple[list[dict], dict]:
    mm_e, mm_a = t["mm_e"].sum(), t["mm_a"].sum()
    avg_e = t["clm_e"].sum() / mm_e
    avg_r = t["clm_r"].sum() / t["mm_r"].sum()
    pm_e = np.where(t["mm_e"] > 0, t["clm_e"] / t["mm_e"].where(t["mm_e"] > 0, 1), avg_e)
    pm_r = np.where(t["mm_r"] > 0, t["clm_r"] / t["mm_r"].where(t["mm_r"] > 0, 1), avg_r)
    pm_a = np.where(t["mm_a"] > 0, t["clm_a"] / t["mm_a"].where(t["mm_a"] > 0, 1), 0.0)
    inf = (t["cohort"] == "INFORCE").to_numpy()
    vol_if = (t.loc[inf, "mm_a"].sum() - t.loc[inf, "mm_e"].sum()) * avg_e
    vol_new = (t.loc[~inf, "mm_a"].sum() - t.loc[~inf, "mm_e"].sum()) * avg_e
    mix = float((t["mm_a"] * pm_e).sum() - mm_a * avg_e)
    base = float((t["mm_a"] * (pm_r - pm_e)).sum())
    trend = float((t["mm_a"] * (pm_a - pm_r)).sum())
    e = exp_f[(exp_f["lob"] == lob) & (exp_f["month_mi"].isin(months))]
    a = act[(act["lob"] == lob) & (act["month_mi"].isin(months))]
    exp_pool, exp_charge = float(e["incurred_pooled"].sum()), float(e["pooling_charge"].sum())
    act_excess = float(a["excess"].sum())
    large = act_excess - exp_charge
    base_months = list(range(E_v - 11, E_v + 1))
    def est(c):
        g = c[(c["lob"] == lob) & (c["inc_mi"].isin(base_months))]
        return float(g["ultimate"].sum() + g["excess_paid"].sum())
    ppd = est(comp_c) - est(comp_v)
    expected = exp_pool + exp_charge
    actual_reported = float(a["incurred_pooled"].sum()) + act_excess + ppd
    steps = [vol_if, vol_new, mix, base, trend, large, ppd]
    total = actual_reported - expected
    # The steps add to the total BY CONSTRUCTION ("trend and morbidity" is the residual), so this assert
    # guards the bookkeeping only; tests/test_v4_forecast.py checks the other steps independently.
    assert abs(sum(steps) - total) < 0.01, f"{lob}: claims waterfall does not reconcile ({sum(steps) - total:.4f})"
    assert abs(float(a["incurred_pooled"].sum()) - float(t["clm_a"].sum())) < 0.01
    assert abs(exp_pool - float(t["clm_e"].sum())) < 0.01
    rows = [dict(lob=lob, measure="claims", step="expected incurred (forecast)", amount=expected)]
    rows += [dict(lob=lob, measure="claims", step=s, amount=v) for s, v in zip(CLAIM_STEPS, steps)]
    rows += [dict(lob=lob, measure="claims", step="actual reported incurred", amount=actual_reported)]
    # Premium: volume, mix, yield.
    pp_avg = t["prem_e"].sum() / mm_e
    pp_e = np.where(t["mm_e"] > 0, t["prem_e"] / t["mm_e"].where(t["mm_e"] > 0, 1), pp_avg)
    pp_a = np.where(t["mm_a"] > 0, t["prem_a"] / t["mm_a"].where(t["mm_a"] > 0, 1), 0.0)
    p_if = (t.loc[inf, "mm_a"].sum() - t.loc[inf, "mm_e"].sum()) * pp_avg
    p_new = (t.loc[~inf, "mm_a"].sum() - t.loc[~inf, "mm_e"].sum()) * pp_avg
    p_mix = float((t["mm_a"] * pp_e).sum() - mm_a * pp_avg)
    p_yield = float((t["mm_a"] * (pp_a - pp_e)).sum())
    pe, pa = float(t["prem_e"].sum()), float(t["prem_a"].sum())
    assert abs(p_if + p_new + p_mix + p_yield - (pa - pe)) < 0.01, f"{lob}: premium waterfall does not reconcile"
    rows += [dict(lob=lob, measure="premium", step="expected premium (forecast)", amount=pe)]
    rows += [dict(lob=lob, measure="premium", step=s, amount=v) for s, v in
             zip(["volume: in-force retention", "volume: new sales", "mix", "yield (rate and age)"], [p_if, p_new, p_mix, p_yield])]
    rows += [dict(lob=lob, measure="premium", step="actual premium", amount=pa)]
    summ = dict(lob=lob, months=f"{fl.ms(months[0])}..{fl.ms(months[-1])}", member_months_expected=mm_e, member_months_actual=mm_a,
                premium_expected=pe, premium_actual=pa, incurred_expected=expected, incurred_actual_reported=actual_reported,
                claims_ae=actual_reported / expected, loss_ratio_expected=expected / pe, loss_ratio_actual=actual_reported / pa,
                margin_variance=(pa - pe) - (actual_reported - expected))
    return rows, summ


def main() -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = fl.load_config()
    run = ac.run_config()
    book = fl.load_book()
    exp_f, E_v, P_v, source = expected_vintage(book, cfg, run.get("mode", "synthetic"))
    months = list(range(E_v + 1, min(E_v + 12, book.elig_as_of) + 1))
    if not months:
        raise SystemExit("no actual months after the vintage: nothing to compare")
    # Hindsight base: the same vintage rebuilt with today's run-out of claims incurred to E_v.
    rst = fl.forecast(fl.cut_book(book, E_v, book.paid_through), cfg=cfg)
    comp_v = fl.completion_table(fl.cut_book(book, E_v, P_v), cfg)
    comp_c = fl.completion_table(book, cfg)
    act = fl.actuals(book, cfg, vintage_E=E_v)
    rows, summs, details = [], [], []
    for lob in fl.LOBS:
        t = segment_table(exp_f, rst.frame, act, lob, months)
        r, s = waterfall(lob, t, exp_f, act, months, comp_v, comp_c, E_v)
        rows += r; summs.append(s | dict(vintage_as_of=fl.ms(E_v), vintage_paid_through=fl.ms(P_v), expected_source=source))
        details.append(t.assign(lob=lob))
    wf = pd.DataFrame(rows)
    wf["share_of_expected"] = wf["amount"] / wf.groupby(["lob", "measure"])["amount"].transform("first")
    summary = pd.DataFrame(summs)
    # Data note: a known eligibility feed defect inside the comparison window shows up as volume.
    notes = []
    md = book.mm[(book.mm["lob"] == "MEDICAID") & (book.mm["state"] == "KY")].groupby("mi").size()
    md = md.reindex(range(int(md.index.min()), int(md.index.max()) + 1), fill_value=0)
    for m in months:
        if m in md.index and (m - 1) in md.index and (m + 1) in md.index and md[m - 1] > 0:
            gap_ = (md[m - 1] + md[m + 1]) / 2 - md[m]
            if gap_ > 0.3 * md[m - 1]:
                notes.append(f"MEDICAID KY {fl.ms(m)}: about {gap_:,.0f} member months missing from eligibility (feed drop, exception DQ-0142); "
                             "their claims are dropped as orphans in silver, so both appear in 'volume: in-force retention', not as morbidity.")
    summary["data_notes"] = " | ".join(notes)
    wf.to_csv(OUT / "ae_waterfall.csv", index=False)
    summary.to_csv(OUT / "ae_summary.csv", index=False)
    pd.concat(details, ignore_index=True).to_csv(OUT / "ae_segment_detail.csv", index=False)

    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
    for ax, lob in zip(axes, fl.LOBS):
        g = wf[(wf["lob"] == lob) & (wf["measure"] == "claims")].reset_index(drop=True)
        cum, bottoms, heights, colors = 0.0, [], [], []
        for i, r in g.iterrows():
            if i == 0 or i == len(g) - 1:
                bottoms.append(0); heights.append(r["amount"]); colors.append("#1F3864"); cum = r["amount"]
            else:
                bottoms.append(cum if r["amount"] >= 0 else cum + r["amount"]); heights.append(abs(r["amount"]))
                colors.append("#A23B2A" if r["amount"] > 0 else "#4E7D2A"); cum += r["amount"]
        ax.bar(range(len(g)), np.array(heights) / 1e3, bottom=np.array(bottoms) / 1e3, color=colors)
        ax.set_xticks(range(len(g))); ax.set_xticklabels([s.split(" (")[0] for s in g["step"]], rotation=60, ha="right", fontsize=7)
        lo = min(b for b in bottoms[1:-1] + [g["amount"].iloc[0]]) / 1e3
        ax.set_ylim(lo * 0.97, max(np.array(bottoms) + np.array(heights)) / 1e3 * 1.01)
        ax.set_title(f"{lob}: claims A/E {summary.set_index('lob').loc[lob, 'claims_ae']:.3f}", fontsize=10)
        ax.set_ylabel("$000")
    fig.suptitle(f"Claims variance, {summary['months'].iloc[0]}: forecast at {fl.ms(E_v)} vs actual (red = adverse, green = favourable)", fontsize=10)
    fig.tight_layout(); fig.savefig(OUT / "ae_waterfall.png", dpi=120); plt.close(fig)

    print(f"A/E waterfall: expected = forecast at {fl.ms(E_v)} (paid through {fl.ms(P_v)}; {source}); months {summary['months'].iloc[0]}")
    piv = wf[wf["measure"] == "claims"].pivot(index="step", columns="lob", values="amount").reindex(
        ["expected incurred (forecast)"] + CLAIM_STEPS + ["actual reported incurred"])
    print(piv.to_string(float_format=lambda x: f"{x:,.0f}"))
    print(summary[["lob", "claims_ae", "loss_ratio_expected", "loss_ratio_actual", "margin_variance"]].to_string(index=False, float_format=lambda x: f"{x:,.3f}" if abs(x) < 10 else f"{x:,.0f}"))
    for n in notes:
        print("NOTE:", n)
    print(f"Reconciles by construction (trend and morbidity is the residual; other steps tested independently). {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

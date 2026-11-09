"""
12_drift_monitoring.py  (v4)
-------------------------------------------------------------------------------
Turns the monitoring outputs into a short list of alerts a forecasting team acts on each cycle.

Why: SQL 06 flags any quarter whose claims A/E is outside a fixed +/-5% band. A fixed band treats
a 3,000-member Medicaid book and a 9,000-policy Med Supp block the same, so the small block
breaches from noise and the large one hides real drift. v4 scales the band with credibility and
asks for persistence before alerting:

  Claims A/E band      +/- k x sqrt((1 + CV^2) / N)   (compound-Poisson relative SD of the quarter's
                       claims; N = claim count in the quarter, CV = claim-size coefficient of
                       variation for the line, on pooled claims)
  Termination band     +/- k x 1 / sqrt(expected terminations)   (Poisson)
  WATCH                outside the band in a quarter
  ALERT                outside the band on the same side for >= `persistence` consecutive quarters
                       (config/monitoring_rules.json)

It also collects the other v4 signals into the same list: persistent forecast bias (02 rolling
origins, 11 vintages), assumption REVIEW items without a disposition (03b), and a breached
break-even headroom (04). Alerts never stop the run: they are information for the assumption
review, recorded in output/monitoring/ and in the run manifest.

Known pattern in the synthetic book (not a model failure): SQL 06's expected basis has no
seasonality or duration, so Med Supp January termination spikes and the Medicaid 2023-24
redetermination wave show as termination alerts. That is what the monitor is for.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import OUTPUT_DIR  # noqa: E402

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = OUTPUT_DIR / "monitoring"
RULES = json.loads((ROOT / "config" / "monitoring_rules.json").read_text())


def quarter(m: pd.Series) -> pd.Series:
    p = pd.PeriodIndex(m.astype(str), freq="M")
    return pd.Series([f"{x.year}-Q{(x.month - 1) // 3 + 1}" for x in p], index=m.index)


def runs(sign: pd.Series) -> pd.Series:
    """Length of the current run of equal non-zero signs at each position (0 where sign is 0)."""
    out, n, prev = [], 0, 0
    for s in sign:
        n = n + 1 if (s != 0 and s == prev) else (1 if s != 0 else 0)
        prev = s; out.append(n)
    return pd.Series(out, index=sign.index)


def breakeven_alerts(b: pd.DataFrame, thresholds: dict) -> list[dict]:
    """Alert rows from the reverse-stress table (04_npv_engine.breakeven()).

    v4.1: a line whose base DE NPV is negative is ONE ALERT row, however far it is from break-even,
    including a line where no driver reaches break-even inside its bracket (the deepest losses): the
    below-break-even set is taken from every DE row BEFORE rows without a break-even are dropped.
    The detail lists the level each driver would need, one at a time. A profitable line with a
    driver within its configured threshold of break-even is a WATCH.
    """
    out = []
    de = b[b.measure == "de"]
    if "base_npv" in de:
        below = de[de.base_npv < 0]
    elif "position" in de:
        below = de[de.position == "below break-even"]
    else:
        below = de.iloc[0:0]
    for lob, grp in below.groupby("lob", sort=False):
        found = grp[grp.breakeven.notna()]
        need = "; ".join(f"{r.driver} {r.breakeven:.4f} (base {r.base_level:.4f})" for r in found.itertuples())
        out.append(dict(source="Reverse stress (04)", lob=lob, item="DE NPV below break-even at base", level="ALERT",
                        value=float(grp.base_npv.iloc[0]) if "base_npv" in grp else None, band="DE NPV < 0",
                        detail=(f"break-even levels, one driver at a time: {need}" if need
                                else "no single driver reaches break-even inside its bracket")))
    ok = de[~de.lob.isin(set(below.lob)) & de.breakeven.notna()]
    for r in ok.itertuples():
        thr = thresholds.get(r.driver)
        if thr is not None and abs(r.headroom) < thr:
            out.append(dict(source="Reverse stress (04)", lob=r.lob, item=f"{r.driver} break-even", level="WATCH",
                            value=r.breakeven, band=f"headroom < {thr}", detail=f"base {r.base_level:.4f}, headroom {r.headroom:+.4f}"))
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    k, persist = RULES["band_k"], RULES["persistence_quarters"]
    ae = pd.read_csv(OUTPUT_DIR / "sql_results" / "06_actual_vs_expected_monitoring.csv")
    con = duckdb.connect(str(OUTPUT_DIR / "practice.duckdb"), read_only=True)
    cl = con.execute("SELECT lob, incurred_month, paid_capped FROM silver_claims").fetchdf()
    con.close()
    cl["quarter"] = quarter(cl["incurred_month"])
    n_q = cl.groupby(["lob", "quarter"]).size().rename("n_claims")
    cv = (cl.groupby("lob")["paid_capped"].std() / cl.groupby("lob")["paid_capped"].mean()).rename("cv")
    ae = ae.merge(n_q.reset_index(), on=["lob", "quarter"], how="left").merge(cv.reset_index(), on="lob", how="left")
    ae["claims_band"] = k * np.sqrt((1 + ae["cv"] ** 2) / ae["n_claims"])
    ae["terms_band"] = k / np.sqrt(ae["expected_terms"].clip(lower=1))
    rows = []
    for lob, d in ae.sort_values(["lob", "quarter"]).groupby("lob"):
        for metric, col, band in [("claims A/E", "claims_a_to_e", "claims_band"), ("termination A/E", "termination_a_to_e", "terms_band")]:
            dev = d[col] - 1.0
            sign = np.sign(dev.where(dev.abs() > d[band], 0.0))
            run = runs(sign)
            for (_, r), s_, n_ in zip(d.iterrows(), sign, run):
                if s_ == 0:
                    continue
                level = "ALERT" if n_ >= persist else "WATCH"
                rows.append(dict(source="A/E (SQL 06)", lob=lob, item=f"{metric} {r['quarter']}", level=level,
                                 value=r[col], band=f"+/-{r[band]:.1%}",
                                 detail=f"{'above' if s_ > 0 else 'below'} band for {n_} consecutive quarter(s); fixed-band flag was {r['flag']}"))
    # Forecast bias signals (02 rolling origins and 11 vintages).
    bd = OUTPUT_DIR / "trend" / "forecast_bias_diagnostics.csv"
    if bd.exists():
        for r in pd.read_csv(bd).itertuples():
            if r.persistent_bias:
                rows.append(dict(source="Trend forecast (02)", lob=r.lob, item="persistent forecast bias", level="ALERT",
                                 value=r.rolling_mean_bias, band=f"+/-{RULES['forecast_bias_trigger']:.0%}", detail=r.verdict))
    fb = OUTPUT_DIR / "forecast" / "forecast_bias_test.csv"
    if fb.exists():
        for r in pd.read_csv(fb).itertuples():
            if abs(r.z) > RULES["vintage_bias_z"] and abs(r.mean_bias) > RULES["vintage_bias_min"]:
                rows.append(dict(source="Forecast vintages (11)", lob=r.lob, item=f"{r.metric} bias across {r.vintages} vintages",
                                 level="WATCH", value=r.mean_bias, band=f"|z| > {RULES['vintage_bias_z']}",
                                 detail=f"z = {r.z:.1f}; {r.caveat}"))
    # Assumption governance (03b).
    st = OUTPUT_DIR / "assumptions" / "assumption_status.json"
    if st.exists():
        s = json.loads(st.read_text())
        for key in s.get("undispositioned_review_items", []):
            rows.append(dict(source="Assumption governance (03b)", lob=key.split(".")[0], item=key, level="ALERT",
                             value=None, band="", detail="REVIEW item without a disposition in the approval"))
    # Break-even (04): lines below DE break-even (ALERT) and thin headroom (WATCH).
    be = OUTPUT_DIR / "npv" / "npv_breakeven.csv"
    if be.exists():
        rows += breakeven_alerts(pd.read_csv(be), RULES["breakeven_headroom"])
    al = pd.DataFrame(rows, columns=["source", "lob", "item", "level", "value", "band", "detail"])
    al.to_csv(OUT / "drift_alerts.csv", index=False)
    summ = {"alerts": int((al.level == "ALERT").sum()), "watch": int((al.level == "WATCH").sum()),
            "by_source": al.groupby(["source", "level"]).size().rename("n").reset_index().to_dict("records"),
            "rules": {k_: v for k_, v in RULES.items() if not k_.startswith("_")}}
    (OUT / "monitoring_summary.json").write_text(json.dumps(summ, indent=2) + "\n")
    print(f"Drift monitoring: {summ['alerts']} ALERT, {summ['watch']} WATCH (credibility-scaled bands, k = {k}, persistence {persist} quarters)")
    pd.set_option("display.width", 220); pd.set_option("display.max_colwidth", 90)
    if len(al):
        print(al[al.level == "ALERT"].to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.3f}" if abs(x) < 10 else f"{x:,.0f}"))
    print(f"Outputs written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

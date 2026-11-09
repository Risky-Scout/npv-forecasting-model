"""
export_results.py
Collects every figure quoted in the documentation (docs/, README) into one file,
output/results.json, straight from the pipeline outputs, so the documents cannot
disagree with the code: rerun the pipeline, rerun this script, and every quoted
number updates together. No figure is typed by hand.

Sources, in pipeline order:
    npv/          NPV summaries (current v3 cash and DE; archived v2 and v1), annual
                  projections, profit tests, v2 -> v3 bridge, tornado, Monte Carlo
    assumptions/  experience indications and the approval status of the assumption set
    backtest/     realized vs model NPV, bias attribution, estimation error,
                  process vs parameter risk
    trend/, lapse/  fitted trends, holdout scores, lapse assumptions and GLM coefficients
    sql_results/  data-quality checks, A/E monitoring, gold yearly experience, lapse study
    practice.duckdb  completion factors and record counts (read-only)
    spark_warehouse/ Databricks scenario grid
    workbook      sheet list and formula count of the Excel model
"""
import glob
import json
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent / 'code' / 'python'))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
O = OUTPUT_DIR
R = {}   # every exported figure, keyed by the section of the documents that uses it

# --- NPV results: current model (v3), archived models (v2, v1), projections, sensitivities ---
import importlib.util
s = pd.read_csv(O / "npv" / "npv_summary.csv").set_index("lob")
R["current"] = s.where(s.notna(), None).to_dict("index")
_sp = importlib.util.spec_from_file_location("v2", ROOT / "code" / "python" / "archive" / "v2_monthly_npv_engine.py")
_v2 = importlib.util.module_from_spec(_sp); _sp.loader.exec_module(_v2)
R["v2_archived"] = {lob: {"npv": _v2.npv(lob, _v2.A[lob], _v2.A["global"])} for lob in _v2.PROJECT}
R["v1"] = pd.read_csv(O / "npv_v1" / "npv_summary.csv").set_index("lob").to_dict("index")
br = pd.read_csv(O / "npv" / "npv_bridge_v2_to_v4.csv")
R["bridge"] = br.where(br.notna(), None).to_dict("records")
for lob in ["MEDSUPP", "DENTAL_VISION", "MEDICAID"]:
    R["annual_" + lob] = pd.read_csv(O / "npv" / f"npv_projection_{lob}.csv").to_dict("list")
    R["profit_" + lob] = pd.read_csv(O / "npv" / f"profit_test_{lob}.csv").to_dict("list")
ind = pd.read_csv(O / "assumptions" / "assumption_indications.csv")
R["assumption_indications"] = ind.where(ind.notna(), None).to_dict("records")
R["assumption_status"] = json.loads((O / "assumptions" / "assumption_status.json").read_text())
R["tornado"] = pd.read_csv(O / "npv" / "npv_tornado.csv").to_dict("records")
mcs = pd.read_csv(O / "npv" / "npv_monte_carlo_stats.csv", index_col=0)
R["mc"] = mcs.where(mcs.notna(), None).to_dict("index")   # NaN -> null, which JSON can represent
mc = pd.read_csv(O / "npv" / "npv_monte_carlo.csv")
# Histograms are binned here so the charts in both documents use identical bins.
def hist(x, bins=30):
    c, e = np.histogram(x, bins=bins)
    return {"counts": c.tolist(), "mid": ((e[:-1] + e[1:]) / 2).tolist()}
R["mc_hist"] = {l: hist(mc[l]) for l in mc}

# --- Backtest: how well past NPV predictions held up ---
B = O / "backtest"
R["testA"] = pd.read_csv(B / "test_a_realized_vs_model.csv").set_index("lob").to_dict("index")
at = pd.read_csv(B / "test_a_bias_attribution.csv")
R["attr"] = at.where(at.notna(), None).to_dict("records")
tb = pd.read_csv(B / "test_b_estimation_error.csv")
R["testB"] = tb.where(tb.notna(), None).to_dict("records")
R["testC"] = pd.read_csv(B / "test_c_process_vs_parameter.csv").set_index("lob").to_dict("index")
real = pd.read_csv(B / "realized_npv_draws.csv")
R["real_hist"] = {l: hist(real[l]) for l in real}

# --- Assumption studies: claims trend and lapse (GLM coefficients by line) ---
R["holdout"] = pd.read_csv(O / "trend" / "holdout_scores.csv").to_dict("records")
R["fitted_trends"] = pd.read_csv(O / "trend" / "fitted_trends.csv").set_index("lob").to_dict("index")
R["lapse"] = pd.read_csv(O / "lapse" / "lapse_assumption_table.csv").to_dict("records")
glm = pd.read_csv(O / "lapse" / "lapse_glm_coefficients_MEDSUPP.csv", index_col=0)
R["glm_ms"] = glm.to_dict("index")
R["glm_dv"] = pd.read_csv(O / "lapse" / "lapse_glm_coefficients_DENTAL_VISION.csv", index_col=0).to_dict("index")
R["glm_md"] = pd.read_csv(O / "lapse" / "lapse_glm_coefficients_MEDICAID.csv", index_col=0).to_dict("index")

# --- SQL kit outputs: data quality, A/E monitoring, gold experience, lapse study ---
R["dq"] = pd.read_csv(O / "sql_results" / "01_data_quality_checks.csv").to_dict("records")
R["ae"] = pd.read_csv(O / "sql_results" / "06_actual_vs_expected_monitoring.csv").to_dict("records")
R["yearly_exp"] = pd.read_csv(O / "sql_results" / "05_npv_assumption_inputs_gold.csv").to_dict("records")
R["lapse_sql"] = pd.read_csv(O / "sql_results" / "04_lapse_persistency_study.csv").to_dict("records")

# --- Tables and record counts straight from the SQL database (read-only, nothing is rebuilt) ---
con = duckdb.connect(str(O / "practice.duckdb"), read_only=True)
R["cf"] = con.execute("SELECT lob, lag, ata_factor, completion_factor FROM completion_factors ORDER BY lob, lag").fetchdf().to_dict("records")
q = lambda x: con.execute(x).fetchone()[0]   # run a query that returns a single value
# Bronze vs silver paid shows the dollars removed by cleaning. The KY July 2025
# orphan count traces those claims to the missing eligibility month (anti-join).
R["counts"] = {
    "members": q("SELECT COUNT(*) FROM members"), "member_months": q("SELECT COUNT(*) FROM member_months"),
    "claims": q("SELECT COUNT(*) FROM claims"),
    "bronze_paid": q("SELECT SUM(paid_amount) FROM claims"), "silver_paid": q("SELECT SUM(paid_amount) FROM silver_claims"),
    "orphans_ky_jul": q("""SELECT COUNT(*) FROM claims c LEFT JOIN (SELECT DISTINCT member_id, month FROM member_months) e
                           ON e.member_id = c.member_id AND e.month = c.incurred_month JOIN members m ON m.member_id = c.member_id
                           WHERE e.member_id IS NULL AND c.incurred_month = '2025-07' AND m.state = 'KY'"""),
}
R["counts"]["orphan_paid"] = R["counts"]["bronze_paid"] - R["counts"]["silver_paid"]
con.close()

# --- Databricks scenario grid (lapse multiplier x trend add-on), read from the Spark warehouse parquet ---
grid = pd.concat([pd.read_parquet(f) for f in glob.glob(str(O / "spark_warehouse" / "npv_practice.db" / "gold_npv_grid" / "*.parquet"))])
R["grid"] = grid.sort_values(["lapse_mult", "trend_add"]).to_dict("records")

# --- v4 outputs: reverse stress, planning scenarios, MC attribution, model points, trend method,
#     in-force forecast, A/E waterfall, forecast accuracy, drift monitoring ---
rec = lambda f: (lambda d: d.where(d.notna(), None).to_dict("records"))(pd.read_csv(f))
R["v4"] = {
    "breakeven": rec(O / "npv" / "npv_breakeven.csv"),
    "planning_scenarios": rec(O / "npv" / "npv_planning_scenarios.csv"),
    "mc_attribution": rec(O / "npv" / "npv_monte_carlo_attribution.csv"),
    "medsupp_model_points": rec(O / "npv" / "medsupp_model_points.csv"),
    "rolling_origin_bias": rec(O / "trend" / "forecast_bias_diagnostics.csv"),
    "forecast_annual": rec(O / "forecast" / "inforce_forecast_summary_annual.csv"),
    "ae_waterfall": rec(O / "forecast" / "ae_waterfall.csv"),
    "forecast_accuracy": rec(O / "forecast" / "forecast_accuracy_by_horizon.csv"),
    "forecast_bias_test": rec(O / "forecast" / "forecast_bias_test.csv"),
    "monitoring": json.loads((O / "monitoring" / "monitoring_summary.json").read_text()),
}
# --- v4.1 outputs: judgment and Monte Carlo parameter sensitivity, rating bases, D&V product options,
#     DAC tax schedule, drift alerts ---
R["v41"] = {k: rec(O / sub / f) for k, sub, f in [
    ("judgment_sensitivity", "npv", "npv_judgment_sensitivity.csv"),
    ("mc_parameter_sensitivity", "npv", "npv_mc_parameter_sensitivity.csv"),
    ("medsupp_rating_bases", "npv", "medsupp_rating_bases.csv"),
    ("dv_product_options", "npv", "dv_product_options.csv"),
    ("dac_tax_MEDSUPP", "npv", "dac_tax_MEDSUPP.csv"),
    ("drift_alerts", "monitoring", "drift_alerts.csv"),
    ("pricing_solve", "npv", "npv_pricing_solve.csv"),            # v4.2
    ("medsupp_cell_pricing", "npv", "medsupp_cell_pricing.csv")] if (O / sub / f).exists()}

# --- Excel model: sheet list and formula count, quoted when describing the workbook ---
from openpyxl import load_workbook
wb = load_workbook(O / "Humana_NPV_Forecasting_Model.xlsx")
R["workbook"] = {"sheets": wb.sheetnames,
                 "formulas": sum(1 for ws in wb.worksheets for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("="))}

# default=float converts numpy scalars, which the json module cannot serialize on its own.
json.dump(R, open(O / "results.json", "w"), indent=1, default=float)
print("written", O / "results.json")
# Quick console check: NPV by line and the workbook formula count
print({k: (round(v["npv"]), round(v["de_npv"])) for k, v in R["current"].items()}, R["workbook"]["formulas"])

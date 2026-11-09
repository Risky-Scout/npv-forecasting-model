# Databricks notebook source
# MAGIC %md
# MAGIC # NPV Experience Pipeline — Medicare Supplement, Dental & Vision, Medicaid
# MAGIC
# MAGIC **Purpose.** Bronze → Silver → Gold pipeline that turns raw eligibility and claims into the
# MAGIC assumption inputs an NPV model needs, with data-quality gates, chain-ladder completion,
# MAGIC a lapse study, and a distributed sensitivity grid.
# MAGIC
# MAGIC | Layer | Table | What it holds |
# MAGIC |---|---|---|
# MAGIC | Bronze | `bronze_members`, `bronze_member_months`, `bronze_claims` | raw files, typed, untouched |
# MAGIC | Silver | `silver_member_months`, `silver_claims` | de-duplicated, orphans removed, large claims pooled |
# MAGIC | Gold | `gold_monthly_experience`, `gold_lapse_study`, `gold_npv_grid` | analysis-ready, what the NPV reads |
# MAGIC
# MAGIC **Why three layers.** Bronze is the audit copy of what the source systems sent, so any
# MAGIC cleaning rule can be re-run or reversed without going back to the feed. Silver applies the
# MAGIC business rules once (dedupe, orphan removal, pooling) so every study reads the same clean
# MAGIC data. Gold is small, aggregated and stable: it is the layer the experience studies and the
# MAGIC assumption indications read. (The NPV engine itself reads the approved assumption file;
# MAGIC data reaches the model through those studies and the approval.) Gold is the layer whose
# MAGIC version needs to be recorded against a set of results.
# MAGIC
# MAGIC **How to run.** In Databricks: attach to any cluster (or serverless), upload the three CSVs
# MAGIC from `data/` to a Volume, set the `data_path` widget, Run All. Tables are written as Delta.
# MAGIC Locally: `python npv_pipeline_databricks.py` with `pyspark` installed — it falls back to Parquet.
# MAGIC
# MAGIC **Reproducibility.** Each gold table is a Delta table, so every NPV run can record the table
# MAGIC version it used and rerun later with `VERSION AS OF` (time travel).
# MAGIC
# MAGIC **Governance.** Bronze and Silver hold member-level eligibility and claims (PHI). On Unity
# MAGIC Catalog these belong in a restricted schema with grants limited to the experience-study
# MAGIC team; Gold is aggregated to line/month or line/duration and can be shared more widely.

# COMMAND ----------

# ---------------------------------------------------------------------------------------------
# Environment and parameters
#   The same file runs as a Databricks notebook and as a local PySpark script. Differences:
#     - Databricks: existing SparkSession, widgets for parameters, Delta tables in a managed
#       schema (Unity Catalog: <catalog>.<schema>.<table>; the widget default points at a Volume).
#     - Local: a fresh local[*] session, a throwaway warehouse folder, Parquet instead of Delta
#       (no time travel, no DESCRIBE HISTORY), so section 7 skips the version capture.
# ---------------------------------------------------------------------------------------------
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

ON_DATABRICKS = "DATABRICKS_RUNTIME_VERSION" in os.environ
if ON_DATABRICKS:
    spark = SparkSession.builder.getOrCreate()
else:   # local run: keep the warehouse inside the project's output folder
    import shutil
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
    from kitpaths import DATA_DIR, OUTPUT_DIR  # relocatable data / output (kitpaths.py)
    # Spark's Python workers must use this interpreter (with pyarrow/pandas installed), not whatever
    # 'python3' happens to be first on PATH.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    _wh = OUTPUT_DIR / "spark_warehouse"
    shutil.rmtree(_wh, ignore_errors=True)   # local practice runs start clean (no persistent metastore)
    # 8 shuffle partitions instead of the default 200: the data is small and a laptop has few cores
    spark =(SparkSession.builder.appName("npv_pipeline").master("local[*]")
             .config("spark.sql.warehouse.dir", str(_wh))
             .config("spark.sql.shuffle.partitions", "8")
             .config("spark.ui.showConsoleProgress", "false").getOrCreate())
spark.sparkContext.setLogLevel("ERROR")

# Widgets parameterize the notebook, so a Databricks Workflow (job) can pass a different source
# path or target schema per run without editing code. `dbutils` only exists on Databricks; the
# NameError branch is the local fallback.
try:                                  # Databricks: notebook widgets
    dbutils.widgets.text("data_path", "/Volumes/main/actuarial/raw")      # noqa: F821
    dbutils.widgets.text("schema", "npv_practice")                         # noqa: F821
    DATA_PATH = dbutils.widgets.get("data_path")                           # noqa: F821
    SCHEMA = dbutils.widgets.get("schema")                                 # noqa: F821
    dbutils.widgets.text("repo_root", "/Workspace/Repos/actuarial/npv_kit")  # noqa: F821
    REPO = Path(dbutils.widgets.get("repo_root"))                          # noqa: F821
    FORMAT = "delta"
except NameError:                     # local run
    HERE = Path(__file__).resolve().parent
    REPO = HERE.parents[1]
    DATA_PATH = str(DATA_DIR)
    SCHEMA = "npv_practice"
    FORMAT = "parquet"

spark.sql(f"CREATE DATABASE IF NOT EXISTS {SCHEMA}")
spark.sql(f"USE {SCHEMA}")
# Claims paid-through month: the evaluation date of the triangle. Incurred months close to it are
# immature and are completed in section 4. Months are 'YYYY-MM' strings throughout; zero padding
# makes string comparison equivalent to date comparison.
# Read from the same run configuration as the local pipeline (one source for every run setting).
sys.path.insert(0, str(REPO / "code" / "python"))
import assumption_control as _ac  # noqa: E402  (shared reader of run_config.json and the assumption file)
PAID_THROUGH = _ac.run_config()["paid_through"]


def save(df, name: str):
    """Write `df` as a managed table (full overwrite) and return the table re-read from storage.

    Re-reading means downstream steps work from the persisted table, not a lazily re-computed
    plan. Overwrite is a full refresh each run; on Delta the prior version is kept and can be
    restored with time travel. An incremental production feed would use MERGE instead.
    """
    df.write.mode("overwrite").format(FORMAT).saveAsTable(f"{SCHEMA}.{name}")
    return spark.table(f"{SCHEMA}.{name}")

# COMMAND ----------

# MAGIC %md ## 1. Bronze — typed raw ingest (explicit schemas: never let types be guessed in production)
# MAGIC Schema inference samples the file and can silently read an ID as a double, drop leading zeros
# MAGIC or change type when a new file arrives. An explicit schema makes a malformed feed visible.

# COMMAND ----------

# ---------------------------------------------------------------------------------------------
# Explicit schemas. Months stay as 'YYYY-MM' strings (as delivered); term_month / term_reason are
# null for members still in force.
# ---------------------------------------------------------------------------------------------
members_schema = T.StructType([
    T.StructField("member_id", T.LongType()), T.StructField("lob", T.StringType()),
    T.StructField("state", T.StringType()), T.StructField("plan", T.StringType()),
    T.StructField("rate_cell", T.StringType()), T.StructField("issue_month", T.StringType()),
    T.StructField("issue_age", T.IntegerType()), T.StructField("term_month", T.StringType()),
    T.StructField("term_reason", T.StringType())])
mm_schema = T.StructType([
    T.StructField("member_id", T.LongType()), T.StructField("lob", T.StringType()),
    T.StructField("month", T.StringType()), T.StructField("state", T.StringType()),
    T.StructField("plan", T.StringType()), T.StructField("rate_cell", T.StringType()),
    T.StructField("premium", T.DoubleType())])
claims_schema = T.StructType([
    T.StructField("claim_id", T.LongType()), T.StructField("member_id", T.LongType()),
    T.StructField("lob", T.StringType()), T.StructField("incurred_month", T.StringType()),
    T.StructField("paid_month", T.StringType()), T.StructField("paid_amount", T.DoubleType()),
    T.StructField("service_category", T.StringType())])

read = lambda f, s: spark.read.csv(f"{DATA_PATH}/{f}", header=True, schema=s)
bronze_members = save(read("members.csv", members_schema), "bronze_members")
bronze_mm = save(read("member_months.csv", mm_schema), "bronze_member_months")
bronze_claims = save(read("claims.csv", claims_schema), "bronze_claims")

# Control totals: record what came in so every downstream step can reconcile to it.
# Counts are taken from the persisted tables (not the DataFrames) so they reflect what was
# actually written; they also go into the run log in section 7 as the audit trail for this load.
control = {t: spark.table(f"{SCHEMA}.{t}").count()
           for t in ["bronze_members", "bronze_member_months", "bronze_claims"]}
control["bronze_paid_total"] = bronze_claims.agg(F.sum("paid_amount")).first()[0]
print(json.dumps(control, indent=2, default=float))

# COMMAND ----------

# MAGIC %md ## 2. Data-quality gate (blockers stop the run; warnings are logged)
# MAGIC A failed assertion fails the cell, which fails the Workflow task, so downstream tasks (and any
# MAGIC NPV run reading Gold) do not execute on bad data. Everything else is counted and logged.

# COMMAND ----------

# ---------------------------------------------------------------------------------------------
# Checks (each returns a row count; 0 is clean)
#   duplicate_member_months    : same member and month delivered more than once -> overstated exposure
#   claims_without_eligibility : claims with no member-month in the incurred month (orphans)
#   paid_before_incurred       : impossible lag; indicates a date-field mapping error
#   negative_paid_reversals    : adjustments; expected, netted within the incurred month
#   claims_over_500k           : reasonability flag for very large single claims
# ---------------------------------------------------------------------------------------------
elig_keys = bronze_mm.select("member_id", F.col("month").alias("incurred_month")).distinct()
checks = {
    "duplicate_member_months": bronze_mm.count() - bronze_mm.select("member_id", "month").distinct().count(),
    "claims_without_eligibility": bronze_claims.join(elig_keys, ["member_id", "incurred_month"], "left_anti").count(),
    "paid_before_incurred": bronze_claims.filter(F.col("paid_month") < F.col("incurred_month")).count(),
    "negative_paid_reversals": bronze_claims.filter(F.col("paid_amount") < 0).count(),
    "claims_over_500k": bronze_claims.filter(F.col("paid_amount") > 500000).count(),
}

# Missing month by line/state: build the expected calendar and anti-join.
# A whole line/state disappearing for a month is a feed drop, not real disenrollment; left in, it
# would read as a membership dip and a lapse spike. The calendar is every month seen anywhere in
# the file, from each line/state's first month onward.
cal = bronze_mm.select("month").distinct()
first_seen = bronze_mm.groupBy("lob", "state").agg(F.min("month").alias("first_month"))
expected = first_seen.crossJoin(cal).filter(F.col("month") >= F.col("first_month")).select("lob", "state", "month")
actual = bronze_mm.select("lob", "state", "month").distinct()
missing = expected.join(actual, ["lob", "state", "month"], "left_anti")
checks["missing_lob_state_months"] = missing.count()

dq = pd.DataFrame([{"check": k, "flagged": int(v)} for k, v in checks.items()])
print(dq.to_string(index=False))
if checks["missing_lob_state_months"]:
    print("Missing months:", [tuple(r) for r in missing.collect()])

# Gate (Phase 1): the same versioned policy, exceptions register and rules as the local pipeline
# (config/dq_policy.json, config/dq_exceptions.json, code/python/controls.py). Every BLOCKER is either
# repaired by its approved cleaning rule within tolerance, or covered by an unexpired exception for that
# exact line/state/month; anything else raises and fails the Workflow task. An explicit raise is used
# rather than assert, because asserts are skipped when Python runs with -O.
sys.path.insert(0, str(REPO / "code" / "python"))
import controls  # noqa: E402
_cfg = lambda name: json.loads((REPO / "config" / name).read_text())
run_cfg, dq_policy, dq_exceptions = _ac.run_config(), _cfg("dq_policy.json"), _cfg("dq_exceptions.json")["exceptions"]
paid_by = {r["lob"]: r["paid"] for r in bronze_claims.groupBy("lob").agg(F.sum("paid_amount").alias("paid")).collect()}
orph_by = {r["lob"]: (r["n"], r["paid"]) for r in bronze_claims.join(elig_keys, ["member_id", "incurred_month"], "left_anti")
           .groupBy("lob").agg(F.count("*").alias("n"), F.sum("paid_amount").alias("paid")).collect()}
mm_by = {r["lob"]: (r["rows"], r["keys"]) for r in bronze_mm.groupBy("lob")
         .agg(F.count("*").alias("rows"), F.countDistinct("member_id", "month").alias("keys")).collect()}
pbi_by = {r["lob"]: r["n"] for r in bronze_claims.filter(F.col("paid_month") < F.col("incurred_month"))
          .groupBy("lob").agg(F.count("*").alias("n")).collect()}
miss_by = {}
for r in missing.collect():
    miss_by.setdefault(r["lob"], []).append(f"{r['state']}|{r['month']}")
findings = []
for lob in run_cfg["lines_of_business"]:
    n_o, p_o = orph_by.get(lob, (0, 0.0))
    rows, keys = mm_by.get(lob, (0, 0))
    findings += [
        {"check": "claims_without_eligibility", "lob": lob, "count": n_o, "share": p_o / paid_by[lob], "keys": []},
        {"check": "duplicate_member_months", "lob": lob, "count": rows - keys, "share": (rows - keys) / rows, "keys": []},
        {"check": "paid_before_incurred", "lob": lob, "count": pbi_by.get(lob, 0), "share": None, "keys": []},
        {"check": "missing_eligibility_month", "lob": lob, "count": len(miss_by.get(lob, [])), "share": None,
         "keys": sorted(miss_by.get(lob, []))},
    ]
# Same rule as 01b_controls_gate.py: production judges exception expiry on today's date.
import datetime as _dt  # noqa: E402
_as_of = _dt.date.today().isoformat() if run_cfg.get("mode") == "production" else run_cfg["run_date"]
gate = controls.evaluate_gate(findings, dq_policy, dq_exceptions, _as_of)
print(pd.DataFrame(gate)[["check", "lob", "count", "status", "reason"]].to_string(index=False))
gate_failures = [g for g in gate if g["status"] == "FAIL"]
if gate_failures:
    raise RuntimeError(f"DQ gate failed: {gate_failures}")

# COMMAND ----------

# MAGIC %md ## 3. Silver — clean, conformed data
# MAGIC Dedupe eligibility, drop orphan claims, cap large claims at a line-specific pooling point,
# MAGIC then prove by reconciliation that the only paid dollars removed are the orphans.

# COMMAND ----------

# Dedupe: one row per member-month. Grouping on all dimension columns assumes duplicates are exact
# repeats; a duplicate with a conflicting state/plan/rate cell would survive as two rows, so the
# duplicate check above should return 0 when re-run against Silver.
silver_mm = save(
    bronze_mm.groupBy("member_id", "lob", "month", "state", "plan", "rate_cell")
             .agg(F.max("premium").alias("premium")),
    "silver_member_months")

# Pooling points by line. Claims are capped so one catastrophic claim does not drive the PMPM used
# for trend and completion; paid_excess is kept so a pooling charge can be added back in the NPV.
# Points scale with each line's severity (Med Supp is secondary to Medicare; D&V is low severity).
_pool_cfg = json.loads((REPO / "config" / "experience_parameters.json").read_text())["pooling_points"]
pool = spark.createDataFrame([(k, float(v)) for k, v in _pool_cfg.items()], ["lob", "pool_point"])   # shared config
# Inner join to eligibility drops orphans; inner join to `pool` would also drop any unexpected lob
# code, which the reconciliation below would then catch.
silver_claims = save(
    bronze_claims.join(silver_mm.select("member_id", F.col("month").alias("incurred_month")).distinct(),
                       ["member_id", "incurred_month"], "inner")
                 .join(pool, "lob")
                 .withColumn("paid_capped", F.least("paid_amount", "pool_point"))
                 .withColumn("paid_excess", F.greatest(F.col("paid_amount") - F.col("pool_point"), F.lit(0.0))),
    "silver_claims")

# Reconciliation: Bronze paid = Silver paid + removed orphans (prove nothing else disappeared).
# Uses uncapped paid_amount on both sides: pooling reallocates dollars between paid_capped and
# paid_excess, it does not remove them. Tolerance of $1 absorbs floating-point summation order.
orph_paid = bronze_claims.join(elig_keys, ["member_id", "incurred_month"], "left_anti").agg(F.sum("paid_amount")).first()[0]
silver_paid = silver_claims.agg(F.sum("paid_amount")).first()[0]
if abs(control["bronze_paid_total"] - orph_paid - silver_paid) >= 1.0:   # raise, not assert: asserts vanish under python -O
    raise RuntimeError("Paid does not reconcile Bronze -> Silver")
print(f"Reconciled: bronze {control['bronze_paid_total']:,.0f} = silver {silver_paid:,.0f} + orphans {orph_paid:,.0f}")

# COMMAND ----------

# MAGIC %md ## 4. Chain-ladder completion factors (IBNR) with Spark window functions
# MAGIC Triangle of cumulative paid by incurred month × lag, volume-weighted age-to-age factors over
# MAGIC the latest 18 incurred months, cumulative development to ultimate, then completion of each
# MAGIC incurred month's latest diagonal. Built on capped (pooled) paid.

# COMMAND ----------

# 'YYYY-MM' -> year*12 + month, so lags and durations are integer subtraction
month_index = lambda c: F.substring(c, 1, 4).cast("int") * 12 + F.substring(c, 6, 2).cast("int")
paid_through_idx = int(PAID_THROUGH[:4]) * 12 + int(PAID_THROUGH[5:])

# Incremental paid by (lob, incurred month, lag). Lags of 8+ months are bucketed into lag 8, which
# is treated as fully complete (tail factor 1.0; see fillna below).
lagged = (silver_claims
          .withColumn("lag", F.least(month_index("paid_month") - month_index("incurred_month"), F.lit(8)))
          .groupBy("lob", "incurred_month", "lag").agg(F.sum("paid_capped").alias("paid")))
# Full grid of observable cells: every incurred month × lag 0..8, up to the paid-through diagonal.
# Without it, a lag with no payments would be missing rather than zero and the cumulation would skip it.
grid = (silver_claims.select("lob", "incurred_month").distinct()
        .withColumn("observable_lag", F.lit(paid_through_idx) - month_index("incurred_month"))
        .crossJoin(spark.range(0, 9).withColumnRenamed("id", "lag"))
        .filter(F.col("lag") <= F.col("observable_lag")))
# Running sum across lags within each incurred month = cumulative paid
w_cum = Window.partitionBy("lob", "incurred_month").orderBy("lag").rowsBetween(Window.unboundedPreceding, 0)
tri = (grid.join(lagged, ["lob", "incurred_month", "lag"], "left").fillna(0.0, ["paid"])
           .withColumn("cum_paid", F.sum("paid").over(w_cum)))

# Age-to-age factor for lag k -> k+1: self-join the triangle shifted by one lag (inner join keeps
# only incurred months observed at both lags), keep the 18 most recent such months per lag, and
# take sum(cum at k+1) / sum(cum at k), i.e. volume-weighted rather than a simple average of ratios.
nxt = tri.select("lob", "incurred_month", (F.col("lag") - 1).alias("lag"), F.col("cum_paid").alias("cum_next"))
w_recent = Window.partitionBy("lob", "lag").orderBy(F.desc("incurred_month"))
ata = (tri.join(nxt, ["lob", "incurred_month", "lag"])
          .withColumn("rn", F.row_number().over(w_recent)).filter("rn <= 18")
          .groupBy("lob", "lag").agg((F.sum("cum_next") / F.sum("cum_paid")).alias("ata")))
# Development to ultimate at lag k = product of ATA factors from k to the tail. Spark has no product
# aggregate, so it is exp(running sum of log ATA) with lags ordered from the tail backwards.
# completion_factor = share of ultimate paid by lag k (1 / development-to-ultimate).
w_cdf = Window.partitionBy("lob").orderBy(F.desc("lag")).rowsBetween(Window.unboundedPreceding, 0)
cf = (ata.withColumn("dev_to_ult", F.exp(F.sum(F.log("ata")).over(w_cdf)))
         .withColumn("completion_factor", 1 / F.col("dev_to_ult")))
cf.orderBy("lob", "lag").show(30, truncate=False)

# Complete each incurred month from its latest observed diagonal. Lag 8 has no factor (it is the
# tail), so fillna(1.0) treats it as complete.
completed = (tri.filter(F.col("lag") == F.least(F.col("observable_lag"), F.lit(8)))
                .join(cf.select("lob", "lag", "completion_factor"), ["lob", "lag"], "left")
                .fillna(1.0, ["completion_factor"])
                .withColumn("completed_claims", F.col("cum_paid") / F.col("completion_factor"))
                .select("lob", F.col("incurred_month").alias("month"), "completed_claims"))

# Gold monthly experience: exposure and premium from Silver eligibility, completed (pooled) claims.
# Left join from eligibility so a month with members but no claims shows zero claims, not no row.
# The most recent months carry the largest IBNR share and the most estimation uncertainty;
# trend fits downstream should weigh or exclude them accordingly.
gold_monthly = save(
    silver_mm.groupBy("lob", "month").agg(F.count("*").alias("member_months"), F.sum("premium").alias("premium"))
             .join(completed, ["lob", "month"], "left").fillna(0.0, ["completed_claims"])
             .withColumn("year", F.substring("month", 1, 4).cast("int")),
    "gold_monthly_experience")

# Annual PMPM and loss-ratio summary (ratios of sums, so months are exposure-weighted)
(gold_monthly.groupBy("lob", "year")
   .agg((F.sum("premium") / F.sum("member_months")).alias("premium_pmpm"),
        (F.sum("completed_claims") / F.sum("member_months")).alias("claims_pmpm"),
        (F.sum("completed_claims") / F.sum("premium")).alias("loss_ratio"))
   .orderBy("lob", "year").show(20, truncate=False))

# COMMAND ----------

# MAGIC %md ## 5. Lapse study (exposure by duration; annualized rates)
# MAGIC Exposure is member-months by policy duration; a lapse is counted in the member's last covered
# MAGIC month. Monthly rate = lapses / exposure, annualized as 1 − (1 − monthly)^12.

# COMMAND ----------

# Duration is months since issue, banded to policy years 1, 2 and 3+ (the select / ultimate split
# used in the NPV lapse assumption). Deaths and other term reasons are not counted as lapses; they
# remain in exposure up to termination. The GLM version with plan, age band and the January
# rate-increase effect is in python/03_lapse_glm_study.py.
lapse = save(
    silver_mm.join(bronze_members.select("member_id", "issue_month", "term_month", "term_reason"), "member_id")
      .withColumn("dur", month_index("month") - month_index("issue_month"))
      .withColumn("duration_year", F.when(F.col("dur") < 12, "1").when(F.col("dur") < 24, "2").otherwise("3+"))
      .withColumn("lapsed", ((F.col("term_month") == F.col("month"))
                             & F.col("term_reason").isin("LAPSE", "DISENROLL")).cast("int"))
      .groupBy("lob", "duration_year")
      .agg(F.count("*").alias("exposure_months"), F.sum("lapsed").alias("lapses"))
      .withColumn("annual_lapse", 1 - F.pow(1 - F.col("lapses") / F.col("exposure_months"), 12)),
    "gold_lapse_study")
lapse.orderBy("lob", "duration_year").show(truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Distributed sensitivity grid — Med Supp NPV across lapse × trend scenarios
# MAGIC `applyInPandas` sends each scenario block to a worker. On a real cluster this is how you run
# MAGIC thousands of scenarios (or seriatim policies) in parallel instead of looping on one machine.
# MAGIC
# MAGIC Lapse and trend interact: higher lapse shortens the projection, which removes future margin but
# MAGIC also removes exposure to compounding claim trend. The grid shows the joint effect that two
# MAGIC one-at-a-time sensitivities would miss.

# COMMAND ----------

# Base assumptions are read from npv_assumptions.json (the single source of truth), so the grid's
# centre cell reproduces 04_npv_engine.py exactly. The monthly mechanics below are an independent
# re-implementation of the engine (version 3): premium monthly in advance, lapse effective at the
# end of the paid month, deaths mid-month, dynamic shock lapse on the anniversary taken only by the
# healthier "shopper" class (anti-selective lapse), acquisition paid at issue.
# The base cell (lapse x1.0, trend +0) is the control: it must tie to the engine to the cent or
# the notebook raises, because if it does not, every other cell is suspect.
import assumption_control as _ac  # noqa: E402  (same resolver as the gate and the engine; REPO/code/python is on sys.path)
_NPVA = _ac.load_assumptions()
_ms, _g = _NPVA["MEDSUPP"], _NPVA["global"]
_feat = _g.get("features", {})
_as = _ms["antiselection"]; _dy = _ms["dynamic_shock_lapse"]
BASE = dict(years=_ms["projection_years"], cohort=_ms["cohort_size"], issue_age=_ms["issue_age"],
            prem1=_ms["annual_premium_year1"], prem_age=_ms["premium_age_step"], rate_inc=_ms["annual_rate_increase"],
            clm1=_ms["annual_claim_cost_year1_ultimate"], clm_age=_ms["claim_age_slope"], trend=_ms["claim_trend"],
            sel=_ms["selection_factors"], lapse=_ms["lapse_by_duration"], shock=_ms["shock_lapse_add_from_year2"],
            q65=_ms["mortality_q65"], mort_g=_ms["mortality_growth"], fy_comm=_ms["first_year_commission"],
            ren_comm=_ms["renewal_commission"], ptax=_ms["premium_tax"], acq=_ms["acquisition_cost_per_policy"],
            maint=_ms["maintenance_cost_per_policy"], infl=_g["expense_inflation"], disc=_g["discount_rate"],
            antisel=bool(_feat.get("antiselective_shock_lapse")), share=_as["shopper_share_at_issue"], rel_s=_as["shopper_relative_claim_cost"],
            kappa=_as["shopper_shock_lapse_propensity"],
            dynamic=bool(_feat.get("dynamic_shock_lapse")), shock_ref=_dy["reference_rate_increase"], shock_slope=_dy["shock_lapse_per_unit_rate_increase"])


def medsupp_npv(lapse_mult: float, trend_add: float, a=BASE) -> float:
    """Monthly Med Supp cohort NPV, same mechanics as 04_npv_engine.py (version 3).

    lapse_mult scales the base lapse rates (not the anniversary shock); trend_add is added to the
    annual claim trend. Two health classes: both take the base lapse; the block's shock lapse is
    split so a shopper is kappa times as likely as a non-shopper to take it. Pure Python with no
    Spark calls, so it can be shipped to workers inside applyInPandas.
    """
    a2m = lambda q: 1 - (1 - q) ** (1 / 12)                     # annual -> monthly decrement
    share = a["share"] if a["antisel"] else 1.0
    rs = a["rel_s"] if a["antisel"] else 1.0
    rn = (1 - share * rs) / (1 - share) if share < 1 else 1.0
    kappa = a["kappa"] if a["antisel"] else 1.0
    shock = max(0.0, a["shock"] + a["shock_slope"] * (a["rate_inc"] - a["shock_ref"])) if a["dynamic"] else a["shock"]
    nS, nN = a["cohort"] * share, a["cohort"] * (1 - share)
    pv = -a["cohort"] * a["acq"]                                 # acquisition paid at issue
    for k in range(12 * a["years"]):
        t, m = k // 12 + 1, k % 12 + 1                           # policy year, month within year
        d = min(t, 3) - 1                                        # duration index: years 1, 2, 3+
        qd = a2m(min(a["q65"] * np.exp(a["mort_g"] * (a["issue_age"] + t - 1 - 65)), 0.35))
        base = min(a["lapse"][d] * lapse_mult, 1.0)
        q_year = min(base + (shock if t >= 2 else 0.0), 1.0)
        base_m = a2m(base)
        # Anniversary month carries the shock, sized so month 1 plus 11 base months give q_year.
        ql = 1 - (1 - q_year) / (1 - base_m) ** 11 if m == 1 else base_m
        prem_pp = a["prem1"] * (1 + a["prem_age"]) ** (t - 1) * (1 + a["rate_inc"]) ** (t - 1)
        clm_pp = a["clm1"] * (1 + a["clm_age"]) ** (t - 1) * (1 + a["trend"] + trend_add) ** (t - 1) * a["sel"][d]
        comm = a["fy_comm"] if t == 1 else a["ren_comm"]
        maint = a["maint"] * (1 + a["infl"]) ** (t - 1)
        dS, dN = nS * qd, nN * qd
        sS, sN = nS - dS, nN - dN
        x = (sS + sN) * (ql - base_m) / (kappa * sS + sN)         # non-shopper extra (shock) rate
        lN = sN * (base_m + x)
        lS = sS * min(base_m + kappa * x, 1.0)                     # shoppers: kappa times as likely
        expoS, expoN = nS - 0.5 * dS, nN - 0.5 * dN               # lapsers covered to month end
        prem = (nS + nN) * prem_pp / 12                           # collected in advance
        pv += prem * (1 - comm - a["ptax"]) * (1 + a["disc"]) ** (-k / 12)
        pv -= ((expoS * rs + expoN * rn) * clm_pp / 12 + (expoS + expoN) * maint / 12) * (1 + a["disc"]) ** (-(k + 0.5) / 12)
        nS, nN = sS - lS, sN - lN
    return pv


# Control: the independent implementation must reproduce the engine's base Med Supp NPV.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("npv_engine", REPO / "code" / "python" / "04_npv_engine.py")
_eng = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_eng)
_base_nb, _base_eng = medsupp_npv(1.0, 0.0), _eng.npv("MEDSUPP", _eng.A["MEDSUPP"], _eng.A["global"])
if abs(_base_nb - _base_eng) > 0.01:
    raise RuntimeError(f"Notebook Med Supp NPV {_base_nb:,.2f} does not tie to the engine {_base_eng:,.2f}")
print(f"Base Med Supp NPV per 1,000 policies: {_base_nb:,.2f}  (ties to 04_npv_engine.py: difference {_base_nb - _base_eng:+.6f})")


# Scenario table: lapse multiplier 0.6-1.4 by 0.1 crossed with trend shift -2% to +2% by 0.5%.
# `block` spreads scenarios across 8 groups; each group becomes one pandas task on a worker.
# Rounding removes float drift from np.arange so scenario values are clean decimals.
scen = spark.createDataFrame(
    [(i, float(lm), float(ta)) for i, (lm, ta) in enumerate(
        [(lm, ta) for lm in np.round(np.arange(0.6, 1.41, 0.1), 2) for ta in np.round(np.arange(-0.02, 0.0201, 0.005), 3)])],
    ["scenario_id", "lapse_mult", "trend_add"]).withColumn("block", F.col("scenario_id") % 8)


def run_block(pdf: pd.DataFrame) -> pd.DataFrame:
    """Evaluate every scenario in one block (runs on a worker; input and output are pandas)."""
    pdf = pdf.copy()
    pdf["npv"] = [medsupp_npv(l, t) for l, t in zip(pdf["lapse_mult"], pdf["trend_add"])]
    return pdf[["scenario_id", "lapse_mult", "trend_add", "npv"]]


# The output schema must be declared up front: Spark plans the result before any worker runs.
# The same pattern distributes seriatim policies (group by policy block) or stochastic scenarios.
grid_out = save(scen.groupBy("block").applyInPandas(
    run_block, schema="scenario_id long, lapse_mult double, trend_add double, npv double"), "gold_npv_grid")

# Pivot is done in pandas: the grid is small and already aggregated
heat = grid_out.toPandas().pivot(index="lapse_mult", columns="trend_add", values="npv").round(-3)
print("Med Supp NPV per 1,000 policies — rows: lapse multiplier, columns: trend shift")
print(heat.to_string())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Run log (governance)
# MAGIC Record the inputs, table versions and outputs of every run. On Databricks, `DESCRIBE HISTORY`
# MAGIC gives the Delta version of each gold table; store it with the NPV results.
# MAGIC A later rerun or audit can then read exactly the same inputs with
# MAGIC `SELECT ... FROM <table> VERSION AS OF <n>`, subject to the table's retention settings.

# COMMAND ----------

# The run log ties results to inputs: evaluation date, Bronze control totals, DQ counts, the base NPV
# and (on Delta) the latest version of each Gold table. Printed here; a production job appends it to
# an audit table so runs can be compared over time.
run_log = {"paid_through": PAID_THROUGH, "control_totals": control, "dq_checks": checks,
           "dq_gate": {f"{g['check']}|{g['lob']}": g["status"] for g in gate},
           "medsupp_base_npv": medsupp_npv(1.0, 0.0)}
if FORMAT == "delta":
    run_log["gold_versions"] = {t: spark.sql(f"DESCRIBE HISTORY {SCHEMA}.{t} LIMIT 1").first()["version"]
                                for t in ["gold_monthly_experience", "gold_lapse_study", "gold_npv_grid"]}
print(json.dumps(run_log, indent=2, default=float))

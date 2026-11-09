"""
01_run_sql_kit.py
-------------------------------------------------------------------------------
Runs every SQL program in ../sql/ in order against the synthetic data using
DuckDB (a free, in-process SQL engine - no server needed), and saves each
program's final result set to ../../output/sql_results/.

Why DuckDB here: it allows practice with the same SQL that would run in
Databricks SQL, on a laptop. The SQL is written in ANSI style so the same
files run in Databricks with the tables pointed at Delta tables instead of
CSVs.

Pipeline layers (medallion convention):
    bronze  raw extracts loaded as-is (this script)
    silver  de-duplicated, reconciled, large claims pooled (SQL 02)
    gold    completed monthly experience and assumption inputs (SQL 03, 05)
Keeping raw data untouched in bronze means every cleaning step is a visible,
reviewable SQL statement rather than an undocumented edit to the source -
the audit trail ASOP 23 expects when data are adjusted.

Execution order is the file-name prefix (01_, 02_, ...); later programs read
tables built by earlier ones, so the files must not be run out of order.

Usage:  python 01_run_sql_kit.py
-------------------------------------------------------------------------------
"""
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "code" / "sql"
DATA = DATA_DIR
OUT = OUTPUT_DIR / "sql_results"
OUT.mkdir(parents=True, exist_ok=True)

# A persistent database file (not in-memory) so downstream Python steps (the
# 02 trend forecast, the controls gate) read exactly the gold tables the SQL built.
# It is built on local disk and copied to OUTPUT_DIR at the end: DuckDB needs random
# writes, which network / FUSE storage such as a Unity Catalog Volume may not support;
# readers open the copy read-only.
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_tmpdir = tempfile.mkdtemp(prefix="npv_duckdb_")
DB_LOCAL = Path(_tmpdir) / "practice.duckdb"
con = duckdb.connect(str(DB_LOCAL))
# Single-threaded execution makes floating-point aggregation order fixed, so every rebuild of the
# gold tables is bit-identical. With parallel threads, sums differ in the 16th digit between runs,
# which the SARIMA optimizer in step 02 amplifies into visible differences: a production run must
# reproduce exactly. The data volume here makes the speed cost negligible; on Databricks, Spark
# results are made reproducible by the recorded Delta table version instead.
con.execute("SET threads TO 1")

# ----------------------------------------------------------------------------
# 1. Load bronze tables
# Load raw ("bronze") tables. Month columns stay as 'YYYY-MM' text.
# Forcing these columns to text rather than letting the CSV reader infer
# types matters for three reasons:
#   * type inference can turn '2025-07' into a DATE (day 1) in one table and
#     leave it as text in another, and the incurred-month-to-eligibility-month
#     join then silently fails or mis-matches;
#   * 'YYYY-MM' text sorts chronologically and is the key format used in every
#     SQL program and in the lag arithmetic (SUBSTR on year and month);
#   * rate_cell is null for every non-Medicaid row, so inference from the
#     leading rows can pick the wrong type for the whole column.
# ----------------------------------------------------------------------------
text_cols = {"month": "VARCHAR", "issue_month": "VARCHAR", "term_month": "VARCHAR",
             "incurred_month": "VARCHAR", "paid_month": "VARCHAR", "rate_cell": "VARCHAR"}
for table in ["members", "member_months", "claims"]:
    path = DATA / f"{table}.csv"
    header = pd.read_csv(path, nrows=0).columns
    # Only pass overrides for columns this table actually has.
    types = {c: t for c, t in text_cols.items() if c in header}
    # Table and path names are interpolated from fixed local values. In
    # Databricks the equivalent is reading governed Delta tables by name, not
    # building SQL from user-supplied strings.
    con.execute(f"""
        CREATE OR REPLACE TABLE {table} AS
        SELECT * FROM read_csv_auto('{path.as_posix()}', header=true, types={types!r})
    """)
    # Row counts printed at load are the first control total: they should tie
    # to the source extract before any cleaning is applied.
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"loaded {table:<14} {n:>10,} rows")

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

# ----------------------------------------------------------------------------
# 2. Run each SQL program in order and keep its final result set
# Each program's last SELECT is its reviewable output (data-quality exceptions,
# experience PMPM, completion factors, lapse study, A/E). Saving it as CSV
# gives a reviewer the evidence without rerunning the pipeline.
# ----------------------------------------------------------------------------
# SQL parameters come from configuration, never from literals in the SQL: the paid-through
# month (run_config.json, the same value the data contract enforces) and the shared experience
# parameters. A placeholder left unfilled stops the run.
RUN = json.loads(Path(os.environ.get("RUN_CONFIG", ROOT / "config" / "run_config.json")).read_text())
EXP = json.loads((ROOT / "config" / "experience_parameters.json").read_text())
DQP = json.loads((ROOT / "config" / "dq_policy.json").read_text())
_ae = EXP["ae_expected_basis"]
PARAMS = {
    "PAID_THROUGH": RUN["paid_through"],
    "POOLING_VALUES": ", ".join(f"('{l}', {float(v)})" for l, v in EXP["pooling_points"].items()),
    "LARGE_CLAIM_WHEN": " ".join(f"WHEN '{l}' THEN {float(v)}" for l, v in DQP["checks"]["large_claims"]["threshold_by_lob"].items()),
    "AE_BASIS_VALUES": ", ".join(f"('{l}', {float(x['claims_pmpm_base_year'])}, {float(x['annual_trend'])}, {float(x['monthly_termination'])})"
                                 for l, x in _ae["lines"].items()),
    "AE_BASE_YEAR": str(int(_ae["base_year"])),
}


def render(sql: str) -> str:
    for k, v in PARAMS.items():
        sql = sql.replace("{{" + k + "}}", v)
    if "{{" in sql:
        raise ValueError("unfilled SQL parameter: " + sql[sql.index("{{"):sql.index("{{") + 40])
    return sql


for sql_file in sorted(SQL_DIR.glob("*.sql")):
    script = render(sql_file.read_text())
    print(f"\n=== {sql_file.name} ===")
    result = con.execute(script).fetchdf()   # DuckDB runs all statements, returns the last result
    result.to_csv(OUT / f"{sql_file.stem}.csv", index=False)
    print(result.head(12).to_string(na_rep="n/a", index=False))
    if len(result) > 12:
        print(f"... {len(result)} rows total -> output/sql_results/{sql_file.stem}.csv")

con.close()
# Copy the finished database to the output location in one sequential write, then clean up.
shutil.copyfile(DB_LOCAL, OUTPUT_DIR / "practice.duckdb")
shutil.rmtree(_tmpdir, ignore_errors=True)

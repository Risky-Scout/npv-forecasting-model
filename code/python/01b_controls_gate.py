"""
01b_controls_gate.py
-------------------------------------------------------------------------------
Phase 1 control gate: runs after the SQL kit (01) and before any assumption
or NPV work. It stops the pipeline (exit code 1) unless all three pass:

  1. Data contract  - each raw feed matches config/data_contract.json
                      (columns, types, nullability, allowed values, unique
                      keys, freshness against the paid-through date; volume
                      drops are surfaced as warnings).
  2. Data gate      - every BLOCKER from SQL 01 is either repaired by its
                      approved cleaning rule within tolerance
                      (config/dq_policy.json) or covered by an unexpired,
                      specifically matching exception
                      (config/dq_exceptions.json).
  3. Ledger tie-out - paid claims and premium by line and month agree with
                      the finance control totals (data/finance_controls.csv)
                      within the per-cell tolerance, or differ only by
                      registered reconciling items (config/recon_items.json).

The rules themselves live in controls.py, shared with the Databricks notebook
and the unit tests. Reports are written to output/controls/.

Why a separate gate: SQL 01 measures problems; this script decides, from
versioned policy, whether the run may continue. Thresholds and exceptions are
reviewed configuration, not code (ASOP 23 data quality; ASOP 56 governance).
-------------------------------------------------------------------------------
"""
import json
import sys
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import controls  # noqa: E402

# CONTROLS_CONFIG_DIR lets tests run the gate against altered policy without touching config/.
import os  # noqa: E402
CFG = Path(os.environ.get("CONTROLS_CONFIG_DIR", ROOT / "config"))
OUT = OUTPUT_DIR / "controls"
OUT.mkdir(parents=True, exist_ok=True)
load = lambda name: json.loads((CFG / name).read_text())
import assumption_control as _ac  # noqa: E402
# The run configuration comes from the shared reader (honours RUN_CONFIG) unless a test points the
# gate at an altered copy of the whole config folder.
run = load("run_config.json") if "CONTROLS_CONFIG_DIR" in os.environ else _ac.run_config()
contract, policy = load("data_contract.json"), load("dq_policy.json")
exceptions, recon = load("dq_exceptions.json")["exceptions"], load("recon_items.json")

con = duckdb.connect()
MONTH_RE = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
CAST = {"BIGINT": "BIGINT", "DOUBLE": "DOUBLE"}

# ----------------------------------------------------------------------------
# 1. Data contract: profile each raw file read as text, so a bad value is
#    counted rather than silently coerced by type inference.
# ----------------------------------------------------------------------------
contract_rows = []
for src, spec in contract["sources"].items():
    path = data_file(spec["file"]).as_posix()
    con.execute(f"CREATE OR REPLACE TEMP VIEW raw AS SELECT * FROM read_csv_auto('{path}', header=true, all_varchar=true)")
    cols = [r[0] for r in con.execute("DESCRIBE raw").fetchall()]
    obs = {"columns": cols, "type_failures": {}, "nulls": {}, "disallowed": {}, "max_value": {}}
    for col, c in spec["columns"].items():
        if col not in cols:
            continue
        present = f"NULLIF(TRIM({col}), '') IS NOT NULL"
        if c["type"] in CAST:
            bad = f"{present} AND TRY_CAST({col} AS {CAST[c['type']]}) IS NULL"
        elif c["type"] == "MONTH":
            bad = f"{present} AND NOT regexp_matches({col}, '{MONTH_RE}')"
        else:
            bad = "FALSE"
        obs["type_failures"][col] = con.execute(f"SELECT COUNT(*) FROM raw WHERE {bad}").fetchone()[0]
        obs["nulls"][col] = con.execute(f"SELECT COUNT(*) FROM raw WHERE NOT ({present})").fetchone()[0]
        if "allowed" in c:
            allowed = ", ".join(f"'{a}'" for a in c["allowed"])
            obs["disallowed"][col] = con.execute(f"SELECT COUNT(*) FROM raw WHERE {present} AND {col} NOT IN ({allowed})").fetchone()[0]
    if "primary_key" in spec:
        k = ", ".join(spec["primary_key"])
        obs["duplicate_keys"] = con.execute(f"SELECT COUNT(*) - COUNT(DISTINCT ({k})) FROM raw").fetchone()[0]
    if "freshness" in spec:
        col = spec["freshness"]["column"]
        obs["max_value"][col] = con.execute(f"SELECT MAX({col}) FROM raw").fetchone()[0]
    if "volume" in spec:
        g = spec["volume"]["group_by"]
        # Months after the experience window are claim run-out only, so their volume falls by design.
        upto = run[spec["volume"].get("through", "paid_through")]
        counts = con.execute(f"SELECT {g[0]}, {g[1]}, COUNT(*) AS n FROM raw WHERE {g[1]} <= '{upto}' "
                             f"GROUP BY 1, 2 ORDER BY 1, 2").fetchdf()
        counts["prior"] = counts.groupby(g[0])["n"].shift(1)
        counts["drop"] = 1 - counts["n"] / counts["prior"]
        lim = spec["volume"]["max_drop_vs_prior"]
        obs["volume_breaks"] = [(f"{r[g[0]]} {r[g[1]]}", int(r.prior), int(r.n), round(float(r["drop"]), 3))
                                for _, r in counts[counts["drop"] > lim].iterrows()]
    contract_rows += controls.evaluate_contract(src, spec, obs, run)

# ----------------------------------------------------------------------------
# 2. Data gate: counts from SQL 01, plus the measures the policy tests against.
# ----------------------------------------------------------------------------
db = duckdb.connect(str(OUTPUT_DIR / "practice.duckdb"), read_only=True)
dq = pd.read_csv(OUTPUT_DIR / "sql_results" / "01_data_quality_checks.csv")
orphan_share = dict(db.execute("""
    SELECT c.lob, SUM(CASE WHEN e.member_id IS NULL THEN c.paid_amount ELSE 0 END) / SUM(c.paid_amount)
    FROM claims c LEFT JOIN (SELECT DISTINCT member_id, month FROM member_months) e
      ON e.member_id = c.member_id AND e.month = c.incurred_month
    GROUP BY c.lob""").fetchall())
dup_share = dict(db.execute("""
    SELECT lob, (COUNT(*) - COUNT(DISTINCT (member_id, month))) / COUNT(*) FROM member_months GROUP BY lob""").fetchall())
missing_keys = db.execute("""
    WITH cal AS (SELECT DISTINCT month FROM member_months),
         first_seen AS (SELECT lob, state, MIN(month) AS first_month FROM member_months GROUP BY lob, state),
         expected AS (SELECT f.lob, f.state, c.month FROM first_seen f CROSS JOIN cal c WHERE c.month >= f.first_month),
         actual AS (SELECT DISTINCT lob, state, month FROM member_months)
    SELECT e.lob, e.state || '|' || e.month FROM expected e
    LEFT JOIN actual a ON a.lob = e.lob AND a.state = e.state AND a.month = e.month
    WHERE a.lob IS NULL ORDER BY 1, 2""").fetchall()
db.close()

findings = []
for _, r in dq.iterrows():
    check = policy["sql01_check_names"].get(r.check_name, r.check_name)
    share = {"claims_without_eligibility": orphan_share, "duplicate_member_months": dup_share}.get(check, {}).get(r.lob)
    keys = [k for lob, k in missing_keys if lob == r.lob] if check == "missing_eligibility_month" else []
    findings.append({"check": check, "lob": r.lob, "count": int(r.n_flagged), "share": share, "keys": keys})
# Exception expiry is judged on the actual date in production (an operator-set run_date must not
# keep an expired exception alive); synthetic runs use the fixed run_date so results reproduce.
from datetime import date  # noqa: E402
as_of = date.today().isoformat() if run.get("mode") == "production" else run["run_date"]
if run.get("mode") == "production" and as_of != run["run_date"]:
    print(f"Note: exceptions checked as of today ({as_of}), not run_date {run['run_date']}")
gate_rows = controls.evaluate_gate(findings, policy, exceptions, as_of)

# ----------------------------------------------------------------------------
# 3. Ledger tie-out: raw paid claims (orphans included: they were paid) and
#    de-duplicated premium against the finance control totals.
# ----------------------------------------------------------------------------
claims_path = (DATA_DIR / "claims.csv").as_posix()
mm_path = (DATA_DIR / "member_months.csv").as_posix()
feed = {}
for lob, m, amt in con.execute(f"""SELECT lob, paid_month, SUM(paid_amount) FROM read_csv_auto('{claims_path}',
        header=true, types={{'paid_month':'VARCHAR','incurred_month':'VARCHAR'}}) GROUP BY 1, 2""").fetchall():
    feed[("paid_claims", lob, m)] = amt
for lob, m, amt in con.execute(f"""SELECT lob, month, SUM(premium) FROM (SELECT DISTINCT * FROM read_csv_auto('{mm_path}',
        header=true, types={{'month':'VARCHAR','rate_cell':'VARCHAR'}})) GROUP BY 1, 2""").fetchall():
    feed[("premium", lob, m)] = amt
fc = pd.read_csv(DATA_DIR / "finance_controls.csv", dtype={"month": str})
ledger = {(r.control, r.lob, r.month): float(r.amount) for r in fc.itertuples()}
recon_rows = controls.evaluate_ledger(feed, ledger, recon["items"], recon["tolerance_per_cell"])

# ----------------------------------------------------------------------------
# Reports and verdict
# ----------------------------------------------------------------------------
pd.DataFrame(contract_rows).to_csv(OUT / "data_contract_report.csv", index=False)
pd.DataFrame(gate_rows).to_csv(OUT / "data_gate_report.csv", index=False)
pd.DataFrame(recon_rows).to_csv(OUT / "ledger_tieout_report.csv", index=False)

def summary(name, rows):
    s = pd.Series([r["status"] for r in rows]).value_counts().to_dict()
    print(f"{name:<16} {'PASS' if controls.overall(rows) else 'FAIL':<5} {s}")
    for r in rows:
        if r["status"] in ("FAIL", "PASS-EXCEPTION", "PASS-EXPLAINED", "PASS-CLEANED", "WARN"):
            print("   ", {k: v for k, v in r.items() if k not in ("control",)})

print(f"Run {run['run_date']}  paid through {run['paid_through']}")
summary("Data contract", contract_rows)
summary("Data gate", gate_rows)
summary("Ledger tie-out", recon_rows)
ok = all(controls.overall(x) for x in (contract_rows, gate_rows, recon_rows))
log = {"run": run, "passed": ok,
       "data_contract": controls.overall(contract_rows), "data_gate": controls.overall(gate_rows),
       "ledger_tieout": controls.overall(recon_rows),
       "exceptions_used": sorted({r["reason"].split()[-1] for r in gate_rows if r["status"] == "PASS-EXCEPTION"}),
       "reconciling_items_used": [i["id"] for i in recon["items"]]}
(OUT / "controls_log.json").write_text(json.dumps(log, indent=2))
print("\nRESULT:", "PASS - data may proceed to assumptions and NPV" if ok else "FAIL - pipeline stopped; see output/controls/")
sys.exit(0 if ok else 1)

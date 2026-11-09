"""Runs the whole kit end to end, in dependency order, and stops at the first failure.

Two modes (config/run_config.json -> "mode", or --mode on the command line):

  synthetic   builds the synthetic practice book and runs every step, including
              the model-validation steps that need a known data-generating process
              (archived v1 engine, backtest). This is the release / validation run.
  production  reads source extracts through config/source_mapping.json instead of
              generating data, refuses an unapproved assumption set, and skips the
              synthetic-only validation steps. This is the cycle run on Humana data.

Order matters: each step reads what the step before it wrote.
    [synthetic data] -> ingest -> SQL kit -> controls gate -> trend and lapse studies
    -> assumption governance -> NPV engine (+ profit test) -> in-force forecast, A/E waterfall,
    forecast vintages (v4) -> [v1 engine, backtest]
    -> Excel model -> recalculation -> Python-to-Excel reconciliation -> [non-neutral Excel tie-out]
    -> drift monitoring (v4)
    -> Databricks / Spark pipeline -> [tests] -> run manifest

Fail-fast: a non-zero exit code from any step ends the run, so later steps never
produce results from stale or partial inputs. Each step's status is recorded in
output/run_steps.json and carried into output/run_manifest.json (also on failure).

In Databricks this list maps one-to-one onto a Workflow (Job): see
deploy/databricks_job.json, where each step is a task depending on the one before.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent / 'code' / 'python'))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

ROOT = Path(__file__).resolve().parent
PY = ROOT / "code" / "python"
sys.path.insert(0, str(ROOT / "code" / "python"))
import assumption_control as _ac  # noqa: E402  (one reader of run_config.json for every step)
RUN = _ac.run_config()

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=["synthetic", "production"], default=RUN.get("mode", "synthetic"))
ap.add_argument("--skip-tests", action="store_true", help="skip the test suite (it always runs in synthetic mode by default)")
args = ap.parse_args()
if args.mode != RUN.get("mode"):
    sys.exit(f"--mode {args.mode} differs from config/run_config.json mode {RUN.get('mode')!r}; "
             "set it in the config so every step sees the same mode.")

SYN, PROD = {"synthetic"}, {"production"}
BOTH = SYN | PROD
# (modes, interpreter, script)
steps = [
    (BOTH, sys.executable, PY / "00s_start_run.py"),                  # clean start: clear previous artefacts, stamp run
    (SYN, sys.executable, PY / "00_generate_synthetic_data.py"),
    (SYN, sys.executable, PY / "00b_generate_finance_controls.py"),   # synthetic stand-in for ledger totals
    (BOTH, sys.executable, PY / "00a_ingest_source_extracts.py"),     # source mapping + lineage hashes
    (BOTH, sys.executable, PY / "01_run_sql_kit.py"),
    (BOTH, sys.executable, PY / "01b_controls_gate.py"),              # contract, data gate, ledger tie-out
    (BOTH, sys.executable, PY / "02_claims_trend_forecast.py"),
    (BOTH, sys.executable, PY / "03_lapse_glm_study.py"),
    (BOTH, sys.executable, PY / "03b_assumption_governance.py"),      # indications + approval gate
    (BOTH, sys.executable, PY / "04_npv_engine.py"),                  # cash NPV, profit test, tornado, MC, bridge
    (BOTH, sys.executable, PY / "09_inforce_forecast.py"),            # v4: in-force calendar-month forecast (BF completion)
    (BOTH, sys.executable, PY / "10_actual_vs_expected.py"),          # v4: A/E variance waterfall vs the prior vintage
    (BOTH, sys.executable, PY / "11_forecast_vintages.py"),           # v4: vintage registry, accuracy by horizon, bias test
    (SYN, sys.executable, PY / "archive" / "v1_annual_npv_engine.py"),
    (SYN, sys.executable, PY / "06_npv_backtest.py"),                  # needs the known generating process
    (BOTH, sys.executable, ROOT / "build_workbook.py"),
    (BOTH, sys.executable, ROOT / "recalc_workbook.py"),
    (BOTH, sys.executable, PY / "05_reconcile_python_to_excel.py"),
    (SYN, sys.executable, ROOT / "tests" / "e2e_excel_nonneutral.py"),  # v4: Excel = Python at NON-neutral v4 terms
    (SYN, sys.executable, ROOT / "tests" / "e2e_excel_pricing.py"),     # v4.2: Excel = Python at the RECOMMENDED pricing
    (BOTH, sys.executable, PY / "12_drift_monitoring.py"),            # v4: credibility-scaled A/E bands, alert list
    (BOTH, sys.executable, ROOT / "code" / "databricks" / "npv_pipeline_databricks.py"),
    (SYN, sys.executable, ROOT / "tests" / "run_tests.py"),
    (SYN, sys.executable, ROOT / "tests" / "e2e_databricks_job_local.py"),  # v4.1: the Databricks job definition, run in its DAG order
    (SYN, sys.executable, ROOT / "export_results.py"),                # every quoted figure -> output/results.json
]
log = []
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def record():
    (OUTPUT_DIR / "run_steps.json").write_text(json.dumps({"mode": args.mode, "steps": log}, indent=2) + "\n")


def finish(code: int, msg: str):
    record()
    subprocess.run([sys.executable, str(PY / "08_run_manifest.py")], cwd=PY)
    if code:
        sys.exit(msg)
    print(msg)


print(f"Run mode: {args.mode}  (run id {RUN.get('run_id')})")
for modes, interp, script in steps:
    name = Path(script).name
    if args.mode not in modes or (args.skip_tests and name == "run_tests.py"):
        log.append(dict(step=name, status="SKIPPED", reason=f"not part of a {args.mode} run" if args.mode not in modes else "--skip-tests"))
        continue
    print(f"\n{'=' * 78}\n>>> {name}\n{'=' * 78}", flush=True)
    t = time.time()
    # Run from the script's own folder so its relative paths resolve as they do when run by hand.
    r = subprocess.run([str(interp), str(script)], cwd=Path(script).parent, env=os.environ.copy())
    log.append(dict(step=name, status="PASS" if r.returncode == 0 else "FAIL", exit_code=r.returncode, seconds=round(time.time() - t, 1)))
    if r.returncode != 0:
        finish(1, f"Step failed: {name}")
finish(0, "\nAll steps completed.")

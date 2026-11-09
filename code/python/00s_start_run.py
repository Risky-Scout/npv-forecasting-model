"""
00s_start_run.py
-------------------------------------------------------------------------------
First step of every run (run_all.py and the Databricks job): clears the kit's
previous artefacts from the output location and stamps the start of this run.

Why: outputs live in one working location (a Volume in production). Without a
clean start, a run that fails half-way would leave the previous run's NPVs and
approvals in place, and the run manifest could present them as this run's.
Archive the previous cycle's outputs before starting a new one (RUNBOOK §3).

Only the artefacts the kit itself writes are removed (an explicit list), so a
mis-set NPV_OUTPUT_DIR can never delete anything else. output/run_start.json
records the run id, mode and start time; 08_run_manifest.py includes only
artefacts written after it.
-------------------------------------------------------------------------------
"""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import OUTPUT_DIR  # noqa: E402
import assumption_control as _ac  # noqa: E402

ARTEFACT_DIRS = ["npv", "npv_v1", "npv_v2", "backtest", "trend", "lapse", "sql_results", "controls", "assumptions", "spark_warehouse",
                 "forecast", "monitoring"]          # v4
# NOT cleared: "vintage_registry" (v4). It is the persistent history of every production forecast
# vintage, which 11_forecast_vintages.py scores; clearing it would erase the accuracy record.
ARTEFACT_FILES = ["practice.duckdb", "Humana_NPV_Forecasting_Model.xlsx", "results.json", "run_manifest.json", "run_steps.json", "run_start.json"]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    removed = []
    for d in ARTEFACT_DIRS:
        if (OUTPUT_DIR / d).is_dir():
            shutil.rmtree(OUTPUT_DIR / d); removed.append(d + "/")
    for f in ARTEFACT_FILES:
        if (OUTPUT_DIR / f).is_file():
            (OUTPUT_DIR / f).unlink(); removed.append(f)
    run = _ac.run_config()
    start = {"run_id": run.get("run_id"), "mode": run.get("mode"),
             "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    (OUTPUT_DIR / "run_start.json").write_text(json.dumps(start, indent=2) + "\n")
    print(f"Run {start['run_id']} ({start['mode']}) started {start['started_at_utc']}; cleared {len(removed)} previous artefacts")
    return 0


if __name__ == "__main__":
    sys.exit(main())

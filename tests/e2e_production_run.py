"""
End-to-end test of PRODUCTION mode (run by hand or in CI; takes about two minutes).

  1. Copies the kit to a scratch folder and DELETES its canonical data files and the
     synthetic truth file (data/true_assumptions.json), so nothing synthetic can leak in.
  2. Writes the synthetic book as "source extracts" in the example production layout
     (renamed columns, coded lines of business, YYYYMM months) in a landing folder.
  3. Points config/run_config.json at mode = production and at a mapping whose
     source_root is the landing folder.
  4. Runs run_all.py with data and outputs RELOCATED outside the kit (NPV_DATA_DIR,
     NPV_OUTPUT_DIR), as on Databricks with a Unity Catalog Volume. Production mode ingests
     the extracts, enforces the controls and the assumption approval, and skips the
     synthetic-only steps. Nothing may be written inside the kit's own data/ or output/.
  5. Compares every DATA-DERIVED output (SQL experience tables, completion factors, lapse and
     trend studies, assumption indications, control reports) and the headline results with the
     synthetic run in this kit: they must be identical, because the mapping changes layout only,
     never values. (The NPVs alone would prove nothing about the data: the engine reads the
     approved assumption file, and the data reaches the model through the studies and the
     assumption indications.)

Two phases. Phase 1 runs with the Kentucky exception (DQ-0142) set back to its ORIGINAL expiry,
2026-06-30, in the scratch copy: production judges expiry on today's date, so the controls gate
must STOP the run, and the run manifest must show no results (nothing carried over). Phase 2 uses
the exception as shipped (renewed by the data owner on 2026-11-04 to 2027-06-30), and the run must
complete and reproduce the synthetic book.

Exit 0 = PASS. Usage: python tests/e2e_production_run.py   (after a synthetic run_all.py)
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import os

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code" / "python"))
from kitpaths import OUTPUT_DIR  # noqa: E402
spec = importlib.util.spec_from_file_location("t", ROOT / "tests" / "test_v3_production.py")
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)


def main() -> int:
    ref = pd.read_csv(OUTPUT_DIR / "npv" / "npv_summary.csv").set_index("lob")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        kit = tmp / "kit"
        shutil.copytree(ROOT, kit, ignore=shutil.ignore_patterns("output", ".pytest_cache", "__pycache__", "*.duckdb"))
        landing = tmp / "landing"
        mapping = json.loads((ROOT / "config" / "source_mapping.production_example.json").read_text())
        t.make_production_extracts(landing, mapping)
        for f in ["members.csv", "member_months.csv", "claims.csv", "finance_controls.csv", "true_assumptions.json"]:
            (kit / "data" / f).unlink()
        mapping["source_root"] = str(landing)
        (kit / "config" / "source_mapping.e2e.json").write_text(json.dumps(mapping, indent=2))
        cfg = json.loads((kit / "config" / "run_config.json").read_text())
        cfg.update(mode="production", run_id="E2E-PROD-TEST", source_mapping="config/source_mapping.e2e.json")
        (kit / "config" / "run_config.json").write_text(json.dumps(cfg, indent=2))
        vol = tmp / "volume"                       # stands in for /Volumes/<catalog>/<schema>/<volume>
        env = dict(os.environ, NPV_DATA_DIR=str(vol / "data"), NPV_OUTPUT_DIR=str(vol / "output"))
        out = vol / "output"
        # ---- phase 1: an EXPIRED exception must stop production, with no stale results. The shipped
        # register holds a renewed exception, so the test expires it in its own copy of the kit.
        exc = json.loads((kit / "config" / "dq_exceptions.json").read_text())
        for e in exc["exceptions"]:
            e["expires"] = "2026-06-30"
        (kit / "config" / "dq_exceptions.json").write_text(json.dumps(exc, indent=2))
        r = subprocess.run([sys.executable, str(kit / "run_all.py")], cwd=kit, capture_output=True, text=True, env=env)
        m1 = json.loads((out / "run_manifest.json").read_text())
        failed = [s["step"] for s in m1["steps"]["steps"] if s["status"] == "FAIL"]
        phase1 = r.returncode != 0 and failed == ["01b_controls_gate.py"] and m1["results"] is None and m1["assumptions"] is None
        print(f"Phase 1 (expired exception): run stopped at {failed}, manifest results = {m1['results']} -> {'PASS' if phase1 else 'FAIL'}")
        # ---- phase 2: exception renewed (as shipped); the run must complete
        exc = json.loads((kit / "config" / "dq_exceptions.json").read_text())
        for e in exc["exceptions"]:
            e["expires"] = "2099-12-31"; e["reason"] += " [renewed for the end-to-end test]"
        (kit / "config" / "dq_exceptions.json").write_text(json.dumps(exc, indent=2))
        r = subprocess.run([sys.executable, str(kit / "run_all.py")], cwd=kit, capture_output=True, text=True, env=env)
        tail = (r.stdout + r.stderr)[-3000:]
        if r.returncode != 0:
            print(tail); print("E2E FAIL: production run did not complete"); return 1
        got = pd.read_csv(out / "npv" / "npv_summary.csv").set_index("lob")
        steps = json.loads((out / "run_steps.json").read_text())["steps"]
        manifest = json.loads((out / "run_manifest.json").read_text())
        ok = phase1
        for lob in ref.index:
            for col in ["npv", "de_npv", "pv_revenue", "pv_claims"]:
                d = abs(float(got.loc[lob, col]) - float(ref.loc[lob, col]))
                ok &= d < 0.01
                print(f"{lob:14s} {col:10s} production {float(got.loc[lob, col]):>16,.2f}  synthetic {float(ref.loc[lob, col]):>16,.2f}  diff {d:.6f}")
        # Data-derived outputs: every value must match the synthetic run.
        data_outputs = ["sql_results/01_data_quality_checks.csv", "sql_results/02_clean_and_experience_pmpm.csv",
                        "sql_results/03_completion_factors.csv", "sql_results/04_lapse_persistency_study.csv",
                        "sql_results/05_npv_assumption_inputs_gold.csv", "sql_results/06_actual_vs_expected_monitoring.csv",
                        "trend/fitted_trends.csv", "trend/holdout_scores.csv", "lapse/lapse_assumption_table.csv",
                        "assumptions/assumption_indications.csv", "controls/data_gate_report.csv", "controls/ledger_tieout_report.csv",
                        # v4: trend diagnostics, in-force forecast and A/E waterfall are data-derived too. (Forecast
                        # accuracy and drift alerts are not compared: production has one vintage on its first cycle,
                        # synthetic mode reconstructs five.)
                        "trend/rolling_origin_scores.csv", "trend/forecast_bias_diagnostics.csv",
                        "forecast/inforce_forecast_summary_annual.csv", "forecast/ae_waterfall.csv"]
        for rel in data_outputs:
            a = pd.read_csv(OUTPUT_DIR / rel); b = pd.read_csv(out / rel)
            try:
                truth = [c for c in a.columns if c.startswith("true_")]   # synthetic-only comparison columns
                a = a.drop(columns=truth); b = b.drop(columns=truth)
                pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-9)
                print(f"data output identical: {rel} ({len(a)} rows)")
            except AssertionError as e:
                ok = False; print(f"DATA OUTPUT DIFFERS: {rel}: {str(e)[:300]}")
        skipped = [s["step"] for s in steps if s["status"] == "SKIPPED"]
        print("Skipped in production mode:", skipped)
        print("Ingested from:", manifest["ingest"]["source_root"], "| assumptions approved:", manifest["assumptions"]["approved"])
        ok &= set(skipped) == {"00_generate_synthetic_data.py", "00b_generate_finance_controls.py", "v1_annual_npv_engine.py",
                               "06_npv_backtest.py", "run_tests.py", "e2e_excel_nonneutral.py", "e2e_excel_pricing.py", "e2e_databricks_job_local.py",
                               "export_results.py"}
        # v4: the first production cycle registers its vintage in the persistent registry.
        reg = vol / "output" / "vintage_registry"
        ok &= reg.is_dir() and any(reg.iterdir())
        print("Vintage registry after the cycle:", sorted(p.name for p in reg.iterdir()) if reg.is_dir() else "MISSING")
        ok &= all(s["status"] in ("PASS", "SKIPPED") for s in steps) and manifest["assumptions"]["approved"]
        leaked = [str(p.relative_to(kit)) for p in [kit / "output", *(kit / "data").glob("*.csv")] if p.exists()]
        print("Files written inside the kit (must be none):", leaked or "none")
        ok &= not leaked
        ok &= (vol / "output" / "Humana_NPV_Forecasting_Model.xlsx").exists() and (vol / "data" / "claims.csv").exists()
        print("E2E PASS: production mode reproduces the synthetic results from production-layout extracts" if ok else "E2E FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""
e2e_excel_nonneutral.py  (v4)
-------------------------------------------------------------------------------
The base reconciliation (05) ties Excel to Python on the APPROVED assumption set, where the v4
Medicaid contract terms and D&V benefit categories sit at neutral settings (the synthetic contract
has none of them). A tie at neutral settings cannot show that the v4 formulas are right: a wrong
withhold or corridor formula multiplies by zero there. This test switches every v4 deterministic
mechanic to a NON-neutral value, rebuilds the workbook from that assumption set, recalculates it
in LibreOffice, and requires the same to-the-cent tie on cash NPV, DE NPV, every year and all 17
sensitivity drivers.

Non-neutral settings (illustrative, chosen so every branch is exercised):
  Medicaid  quality withhold 2% with 60% earned back; directed payments $25 PMPM growing 4%;
            acuity path 1.00 / 1.04 / 1.03 with a one-year risk-adjustment lag; risk corridor
            (target 86%, bands 2% / 5%, plan shares 50% / 0%) instead of the MLR floor.
  D&V       12-month waiting period and +1 pt trend add-on on MAJOR; pent-up relativities
            0.8 / 1.0 / 1.6 / 0.9.

Runs in a temporary output directory with its own assumption file (NPV_ASSUMPTIONS,
NPV_OUTPUT_DIR), so nothing the main run produced is touched. Exit code 0 = tie.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "code" / "python"
sys.path.insert(0, str(PY))
from kitpaths import OUTPUT_DIR  # noqa: E402
import assumption_control as _ac  # noqa: E402


def nonneutral(A: dict, variant: str = "corridor_loss") -> dict:
    """Non-neutral v4 settings. Variants exercise every settlement branch in Excel:
    corridor_loss  MLR above the target band every year (state pays the plan);
    corridor_gain  starting cost ratio 78%: MLR below the target band, crossing band 2 (state takes gains);
    floor_binding  MLR floor with withhold earn-back in the MLR base, cost ratio 78% so the floor binds."""
    A = json.loads(json.dumps(A))
    ct = A["MEDICAID"]["contract_terms"]
    ct["quality_withhold"].update(withhold_pct=0.02, expected_earn_back=0.6)
    ct["directed_payments"].update(pmpm_year1=25.0, annual_trend=0.04)
    ct["acuity_shift"].update(medical_factor_by_year=[1.0, 1.04, 1.03], risk_adjustment_lag_years=1)
    ct["mlr_rule"]["type"] = "corridor"
    bc = A["DENTAL_VISION"]["benefit_categories"]
    bc["waiting_period_months"] = [0, 0, 12, 0]
    bc["trend_add_on"] = [0.0, 0.0, 0.01, 0.0]
    bc["pent_up_relativity"] = [0.8, 1.0, 1.6, 0.9]
    for f in ("medicaid_contract_terms", "dv_benefit_categories"):
        A["global"]["features"][f] = True
    if variant in ("corridor_gain", "floor_binding"):
        A["MEDICAID"]["medical_cost_ratio_year1"] = 0.78
    if variant == "floor_binding":
        ct["mlr_rule"]["type"] = "floor"
    return A


VARIANTS = ["corridor_loss", "corridor_gain", "floor_binding"]


def run_variant(variant: str) -> bool:
    tmp = Path(tempfile.mkdtemp(prefix=f"npv_v4_{variant}_"))
    try:
        out = tmp / "output"
        # Inputs the workbook's experience and backtest sheets read (copied, never modified).
        for d in ("sql_results", "backtest"):
            if (OUTPUT_DIR / d).is_dir():
                shutil.copytree(OUTPUT_DIR / d, out / d)
        A = nonneutral(_ac.load_assumptions(), variant)
        af = tmp / "npv_assumptions_nonneutral.json"
        af.write_text(json.dumps(A, indent=1) + "\n")
        env = dict(os.environ, NPV_ASSUMPTIONS=str(af), NPV_OUTPUT_DIR=str(out))
        steps = [(PY, [sys.executable, "04_npv_engine.py"]), (ROOT, [sys.executable, "build_workbook.py"]),
                 (ROOT, [sys.executable, "recalc_workbook.py"]), (PY, [sys.executable, "05_reconcile_python_to_excel.py"])]
        for cwd, cmd in steps:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-3000:], r.stderr[-3000:])
                print(f"FAIL [{variant}]: {cmd[-1]} exited {r.returncode}")
                return False
        rec = json.loads((out / "controls" / "reconciliation.json").read_text())
        # Show which settlement branch the variant exercised (so a vacuous pass is visible).
        sys.path.insert(0, str(PY))
        import importlib.util
        spec = importlib.util.spec_from_file_location("eng_v", PY / "04_npv_engine.py")
        os.environ["NPV_ASSUMPTIONS"] = str(af)
        eng = importlib.util.module_from_spec(spec); spec.loader.exec_module(eng)
        del os.environ["NPV_ASSUMPTIONS"]
        y = eng.project_medicaid(A["MEDICAID"], A["global"])["yearly"]
        mlr = y["medical"] / (y["revenue"] + y["withhold_earned"])
        settle = y["mlr_remittance"] + y["withhold_earned"]          # before netting the withhold earn-back
        print(f"[{variant}] MLR range {mlr.min():.3f}-{mlr.max():.3f}; years paying the state {int((settle > 0).sum())}, "
              f"years paid by the state {int((settle < 0).sum())}; reconciliation {rec.get('result')}, "
              f"max sensitivity diff {rec.get('max_sensitivity_diff'):.6f}")
        return rec.get("result") == "PASS"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    results = {v: run_variant(v) for v in VARIANTS}
    ok = all(results.values())
    print("RESULT: " + ("PASS - v4 contract terms and benefit categories tie Excel to Python to the cent at non-neutral settings "
                        f"({', '.join(VARIANTS)})" if ok else f"FAIL - Excel and Python differ at non-neutral v4 settings: {results}"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

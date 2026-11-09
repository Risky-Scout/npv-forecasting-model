"""
e2e_excel_pricing.py  (v4.2)
-------------------------------------------------------------------------------
The v4.2 pricing solve recommends new premiums (config/pricing_targets.json). This test proves the
recommended pricing is reproduced by the independent Excel implementation: it writes an assumption
set with the RECOMMENDED premiums (Med Supp and D&V, from output/npv/npv_pricing_solve.csv),
reruns the engine, rebuilds and recalculates the workbook, and requires

  1. the to-the-cent Excel = Python reconciliation (cash, DE incl. the DAC tax, every year and
     all 17 sensitivity drivers) at the recommended pricing, and
  2. the DE NPV of each repriced line to equal the de_npv_recommended the pricing table reports.

Runs in a temporary output directory with its own assumption file (NPV_ASSUMPTIONS,
NPV_OUTPUT_DIR), so nothing the main run produced is touched. Exit code 0 = PASS.
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

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "code" / "python"
sys.path.insert(0, str(PY))
from kitpaths import OUTPUT_DIR  # noqa: E402
import assumption_control as _ac  # noqa: E402


def main() -> int:
    prc = pd.read_csv(OUTPUT_DIR / "npv" / "npv_pricing_solve.csv")
    rec = prc[prc.item == "Premium (risk-based target)"].set_index("lob")
    A = _ac.load_assumptions()
    for lob in rec.index:
        A[lob]["annual_premium_year1"] = float(rec.loc[lob, "recommended"])
    tmp = Path(tempfile.mkdtemp(prefix="npv_v42_pricing_"))
    try:
        out = tmp / "output"
        for d in ("sql_results", "backtest"):
            if (OUTPUT_DIR / d).is_dir():
                shutil.copytree(OUTPUT_DIR / d, out / d)
        af = tmp / "npv_assumptions_recommended.json"
        af.write_text(json.dumps(A, indent=1) + "\n")
        env = dict(os.environ, NPV_ASSUMPTIONS=str(af), NPV_OUTPUT_DIR=str(out))
        for cwd, cmd in [(PY, [sys.executable, "04_npv_engine.py"]), (ROOT, [sys.executable, "build_workbook.py"]),
                         (ROOT, [sys.executable, "recalc_workbook.py"]), (PY, [sys.executable, "05_reconcile_python_to_excel.py"])]:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-3000:], r.stderr[-3000:])
                print(f"FAIL: {cmd[-1]} exited {r.returncode}")
                return 1
        recon = json.loads((out / "controls" / "reconciliation.json").read_text())
        summ = pd.read_csv(out / "npv" / "npv_summary.csv").set_index("lob")
        ok = recon.get("result") == "PASS"
        for lob in rec.index:
            d = abs(float(summ.loc[lob, "de_npv"]) - float(rec.loc[lob, "de_npv_recommended"]))
            print(f"{lob}: premium {rec.loc[lob, 'recommended']:,.2f}  DE NPV {summ.loc[lob, 'de_npv']:,.2f}  "
                  f"(pricing table {rec.loc[lob, 'de_npv_recommended']:,.2f}, diff {d:.6f})")
            ok &= d < 0.01
        print(f"Excel = Python at the recommended pricing: {recon.get('result')}, max sensitivity diff {recon.get('max_sensitivity_diff'):.6f}")
        print("RESULT: " + ("PASS - the recommended pricing ties Excel to Python to the cent and reproduces the pricing table"
                            if ok else "FAIL - Excel / Python / pricing table disagree at the recommended pricing"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

"""
05_reconcile_python_to_excel.py  (version 3: cash NPV and after-tax profit test)
-------------------------------------------------------------------------------
Independent tie-out between the Excel workbook and the Python engine.
Two models built separately from the same assumption file should agree to the
cent. If they don't, one of them has a formula error, and it is found before
anyone relies on the number.

This is model validation by independent re-implementation, in the sense of
ASOP No. 56 (Modeling): the workbook is a second build of the same
specification, not an export of Python results, so agreement tests both the
formulas and the reading of the specification. It does not validate the
assumptions themselves; that is the job of the experience studies and of
06_npv_backtest.py (Tests B and C).

Checks:
  1. NPV, PV premium, PV claims by line: Excel vs Python
  2. Year-by-year profit and PV profit by line: max absolute difference.
     Totals alone can agree while errors offset between years (for example cash
     shifted from one year to the next, or a misplaced discount factor), and
     the year-by-year columns are what reviewers read, so every year is compared.
  3. Sensitivity scenarios: Excel tornado vs Python tornado (base, favorable
     and adverse NPV for all 17 drivers, 37 re-projections), which tests the
     shock mechanics as well as the base case.
  4. Profit test (v3): DE NPV by line and the PV of distributable earnings for
     every year including the claim-runoff year.

Tolerance: $0.01 absolute on every compared figure. Both builds are
deterministic, so any real formula difference shows up far above rounding noise.

Exit code: 0 on PASS, 1 on FAIL, so a scheduled job (Databricks workflow, CI)
stops and flags the run instead of publishing results that do not reconcile.

Requires the workbook to have been recalculated (open + save in Excel, or the
LibreOffice recalc step used to build it) so cached values exist: openpyxl
reads stored values and does not evaluate formulas.
Usage: python 05_reconcile_python_to_excel.py [path/to/workbook.xlsx]
-------------------------------------------------------------------------------
"""
import importlib.util
import sys
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import pandas as pd
from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
xl_path = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_DIR / "Humana_NPV_Forecasting_Model.xlsx"

# Load the engine by path (the module name starts with a digit, so a normal import is not possible).
spec = importlib.util.spec_from_file_location("engine", HERE / "04_npv_engine.py")
eng = importlib.util.module_from_spec(spec); spec.loader.exec_module(eng)

# data_only=True returns the cached results of formulas, not the formula text.
wb = load_workbook(xl_path, data_only=True)
TOL = 0.01
ok = True

# ----------------------------------------------------------------------------
# Checks 1 and 2: headline PVs and year-by-year profit by line.
# Cell addresses follow the workbook layout: summary block in D5:D8, annual rows from 17.
# ----------------------------------------------------------------------------
sheets = {"MEDSUPP": "MedSupp_NPV", "DENTAL_VISION": "DentalVision_NPV", "MEDICAID": "Medicaid_NPV"}
PROFIT_COL = "K"            # annual 'Profit (cash)' column on every annual sheet; rows start at 17
rows = []
for lob, sh in sheets.items():
    ws = wb[sh]
    p = eng.PROJECT[lob](eng.A[lob], eng.A["global"])
    tab = eng.annual_table(lob, p)
    s = eng.summarize(lob, tab)
    xl = {"npv": ws["D5"].value, "pv_revenue": ws["D7"].value, "pv_claims": ws["D8"].value}
    for k in xl:
        diff = xl[k] - s[k]
        rows.append(dict(lob=lob, metric=k, excel=xl[k], python=s[k], diff=diff))
        ok &= abs(diff) < TOL
    # Undiscounted annual profit: catches cash-flow errors before discounting enters.
    xl_profit = [ws[f"{PROFIT_COL}{17 + i}"].value for i in range(len(tab))]
    max_diff = max(abs(a - b) for a, b in zip(xl_profit, tab["profit"]))
    rows.append(dict(lob=lob, metric="max yearly profit diff", excel=None, python=None, diff=max_diff))
    ok &= max_diff < TOL
    # Discounted (and, for Medicaid, renewal-weighted) annual profit. The Medicaid sheet carries
    # extra columns for remittance and renewal probability, so its PV profit sits in column N.
    xl_pv = [ws[f"{'N' if lob == 'MEDICAID' else 'L'}{17 + i}"].value for i in range(len(tab))]
    max_pv = max(abs(a - b) for a, b in zip(xl_pv, tab["pv_profit"]))
    rows.append(dict(lob=lob, metric="max yearly PV profit diff", excel=None, python=None, diff=max_pv))
    ok &= max_pv < TOL

# ----------------------------------------------------------------------------
# Check 4 (v3): after-tax profit test. DE NPV on each annual sheet (D13) and the PV of
# distributable earnings for every year (including the runoff year) on the *_Profit sheets.
# ----------------------------------------------------------------------------
profit_sheets = {"MEDSUPP": ("MedSupp_Profit", "V"), "DENTAL_VISION": ("DentalVision_Profit", "V"), "MEDICAID": ("Medicaid_Profit", "Y")}
for lob, (sh, col) in profit_sheets.items():
    p = eng.PROJECT[lob](eng.A[lob], eng.A["global"])
    pt = eng.profit_test(lob, p, eng.A[lob], eng.A["global"])
    ptab = eng.profit_table(lob, pt)
    xl_de = wb[sheets[lob]]["D13"].value
    rows.append(dict(lob=lob, metric="de_npv", excel=xl_de, python=float(pt["de_npv"]), diff=xl_de - float(pt["de_npv"])))
    ok &= abs(xl_de - float(pt["de_npv"])) < TOL
    xl_pv = [wb[sh][f"{col}{5 + i}"].value for i in range(len(ptab))]
    max_pv = max(abs(a - b) for a, b in zip(xl_pv, ptab["pv_distributable_earnings"]))
    rows.append(dict(lob=lob, metric="max yearly PV DE diff", excel=None, python=None, diff=max_pv))
    ok &= max_pv < TOL

print(pd.DataFrame(rows).to_string(na_rep="n/a", index=False, float_format=lambda x: f"{x:,.4f}"))

# ----------------------------------------------------------------------------
# Check 3: sensitivity tie-out.
# Sensitivity sheet rows 6-22 hold the 17 drivers (Med Supp 6, D&V 5, Medicaid 6) in the same
# order as eng.SENS; rows are matched by position within each line.
# ----------------------------------------------------------------------------
tor_py = eng.tornado()
ws = wb["Sensitivity"]
xl_tor = pd.DataFrame([[ws.cell(r, c).value for c in range(1, 7)] for r in range(6, 6 + sum(len(v) for v in eng.SENS.values()))],
                      columns=["line", "driver", "base", "fav", "adv", "swing"])
lob_map = {"Med Supp": "MEDSUPP", "Dental & Vision": "DENTAL_VISION", "Medicaid": "MEDICAID"}
diffs = []
for lob in eng.SENS:
    xs = xl_tor[xl_tor.line.map(lob_map) == lob].reset_index(drop=True)
    ps = tor_py[tor_py.lob == lob].reset_index(drop=True)
    for i in range(len(ps)):
        diffs.append(max(abs(xs.loc[i, "fav"] - ps.loc[i, "npv_favorable_side"]),
                         abs(xs.loc[i, "adv"] - ps.loc[i, "npv_adverse_side"]),
                         abs(xs.loc[i, "base"] - ps.loc[i, "base_npv"])))
assert len(diffs) == sum(len(v) for v in eng.SENS.values()), "Sensitivity sheet and engine drivers differ"
print(f"\nSensitivity drivers compared: {len(diffs)}   max |Excel - Python| = {max(diffs):,.6f}")
ok &= max(diffs) < TOL
print("\nRESULT:", "PASS - Excel and Python tie to the cent" if ok else "FAIL - investigate differences above")
# Status marker for the run manifest: results are reported only when this run's reconciliation passed
# (needed where step status is not recorded centrally, e.g. a Databricks job).
import json as _json  # noqa: E402
(OUTPUT_DIR / "controls").mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "controls" / "reconciliation.json").write_text(_json.dumps(
    {"result": "PASS" if ok else "FAIL", "sensitivity_drivers_compared": len(diffs), "max_sensitivity_diff": float(max(diffs))}, indent=2) + "\n")
# Non-zero exit on failure so an orchestrated pipeline halts here.
sys.exit(0 if ok else 1)

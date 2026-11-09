"""Recalculates every formula in the workbook with LibreOffice (headless) so cached
values exist for the reconciliation script (05_reconcile_python_to_excel.py).

Why this step is needed: the workbook is written by openpyxl, which stores
formulas but does not calculate them. A formula cell's value is only saved
("cached") when a spreadsheet application calculates and saves the file. Until
then, reading the workbook with data_only=True returns None for every formula,
and the Excel model cannot be reconciled to the Python NPV engine.

After recalculation the script scans every formula cell and fails if any has
no value or evaluates to an Excel error (#REF!, #DIV/0!, #NAME?, #VALUE!, ...),
so a broken formula is caught here rather than in the reconciliation.

Without LibreOffice: open the workbook in Excel, save it, then run
    python code/python/05_reconcile_python_to_excel.py

Exit codes: 0 = all formulas valued and error-free, 1 = missing values or errors,
2 = LibreOffice not found.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent / 'code' / 'python'))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent
xl = OUTPUT_DIR / "Humana_NPV_Forecasting_Model.xlsx"

# Look for LibreOffice on PATH first, then in the default macOS and Windows install locations.
candidates = [shutil.which("soffice"), shutil.which("libreoffice"),
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe"]
soffice = next((c for c in candidates if c and os.path.exists(c)), None)
if soffice is None:
    print("LibreOffice not found. Open output/Humana_NPV_Forecasting_Model.xlsx in Excel, save it,\n"
          "then run: python code/python/05_reconcile_python_to_excel.py")
    sys.exit(2)

with tempfile.TemporaryDirectory() as tmp:
    # Converting xlsx -> xlsx makes LibreOffice load, calculate and re-save every formula.
    # Output goes to a temp folder first, so a failed conversion never overwrites the workbook.
    r = subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", tmp, str(xl)],
                       capture_output=True, text=True, timeout=600)
    out = Path(tmp) / xl.name
    if r.returncode != 0 or not out.exists():
        print(r.stdout, r.stderr)
        sys.exit("LibreOffice recalculation failed")
    shutil.copyfile(out, xl)

# Check: every formula now has a value and none is an Excel error.
# The workbook is opened twice: wf holds the formulas, wv the cached values of the same cells.
wf = load_workbook(xl)
wv = load_workbook(xl, data_only=True)
n_formulas = n_missing = 0
errors = []
for ws in wf.worksheets:
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                n_formulas += 1
                v = wv[ws.title][c.coordinate].value
                if v is None:
                    n_missing += 1                     # formula never calculated
                elif isinstance(v, str) and v.startswith("#"):
                    errors.append(f"{ws.title}!{c.coordinate} = {v}")   # Excel error value
print(f"Formulas: {n_formulas:,}   without a value: {n_missing:,}   errors: {len(errors)}")
for e in errors[:20]:   # show the first 20; the count above is the full total
    print("  ", e)
sys.exit(0 if not errors and n_missing == 0 else 1)

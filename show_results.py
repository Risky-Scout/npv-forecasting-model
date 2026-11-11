"""Prints the headline results and checks of the last run, so nobody has to open a CSV or the
workbook to see them. Reads only what run_all.py wrote to output/ (standard library only).

    python show_results.py        (or: make results)
"""
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("NPV_OUTPUT_DIR", ROOT / "output"))
LINES = {"MEDSUPP": "Med Supp, Plan G at 65 (per 1,000)",
         "DENTAL_VISION": "Dental & Vision (per 1,000)",
         "MEDICAID": "Medicaid contract (10,000 members)"}


def need(path: Path) -> Path:
    if not path.exists():
        sys.exit(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} not found. "
                 "Run the model first:  make run   (or python run_all.py)")
    return path


def rows(path: Path):
    with open(need(path), newline="") as f:
        return list(csv.DictReader(f))


def money(x) -> str:
    v = float(x)
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def pct(x, d=1) -> str:
    return f"{float(x) * 100:.{d}f}%"


manifest = json.loads(need(OUT / "run_manifest.json").read_text())
steps = {s["step"]: s["status"] for s in (manifest.get("steps") or {}).get("steps", [])}
summary = {r["lob"]: r for r in rows(OUT / "npv" / "npv_summary.csv")}
pricing = rows(OUT / "npv" / "npv_pricing_solve.csv")
# Read from the run's own output files, so the summary also works when the manifest withholds results
p_neg = {r[""]: r["prob_npv_negative"] for r in rows(OUT / "npv" / "npv_monte_carlo_stats.csv")}
port = next(({"cash_npv": r["cash_npv_per_1000"], "de_npv": r["de_npv_per_1000"]}
             for r in rows(OUT / "npv" / "medsupp_model_points.csv") if r["band"] == "PORTFOLIO"), {})
run, a = manifest.get("run", {}), manifest.get("assumptions") or {}
approved = bool(a.get("approved"))

print(f"\nNPV forecasting model {a.get('model_version', '')}: last run {run.get('run_id')} "
      f"({run.get('mode')} mode)")
print(f"Status: {manifest.get('results_status')}")
if approved:
    print(f"Assumption set: {a.get('approval_id')} (approved)")
else:
    print("Assumption set: edited, not approved. The figures below are what the edited assumptions produce;\n"
          "  the manifest withholds them from release until the set is approved (README, 'Changing an assumption').")

print("\nValuation as priced today")
print(f"  {'Line':<44}{'Cash NPV':>14}{'DE NPV':>14}{'PV loss ratio':>15}{'P(DE < 0)':>11}")
for lob, label in LINES.items():
    r = summary[lob]
    print(f"  {label:<44}{money(r['npv']):>14}{money(r['de_npv']):>14}"
          f"{pct(r['pv_loss_ratio']):>15}{pct(p_neg.get(lob + '_DE', 'nan')):>11}")
if port:
    print(f"  {'Med Supp portfolio, age x plan (per 1,000)':<44}{money(port['cash_npv']):>14}"
          f"{money(port['de_npv']):>14}")

print("\nPricing recommendation (target: P(DE NPV < 0) at most 10%)")
for r in pricing:
    lob, item, status = r["lob"], r["item"], r["status"]
    if item == "Premium (risk-based target)" and lob == "MEDSUPP":
        print(f"  Med Supp Plan G at 65: {status.lower()}; {r['note']}")
    elif item == "Premium (risk-based target)" and lob == "DENTAL_VISION":
        if status.upper().startswith("MEETS"):
            print(f"  Dental & Vision: {status.lower()} (${float(r['current']):,.2f} a year); "
                  f"P(DE < 0) {pct(r['p_de_negative_current'])}; {r['market_check']}")
        else:
            print(f"  Dental & Vision: {status.lower()} ${float(r['current']):,.2f} -> ${float(r['recommended']):,.2f} a year "
                  f"({float(r['change']) * 100:+.1f}%, {r['note']}); P(DE < 0) "
                  f"{pct(r['p_de_negative_current'])} -> {pct(r['p_de_negative_recommended'])}; {r['market_check']}")
    elif item.startswith("Portfolio risk check"):
        print(f"  Med Supp portfolio: P(DE < 0) {pct(r['p_de_negative_current'])} -> "
              f"{pct(r['p_de_negative_recommended'])} at the recommended cell prices "
              f"(DE {money(r['de_npv_recommended'])}); details in output/npv/medsupp_cell_pricing.csv")
    elif item == "Bid recommendation":
        print(f"  Medicaid: {status.lower()}. {r['note']}")

recon = manifest.get("reconciliation") or {}
print("\nChecks in this run")
print(f"  Excel = Python, this run's assumptions:      {recon.get('result')} "
      f"({recon.get('sensitivity_drivers_compared')} sensitivities, max difference "
      f"${float(recon.get('max_sensitivity_diff') or 0):.2f})")
for name, label in [("e2e_excel_nonneutral.py", "Excel = Python, non-neutral contract terms"),
                    ("e2e_excel_pricing.py", "Excel = Python, at the recommended prices"),
                    ("06_npv_backtest.py", "Backtest, Tests A-C"),
                    ("run_tests.py", "Automated test suite"),
                    ("e2e_databricks_job_local.py", "Databricks job, 17 tasks run locally")]:
    status_ = steps.get(name, "not run")
    if status_ in ("SKIPPED", "not run"):
        status_ = "not part of make quick: run make run (about 15 min)"
    print(f"  {label + ':':<45}{status_}")
alerts = (manifest.get("monitoring") or {}).get("alerts")
print(f"  {'Drift monitor alerts:':<45}{alerts} " + ("(all intended; see README, section: What you'll see in the log)" if approved
      else "(the approved base run has 5; the difference comes from the edited assumptions)"))

print("\nMore detail")
print("  Workbook:  output/Humana_NPV_Forecasting_Model.xlsx  (in a Codespace: right-click > Download)")
print("  Pricing:   output/npv/npv_pricing_solve.csv, output/npv/medsupp_cell_pricing.csv")
print("  Every figure quoted in the docs: output/results.json\n")

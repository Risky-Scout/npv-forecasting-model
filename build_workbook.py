"""
Build Humana_NPV_Forecasting_Model.xlsx: an Excel NPV model for three lines of business
(Medicare Supplement, individual Dental & Vision, new-state Medicaid managed care).

Purpose
    The workbook is an independent re-implementation of code/python/04_npv_engine.py. It reads
    the same assumption file and must reproduce the Python NPVs to the cent. Two engines built
    separately from one specification, that agree, are the validation evidence for the model
    (ASOP No. 56, Modeling: testing that the model output reasonably represents the intended design).

Inputs
    code/python/npv_assumptions.json   single source of every assumption (also drives SAS/Databricks)
    output/npv/npv_summary.csv         Python engine NPVs, pasted only for the Dashboard tie-out
    output/sql_results/*.csv           experience-study output from the SQL kit (Experience sheet)
    output/backtest/*.csv              results of python/06_npv_backtest.py (Backtest sheet)

Sheet map (final tab order)
    README                 plain-English guide, color code, cash-flow conventions, controls
    Dashboard              headline results, Excel-vs-Python tie-out, lever check, swing factors, charts
    Assumptions            every input as a named cell (DiscRate, MS_Prem1, MD_MLRFloor, ...)
    MedSupp_NPV / _Monthly / _Profit     cohort of Plan G policies, 20-year monthly projection (two health classes)
    DentalVision_NPV / _Monthly / _Profit cohort of individual policies, 10-year monthly projection
    Medicaid_NPV / _Monthly / _Profit    open-membership state contract, 10-year monthly projection (three rate cells)
                           *_Profit sheets: after-tax distributable-earnings profit test (claim lag, reserves,
                           required capital, investment income, federal income tax)
    Sensitivity            tornado table (favorable / adverse NPV per driver) and base-case check
    Sens_Calc              37 compact monthly re-projections (13 + 11 + 13 scenarios) feeding Sensitivity
    Experience             completed PMPMs, lapse study and quarterly A/E monitoring from the SQL kit
    Backtest               model NPV vs simulated realized outcomes; why version 1 was replaced

Design principles
    1. Single assumption source: nothing is typed into a formula; every input lives on Assumptions
       and is referenced by a workbook-level defined name, so formulas read like the specification.
    2. Formatting convention: blue font = hard-coded input or pasted external result; black = formula;
       green = link to another sheet; yellow fill = key assumption.
    3. Every model number is a live formula. Python only writes formulas and pasted study results;
       it computes no projection values itself, so the workbook cannot silently inherit a Python bug.
    4. Monthly engine, annual roll-up: cash flows are projected one row per month; the annual
       sheets are SUMIFS on policy / contract year, so annual NPV equals the sum of monthly PVs.
    5. Scenario levers on Assumptions (multipliers 1.0, add-ons 0.0) are neutral in the base case;
       the Dashboard reports whether they are, because the tie-out to Python is only valid at neutral.

Conventions shared by all engines
    Annual rates are converted to monthly as 1 - (1 - q)^(1/12) so twelve months reproduce the
    annual decrement exactly. Premium is received monthly in advance (discounted at month start);
    claims and expenses are paid mid-month. Acquisition and start-up costs are paid at time 0.

Formulas are written without cached values; fullCalcOnLoad makes Excel calculate them on open
(recalc_workbook.py recalculates headlessly with LibreOffice so that
    code/python/05_reconcile_python_to_excel.py can compare cached values with the Python engine).
"""
import json
import re
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent / 'code' / 'python'))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.workbook.defined_name import DefinedName

# Inputs: one assumption file shared with the Python engine; Python NPVs used only for the tie-out.
ROOT = Path(__file__).resolve().parent
import assumption_control as _ac  # noqa: E402  (same resolver as the gate and the engine)
A = _ac.load_assumptions()
PY = pd.read_csv(OUTPUT_DIR / "npv/npv_summary.csv").set_index("lob")
SQLR = OUTPUT_DIR / "sql_results"
# Run mode decides which commentary is shown: the synthetic book's known stories (planted data
# problems, redetermination wave) must never appear next to production numbers.
MODE = _ac.run_config().get("mode", "synthetic")
SYNTH = MODE == "synthetic"

# Workbook-wide styles. Color convention: blue font = input, black = formula, green = cross-sheet link,
# yellow fill = key assumption, light yellow = other input.
wb = Workbook()
F = "Arial"
NAVY, GREEN, GREY = "1F3864", "4E7D2A", "595959"
f_base = Font(name=F, size=10)
f_bold = Font(name=F, size=10, bold=True)
f_title = Font(name=F, size=16, bold=True, color=NAVY)
f_sub = Font(name=F, size=10, italic=True, color=GREY)
f_head = Font(name=F, size=10, bold=True, color="FFFFFF")
f_input = Font(name=F, size=10, color="0000FF")
f_link = Font(name=F, size=10, color="008000")
f_sec = Font(name=F, size=12, bold=True, color=NAVY)
fill_head = PatternFill("solid", fgColor=NAVY)
fill_key = PatternFill("solid", fgColor="FFFF00")
fill_input = PatternFill("solid", fgColor="FFF9DB")
fill_sum = PatternFill("solid", fgColor="E8F0DD")
fill_band = PatternFill("solid", fgColor="F3F5F9")
thin = Side(style="thin", color="BFBFBF")
box = Border(top=thin, bottom=thin, left=thin, right=thin)

# Number formats: negatives in parentheses, zero shown as a dash (accounting style).
FMT_USD = '$#,##0;($#,##0);"-"'
FMT_USD2 = '$#,##0.00;($#,##0.00);"-"'
FMT_NUM = '#,##0;(#,##0);"-"'
FMT_NUM1 = '#,##0.0;(#,##0.0);"-"'
FMT_PCT = '0.0%;(0.0%);"-"'
FMT_PCT2 = '0.00%;(0.00%);"-"'
FMT_F4 = '0.0000'


# -----------------------------------------------------------------------------
# Layout helpers
# -----------------------------------------------------------------------------
def name(n, ref):
    wb.defined_names[n] = DefinedName(n, attr_text=ref)


def style_range(ws, rng, font=None, fill=None, fmt=None, border=None, align=None):
    for row in ws[rng]:
        for c in row:
            if font: c.font = font
            if fill: c.fill = fill
            if fmt: c.number_format = fmt
            if border: c.border = border
            if align: c.alignment = align


def header_row(ws, r, c0, labels, widths=None):
    for i, t in enumerate(labels):
        c = ws.cell(r, c0 + i, t)
        c.font, c.fill, c.border = f_head, fill_head, box
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[r].height = 42


def title(ws, t, sub):
    ws["A1"] = t; ws["A1"].font = f_title
    ws["A2"] = sub; ws["A2"].font = f_sub
    ws.sheet_view.showGridLines = False



# =============================================================================
# README
# =============================================================================
ws = wb.active; ws.title = "README"
title(ws, "NPV Forecasting Model — Medicare Supplement, Dental & Vision, Medicaid",
      ("Version 4.2. Synthetic practice data; assumptions from public sources, statute and documented judgment (docs/ASSUMPTION_BASIS.md). Not Humana data or Humana assumptions. Production use requires Humana data, calibration and approval (see docs/)."
       if SYNTH else "Version 4.2. PRODUCTION run on source extracts (see output/run_manifest.json for data and assumption lineage)."))
# Each tuple is (text, unused); a line whose text is in HEADINGS is styled as a section heading.
lines = [
    ("What this workbook does", None),
    ("Projects cash flows month by month for three lines of business and discounts them to a Net Present Value (NPV): the value a block of business creates for the company today.", None),
    ("Medicare Supplement and Dental & Vision are modeled as a cohort of 1,000 policies sold today. Medicaid is modeled as a new-state managed-care contract starting with 10,000 members.", None),
    ("This is version 4. Version 1 projected annually; a backtest found its conventions biased (Medicaid by −12%), so version 2 was rebuilt monthly. Version 3 added anti-selective and dynamic shock lapse (Med Supp), Medicaid rate cells with risk adjustment, and an after-tax profit test. Version 4 sets trend by a credibility blend of the normalized fit with a benchmark, adds Medicaid contract terms (withhold, directed payments, risk corridor, acuity path), D&V benefit categories, Med Supp model points, an in-force forecast with an A/E waterfall, process-risk Monte Carlo with a rate-filing response, break-evens and planning scenarios (V4_Analytics sheet). Version 4.1 sets every assumption from public sources, statute or documented judgment, adds the IRC §848 DAC tax to the profit test (live formulas on MedSupp_Profit), D&V waiting periods that defer pent-up demand, and on V4_Analytics the Med Supp rating bases (attained-age, issue-age, community), the judgment-assumption sensitivity and the break-even position.", None),
    ("Two results per line: Cash NPV (pre-tax, claims at incurred timing; the version 2 measure) and DE NPV (after-tax distributable earnings, after claim payment lag, reserves, required capital and tax, including the IRC §848 DAC tax; the decision measure).", None),
    ("The v3 feature switches on the Assumptions sheet (1 = on) can all be set to 0 to reproduce version 2 exactly.", None),
    ("", None),
    ("How to read it (sheet by sheet)", None),
    ("Assumptions — every input in one place, with a plain-English note and source. Change a blue cell and the whole model updates.", None),
    ("MedSupp_Monthly, DentalVision_Monthly, Medicaid_Monthly — the engine: one row per month, each column one step of the calculation, left to right.", None),
    ("MedSupp_NPV, DentalVision_NPV, Medicaid_NPV — the annual view: each year is the sum of its 12 months (SUMIFS), plus the results block (cash and DE NPV).", None),
    ("MedSupp_Profit, DentalVision_Profit, Medicaid_Profit — the profit test: claims paid on the lag pattern, claim reserve, MLR liability, required capital, investment income, tax, distributable earnings.", None),
    ("Sensitivity — the tornado table; Sens_Calc re-runs the monthly model with one assumption shocked at a time (37 scenarios, all live formulas).", None),
    ("Experience — what the (synthetic) data shows: completed PMPMs, loss ratios, lapse rates, actual-vs-expected monitoring.", None),
    ("Backtest — how good the NPV predictions are: model vs 4,000 simulated realized cohorts per line, and what the backtest found in version 1.", None),
    ("Dashboard — the answers on one page, plus the tie-out to the independent Python engine.", None),
    ("", None),
    ("Color code", None),
    ("Blue text = an input you can change.  Black text = a formula.  Green text = a link to another sheet.  Yellow fill = a key assumption.", None),
    ("", None),
    ("Cash-flow conventions (the real contract mechanics)", None),
    ("Premium is collected monthly in advance from everyone in force at the start of the month. Commission and premium tax ride on premium.", None),
    ("A lapse takes effect at the end of the month already paid for (the member is covered all month); deaths occur mid-month on average.", None),
    ("Claims and maintenance are paid mid-month. Med Supp's rate increase hits on the policy anniversary, so the extra 'shock' lapse happens in month 1 of each renewal year.", None),
    ("Acquisition (Med Supp, D&V) and start-up (Medicaid) costs are paid at issue: time 0, not discounted.", None),
    ("Medicaid members churn monthly; new members join evenly through the year and can churn from the next month. The 85% minimum-MLR remittance is settled on each contract year and paid six months after it closes.", None),
    ("", None),
    ("Key formulas in plain English", None),
    ("Monthly rate = 1 − (1 − annual rate)^(1/12), so 12 months of the monthly rate reproduce the annual rate exactly.", None),
    ("In force next month = in force − deaths − lapses.  Exposure = in force − ½ × deaths.", None),
    ("Premium = in force × annual premium ÷ 12.  Claims = exposure × annual claim cost ÷ 12.", None),
    ("PV of a month's cash = (premium − commission − tax) × v^(month start) − (claims + maintenance) × v^(mid-month), with v = 1 ÷ (1 + discount rate).", None),
    ("NPV = sum of monthly PVs − acquisition at issue.  PV loss ratio = PV claims ÷ PV premium.  Margin = NPV ÷ PV premium.", None),
    ("", None),
    ("Controls built in", None),
    ("1. Single source of truth: the same assumption file drives this workbook, the Python engine and the Databricks notebook (the SAS mirror is retired). Each input's note on the Assumptions sheet is its basis from the assumption register.", None),
    ("2. Independent tie-out: the Dashboard compares this workbook's cash and DE NPVs with the Python engine's; the reconciliation script checks every year, every profit-test year and all 17 sensitivity drivers to the cent.", None),
    ("3. Scenario levers on the Assumptions sheet are neutral (1.0 / 0.0) for the base case; the Dashboard flags if they are not.", None),
    ("4. Every annual sheet adds back to its monthly sheet; the annual NPV equals the sum of monthly PVs.", None),
    ("5. Profit test identity: with tax 0, capital 0, earned rate = discount rate and immediate claim payment, DE NPV equals cash NPV (tested in Python).", None),
    ("", None),
    ("Profit test in plain English", None),
    ("Paid claims = this month's and earlier months' incurred claims × the payment pattern.  Claim reserve = last month's reserve + incurred − paid.", None),
    ("Book profit = start-of-month cash × (1 + j) + (mid-month cash − paid claims) × (1 + j)^½ − increase in claim reserve − MLR remittance accrued + j × (reserves held last month).", None),
    ("Tax = tax rate × (book profit + j × capital held).  Distributable earnings = book profit + j × capital − tax − increase in required capital.  j = monthly investment earned rate.", None),
]
HEADINGS = {"What this workbook does", "How to read it (sheet by sheet)", "Color code", "Cash-flow conventions (the real contract mechanics)",
            "Key formulas in plain English", "Controls built in", "Profit test in plain English"}
r = 4
for t, _ in lines:
    c = ws.cell(r, 1, t)
    c.font = f_sec if t in HEADINGS else f_base
    c.alignment = Alignment(wrap_text=True, vertical="top")
    r += 1
ws.column_dimensions["A"].width = 135

# =============================================================================
# ASSUMPTIONS
# =============================================================================
wa = wb.create_sheet("Assumptions")
title(wa, "Assumptions", "Blue = input. Yellow = key assumption. Every model formula references these cells by name.")
header_row(wa, 4, 1, ["Assumption", "Value", "Name used in formulas", "What it means / source"])
for col, w in zip("ABCD", [44, 14, 22, 92]):
    wa.column_dimensions[col].width = w

# Each assumption row becomes one blue input cell plus a workbook-level defined name pointing at
# it, so engine formulas read MS_Prem1, DiscRate, etc. rather than cell addresses. Values come
# straight from npv_assumptions.json; a None value marks a section heading row.
rows = []  # (label, value, name, note, fmt, key)
g = A["global"]
rows += [("GLOBAL", None, None, None, None, False),
         ("Discount rate (hurdle rate)", g["discount_rate"], "DiscRate", "Required return used to value future profits. Illustrative.", FMT_PCT, True),
         ("Expense inflation", g["expense_inflation"], "ExpInfl", "Annual growth in per-policy / per-member admin costs. Illustrative.", FMT_PCT, False)]
fe = g["features"]
rows += [("V3 FEATURE SWITCHES (1 = on, 0 = off; all 0 reproduces version 2)", None, None, None, None, False),
         ("Anti-selective shock lapse (Med Supp)", int(fe["antiselective_shock_lapse"]), "F_AntiSel", "Only healthier 'shoppers' take the rate-increase shock lapse; the remaining block's claim cost rises.", "0", True),
         ("Dynamic shock lapse (Med Supp)", int(fe["dynamic_shock_lapse"]), "F_DynLapse", "Shock lapse moves with the size of the rate increase.", "0", True),
         ("Medicaid rate cells + risk adjustment", int(fe["medicaid_rate_cells"]), "F_RateCells", "Child / Adult / ABD cells with their own mix, capitation, cost and churn; budget-neutral risk adjustment.", "0", True),
         ("Correlated Monte Carlo (Python only)", int(fe["correlated_monte_carlo"]), "F_CorrMC", "The Monte Carlo runs in the Python engine; this switch is shown for completeness and drives no formula here.", "0", False),
         ("V4: Medicaid contract terms", int(fe.get("medicaid_contract_terms", False)), "F_MDTerms", "Quality withhold, directed payments, MLR floor or risk corridor, acuity path with lagged risk adjustment. Neutral settings = v3.", "0", True),
         ("V4: D&V benefit categories", int(fe.get("dv_benefit_categories", False)), "F_DVCats", "Preventive / basic / major / vision with their own trend add-on, pent-up and waiting period. Neutral settings = v3.", "0", True),
         ("V4: Monte Carlo process risk (Python only)", int(fe.get("mc_process_risk", False)), "F_ProcMC", "Year-to-year trend and rate volatility. Drives no formula here.", "0", False),
         ("V4: Monte Carlo rate-filing response (Python only)", int(fe.get("mc_management_response", False)), "F_MgmtResp", "Med Supp / D&V filings follow emerging trend. Drives no formula here.", "0", False)]
pt = g["profit_test"]
rows += [("PROFIT TEST (after-tax distributable earnings)", None, None, None, None, False),
         ("Federal income tax rate", pt["federal_income_tax_rate"], "PT_Tax", "21% US corporate rate. Tax losses assumed used against other company income. In production: Corporate Tax.", FMT_PCT, True),
         ("Investment earned rate on assets", pt["investment_earned_rate"], "PT_Earn", "Earned on assets backing claim reserves, the MLR liability and required capital. Illustrative; in production: Investments.", FMT_PCT, True),
         ("Monthly earned rate j (formula)", "=(1+PT_Earn)^(1/12)-1", "PT_J", "(1 + earned rate)^(1/12) − 1.", "0.000000", False),
         ("DAC tax rate, Med Supp (IRC §848, % of net premiums capitalized)", pt.get("dac_tax_rate_by_line", {}).get("MEDSUPP", 0.0), "MS_DAC", "Guaranteed-renewable A&H ('other' specified contracts): 9.2%, amortized over 180 months (half-year convention).", FMT_PCT, False),
         ("DAC tax rate, D&V", pt.get("dac_tax_rate_by_line", {}).get("DENTAL_VISION", 0.0), "DV_DAC", "0: excepted-benefit dental treated as cancellable (outside §848). Confirm with Corporate Tax.", FMT_PCT, False)]
m = A["MEDSUPP"]
rows += [("MEDICARE SUPPLEMENT (cohort of policies, Plan G, attained-age rated)", None, None, None, None, False),
         ("Projection years", m["projection_years"], "MS_Years", "Long-duration product; 20 years captures nearly all value.", FMT_NUM, False),
         ("Policies issued (cohort size)", m["cohort_size"], "MS_Cohort", "Results are per 1,000 policies sold.", FMT_NUM, False),
         ("Issue age", m["issue_age"], "MS_IssueAge", "Most Med Supp is bought at 65 during open enrollment.", FMT_NUM, False),
         ("Annual premium per policy, year 1", m["annual_premium_year1"], "MS_Prem1", "About $165/month. Humana 2026 Med Supp premiums for 65-year-olds range roughly $129–$524/month by plan and state (MoneyGeek).", FMT_USD, True),
         ("Premium step per year of age", m["premium_age_step"], "MS_PremAge", "Attained-age rating: premium rises as the insured ages.", FMT_PCT, False),
         ("Annual rate increase (trend-driven)", m["annual_rate_increase"], "MS_RateInc", "Filed rate increases to keep pace with claim trend. Humana filed rate changes effective 9/1/2026 (e.g., up to 25% on some NJ plans).", FMT_PCT, True),
         ("Annual claim cost per policy, year 1 (ultimate)", m["annual_claim_cost_year1_ultimate"], "MS_Clm1", "Expected cost once underwriting selection has worn off.", FMT_USD, True),
         ("Claim cost step per year of age", m["claim_age_slope"], "MS_ClmAge", "Claims rise faster with age than premium does.", FMT_PCT, False),
         ("Claim cost trend", m["claim_trend"], "MS_Trend", "Unit cost and utilization growth. Humana described medical cost trend as 'high single digit' in Q2 2026 remarks (for its overall book).", FMT_PCT, True),
         ("Selection factor, year 1", m["selection_factors"][0], "MS_Sel1", "Underwritten applicants are healthier at first; claims start below ultimate.", FMT_F4, False),
         ("Selection factor, year 2", m["selection_factors"][1], "MS_Sel2", "", FMT_F4, False),
         ("Selection factor, year 3+", m["selection_factors"][2], "MS_Sel3", "", FMT_F4, False),
         ("Lapse rate, year 1", m["lapse_by_duration"][0], "MS_Lapse1", "Industry: Gen Re survey shows lapse incl. mortality ~11% for open Med Supp blocks (2024).", FMT_PCT, True),
         ("Lapse rate, year 2", m["lapse_by_duration"][1], "MS_Lapse2", "", FMT_PCT, True),
         ("Lapse rate, year 3+", m["lapse_by_duration"][2], "MS_Lapse3", "", FMT_PCT, True),
         ("Extra 'shock' lapse when rates increase (yr 2+)", m["shock_lapse_add_from_year2"], "MS_Shock", "Applied in month 1 of each renewal year, when the anniversary rate increase hits. Healthier members re-shop (anti-selective lapse).", FMT_PCT, False),
         ("Mortality at 65", m["mortality_q65"], "MS_Q65", "Simple Gompertz curve: q(age) = q65 × e^(growth × (age − 65)).", FMT_F4, False),
         ("Mortality growth per year of age", m["mortality_growth"], "MS_MortG", "", FMT_F4, False),
         ("First-year commission (% of premium)", m["first_year_commission"], "MS_FYComm", "Front-loaded commissions are why year 1 loses money. Illustrative.", FMT_PCT, False),
         ("Renewal commission (% of premium)", m["renewal_commission"], "MS_RenComm", "Illustrative.", FMT_PCT, False),
         ("Premium tax", m["premium_tax"], "MS_PremTax", "State premium tax, ~2%. Illustrative.", FMT_PCT, False),
         ("Acquisition cost per policy issued", m["acquisition_cost_per_policy"], "MS_Acq", "Underwriting, issue, marketing. Illustrative.", FMT_USD, False),
         ("Maintenance cost per policy per year", m["maintenance_cost_per_policy"], "MS_Maint", "Billing, service, claims admin. Illustrative.", FMT_USD, False),
         ("Dynamic shock lapse: reference rate increase", m["dynamic_shock_lapse"]["reference_rate_increase"], "MS_ShockRef", "Rate increase at which the shock lapse equals its base value (the filed assumption).", FMT_PCT, False),
         ("Dynamic shock lapse: extra lapse per unit of rate increase", m["dynamic_shock_lapse"]["shock_lapse_per_unit_rate_increase"], "MS_ShockSlope", "Shock = MAX(0, base shock + slope × (rate increase − reference)). 0.5 = +0.5 pt lapse per +1 pt of increase. Judgment.", FMT_F4, False),
         ("Anti-selection: shopper share at issue", m["antiselection"]["shopper_share_at_issue"], "MS_ShopShare", "Share of issued lives healthy enough to re-underwrite elsewhere. Judgment; calibrate from post-rate-increase claims A/E.", FMT_PCT, True),
         ("Anti-selection: shopper relative claim cost", m["antiselection"]["shopper_relative_claim_cost"], "MS_ShopCost", "Shoppers' cost relative to the issue-mix average. Judgment — the model's largest unvalidated assumption (see Sensitivity).", FMT_F4, True),
         ("Anti-selection: shopper shock-lapse propensity", m["antiselection"]["shopper_shock_lapse_propensity"], "MS_ShopProp", "A shopper is this many times as likely as a non-shopper to take the shock lapse. Block lapse still equals the study. Judgment.", FMT_F4, True),
         ("Shopper share used (formula)", "=IF(F_AntiSel=1,MS_ShopShare,1)", "MS_ShareEff", "1 (one class) when the feature is off.", FMT_PCT, False),
         ("Shopper propensity used (formula)", "=IF(F_AntiSel=1,MS_ShopProp,1)", "MS_PropEff", "", FMT_F4, False),
         ("Shopper claim relativity used (formula)", "=IF(F_AntiSel=1,MS_ShopCost,1)", "MS_RelS", "", FMT_F4, False),
         ("Non-shopper claim relativity (formula)", "=IF(F_AntiSel=1,(1-MS_ShopShare*MS_ShopCost)/(1-MS_ShopShare),1)", "MS_RelN", "Set so the issue-mix average relativity is exactly 1.0.", FMT_F4, False),
         ("Required capital, % of annualized paid claims", m["required_capital_pct_of_annual_claims"], "MS_RC", "Health RBC-style proxy held from issue and released as claims run off. Illustrative; in production: Humana's capital allocation.", FMT_PCT, False)]
rows += [(f"Claim payment pattern, share paid at lag {j} months", v, f"MS_Pay{j}", "From the chain-ladder completion study (SQL 03). Must sum to 100%." if j == 0 else "", FMT_PCT, False) for j, v in enumerate(m["claim_payment_pattern"])]
d = A["DENTAL_VISION"]
rows += [("DENTAL & VISION (individual)", None, None, None, None, False),
         ("Projection years", d["projection_years"], "DV_Years", "Short-duration product.", FMT_NUM, False),
         ("Policies issued (cohort size)", d["cohort_size"], "DV_Cohort", "", FMT_NUM, False),
         ("Annual premium per policy, year 1", d["annual_premium_year1"], "DV_Prem1", "$39/month. Humana vision coverage starts around $12/month for vision-only.", FMT_USD, True),
         ("Annual rate increase", d["annual_rate_increase"], "DV_RateInc", "", FMT_PCT, False),
         ("Annual claim cost per policy (ultimate)", d["annual_claim_cost_year1_ultimate"], "DV_Clm1", "", FMT_USD, True),
         ("Claim cost trend", d["claim_trend"], "DV_Trend", "", FMT_PCT, False),
         ("Year-1 pent-up demand factor", d["first_year_pent_up_factor"], "DV_PentUp", "New members catch up on delayed dental work in year 1 (anti-selection).", FMT_F4, True),
         ("Lapse rate, year 1", d["lapse_by_duration"][0], "DV_Lapse1", "Buy, use, lapse: early lapse is high.", FMT_PCT, True),
         ("Lapse rate, year 2", d["lapse_by_duration"][1], "DV_Lapse2", "", FMT_PCT, True),
         ("Lapse rate, year 3+", d["lapse_by_duration"][2], "DV_Lapse3", "", FMT_PCT, True),
         ("First-year commission", d["first_year_commission"], "DV_FYComm", "", FMT_PCT, False),
         ("Renewal commission", d["renewal_commission"], "DV_RenComm", "", FMT_PCT, False),
         ("Premium tax", d["premium_tax"], "DV_PremTax", "", FMT_PCT, False),
         ("Acquisition cost per policy", d["acquisition_cost_per_policy"], "DV_Acq", "", FMT_USD, False),
         ("Maintenance cost per policy per year", d["maintenance_cost_per_policy"], "DV_Maint", "", FMT_USD, False),
         ("Required capital, % of annualized paid claims", d["required_capital_pct_of_annual_claims"], "DV_RC", "Health RBC-style proxy held from issue and released as claims run off. Illustrative; in production: Humana's capital allocation.", FMT_PCT, False)]
rows += [(f"Claim payment pattern, share paid at lag {j} months", v, f"DV_Pay{j}", "From the chain-ladder completion study (SQL 03). Must sum to 100%." if j == 0 else "", FMT_PCT, False) for j, v in enumerate(d["claim_payment_pattern"])]

bc_ = d.get("benefit_categories")
if bc_:
    rows += [("D&V BENEFIT CATEGORIES (v4; used when F_DVCats = 1)", None, None, None, None, False)]
    for ci, cn in enumerate(bc_["categories"], start=1):
        rows += [(f"{cn}: share of ultimate claim cost", bc_["cost_share"][ci - 1], f"DV_CatS{ci}", "Paid claims by service category in the experience. Shares sum to 1." if ci == 1 else "", FMT_PCT, False),
                 (f"{cn}: trend add-on vs line trend", bc_["trend_add_on"][ci - 1], f"DV_CatTA{ci}", "0: four years cannot separate category trends." if ci == 1 else "", FMT_PCT, False),
                 (f"{cn}: year-1 pent-up relativity (raw)", bc_["pent_up_relativity"][ci - 1], f"DV_CatPU{ci}", "Normalized below so the blended year-1 factor = DV_PentUp." if ci == 1 else "", FMT_F4, False),
                 (f"{cn}: waiting period (months)", bc_["waiting_period_months"][ci - 1], f"DV_CatW{ci}", "No benefit in policy months before this (product design)." if ci == 1 else "", FMT_NUM, False)]
    _pu = "+".join(f"DV_CatS{c}*DV_CatPU{c}" for c in range(1, len(bc_["categories"]) + 1))
    for ci, cn in enumerate(bc_["categories"], start=1):
        rows += [(f"{cn}: pent-up relativity used (formula)", f"=DV_CatPU{ci}/({_pu})", f"DV_CatPR{ci}", "Share-weighted average = 1." if ci == 1 else "", FMT_F4, False)]
c = A["MEDICAID"]
rows += [("MEDICAID (new-state managed care contract)", None, None, None, None, False),
         ("Projection years", c["projection_years"], "MD_Years", "", FMT_NUM, False),
         ("Initial contract term (years)", c["contract_term_years"], "MD_Term", "After the term the state re-procures. Humana's Florida contract was extended through Jan 2035.", FMT_NUM, False),
         ("Probability contract is renewed", c["renewal_probability"], "MD_RenewProb", "Expected-value weight on years after the initial term. Judgment.", FMT_PCT, True),
         ("Starting members", c["starting_members"], "MD_Start", "Humana Medicaid launches Georgia and Texas in 2027 (Q4 2025 release).", FMT_NUM, False),
         ("Annual disenrollment rate", c["annual_disenrollment"], "MD_Disenroll", "Churn from redeterminations and eligibility changes.", FMT_PCT, True),
         ("New members per year", c["annual_new_members"], "MD_NewMembers", "Auto-assignment and choice enrollment; joiners arrive evenly through the year and churn from the next month.", FMT_NUM, False),
         ("Capitation PMPM, year 1 (blended rate cells)", c["capitation_pmpm_year1"], "MD_Cap1", "Blend of child, adult and aged/blind/disabled rate cells. Illustrative.", FMT_USD2, True),
         ("Annual capitation rate update", c["annual_rate_update"], "MD_RateUpd", "State-certified actuarially sound rate changes.", FMT_PCT, True),
         ("Medical cost ratio, year 1 (before acuity load)", c["medical_cost_ratio_year1"], "MD_MCR1", "Expected medical cost as % of year-1 capitation.", FMT_PCT, True),
         ("New-state acuity load, year 1", c["new_state_acuity_load_year1"], "MD_Acuity", "First-year members often have unmet needs; claims run higher.", FMT_PCT, False),
         ("Medical cost trend", c["claim_trend"], "MD_Trend", "", FMT_PCT, True),
         ("Minimum MLR floor (remittance if below)", c["mlr_floor"], "MD_MLRFloor", "Federal Medicaid managed care rules use an 85% MLR standard; many states require remittance below a floor.", FMT_PCT, False),
         ("Premium tax / assessment", c["premium_tax"], "MD_PremTax", "", FMT_PCT, False),
         ("Admin cost PMPM, year 1", c["admin_pmpm_year1"], "MD_Admin1", "", FMT_USD2, False),
         ("Start-up cost (paid before go-live)", c["startup_cost_year1"], "MD_Startup", "Implementation, network build, systems for a new state. Paid at time 0.", FMT_USD, False)]
rc_ = c["rate_cells"]
rows += [("MEDICAID RATE CELLS (raw inputs; normalized below so the blended year-1 values equal the contract-level assumptions)", None, None, None, None, False)]
for ci, cn in enumerate(rc_["cells"], start=1):
    rows += [(f"{cn}: member share", rc_["member_share"][ci - 1], f"MD_Sh{ci}", "Starting and joiner mix. From the experience book; in production the state data book." if ci == 1 else "", FMT_PCT, False),
             (f"{cn}: capitation relativity", rc_["capitation_relativity"][ci - 1], f"MD_CapR{ci}", "Cell capitation ÷ blended. From the experience book (SQL 07 assumption indications)." if ci == 1 else "", FMT_F4, False),
             (f"{cn}: loss-ratio relativity", rc_["loss_ratio_relativity"][ci - 1], f"MD_MedR{ci}", "Cell cost = cell capitation × blended cost ratio × this (a loss-ratio, not cost, relativity). 1.0: no difference beyond 2 SE in the experience." if ci == 1 else "", FMT_F4, False),
             (f"{cn}: disenrollment relativity", rc_["disenrollment_relativity"][ci - 1], f"MD_ChR{ci}", "MACPAC 12-month churn by eligibility group (children 19%, adults 28%, disabled 9%), normalized to the blended rate." if ci == 1 else "", FMT_F4, False)]
SS = "(MD_Sh1+MD_Sh2+MD_Sh3)"
for ci, cn in enumerate(rc_["cells"], start=1):
    rows += [(f"{cn}: share used (formula)", f"=IF(F_RateCells=1,MD_Sh{ci}/{SS},{1 if ci == 1 else 0})", f"MD_S{ci}", "Off: one blended cell." if ci == 1 else "", FMT_PCT, False)]
for ci, cn in enumerate(rc_["cells"], start=1):
    rows += [(f"{cn}: capitation relativity used (formula)", f"=IF(F_RateCells=1,MD_CapR{ci}/(MD_S1*MD_CapR1+MD_S2*MD_CapR2+MD_S3*MD_CapR3),1)", f"MD_CR{ci}", "Normalized: share-weighted average = 1." if ci == 1 else "", FMT_F4, False)]
for ci, cn in enumerate(rc_["cells"], start=1):
    rows += [(f"{cn}: loss-ratio relativity used (formula)", f"=IF(F_RateCells=1,MD_MedR{ci}/(MD_S1*MD_CR1*MD_MedR1+MD_S2*MD_CR2*MD_MedR2+MD_S3*MD_CR3*MD_MedR3),1)", f"MD_MR{ci}", "Normalized: blended year-1 cost ratio = MD_MCR1." if ci == 1 else "", FMT_F4, False)]
for ci, cn in enumerate(rc_["cells"], start=1):
    rows += [(f"{cn}: churn relativity used (formula)", f"=IF(F_RateCells=1,MD_ChR{ci}/(MD_S1*MD_ChR1+MD_S2*MD_ChR2+MD_S3*MD_ChR3),1)", f"MD_QR{ci}", "Normalized: blended year-1 churn = MD_Disenroll." if ci == 1 else "", FMT_F4, False)]
ra_ = c["risk_adjustment"]
rows += [("MEDICAID RISK ADJUSTMENT (budget neutral)", None, None, None, None, False),
         ("Plan acuity vs state average", ra_["plan_acuity"], "MD_Acu", "1.0 = average. Medical cost scales with acuity. Tornado tests ±3%.", FMT_F4, True),
         ("Share of acuity difference paid by risk adjustment", ra_["revenue_capture"], "MD_RACap", "Capitation moves by capture × (acuity − 1). Judgment.", FMT_PCT, False),
         ("Risk-adjusted revenue factor (formula)", "=IF(F_RateCells=1,1+MD_RACap*(MD_Acu-1),1)", "MD_RARev", "", FMT_F4, False),
         ("Medical acuity factor (formula)", "=IF(F_RateCells=1,MD_Acu,1)", "MD_RAMed", "", FMT_F4, False),
         ("Required capital, % of annualized paid claims", c["required_capital_pct_of_annual_claims"], "MD_RC", "Health RBC-style proxy held from issue and released as claims run off. Illustrative; in production: Humana's capital allocation.", FMT_PCT, False)]
rows += [(f"Claim payment pattern, share paid at lag {j} months", v, f"MD_Pay{j}", "From the chain-ladder completion study (SQL 03). Must sum to 100%." if j == 0 else "", FMT_PCT, False) for j, v in enumerate(c["claim_payment_pattern"])]

ct_ = c.get("contract_terms")
if ct_:
    co_ = ct_["mlr_rule"]["corridor"]
    rows += [("MEDICAID CONTRACT TERMS (v4; used when F_MDTerms = 1; from the state contract)", None, None, None, None, False),
             ("Quality withhold, % of capitation", ct_["quality_withhold"]["withhold_pct"], "MD_WH", "Held back by the state; earned back after the year on quality results.", FMT_PCT, True),
             ("Expected share of withhold earned back", ct_["quality_withhold"]["expected_earn_back"], "MD_WHEarn", "Received six months after the contract year, with the MLR settlement.", FMT_PCT, False),
             ("Directed payments PMPM, year 1", ct_["directed_payments"]["pmpm_year1"], "MD_DP1", "State-directed provider payments passed through: in revenue and in medical, and in both sides of the MLR.", FMT_USD2, False),
             ("Directed payments annual growth", ct_["directed_payments"]["annual_trend"], "MD_DPTrend", "", FMT_PCT, False),
             ("Risk adjustment lag on the acuity path (years)", ct_["acuity_shift"]["risk_adjustment_lag_years"], "MD_RALag", "Risk scores catch up with an acuity shift only after this many years.", FMT_NUM, False),
             ("MLR rule: 0 = floor, 1 = risk corridor", 0 if ct_["mlr_rule"]["type"] == "floor" else 1, "MD_MLRRule", "Floor: remit MAX(0, floor x revenue - medical). Corridor: symmetric bands around the target MLR.", "0", True),
             ("Corridor target MLR", co_["target_mlr"], "MD_CorT", "", FMT_PCT, False),
             ("Corridor band 1 (plan keeps 100% within +/-)", co_["band_1"], "MD_CorB1", "", FMT_PCT, False),
             ("Corridor band 2", co_["band_2"], "MD_CorB2", "", FMT_PCT, False),
             ("Plan share between band 1 and band 2", co_["plan_share_band_2"], "MD_CorS2", "", FMT_PCT, False),
             ("Plan share beyond band 2", co_["plan_share_beyond"], "MD_CorS3", "", FMT_PCT, False)]
    _acu = list(ct_["acuity_shift"]["medical_factor_by_year"]) + [1.0] * c["projection_years"]
    rows += [(f"Acuity path: medical factor, contract year {y}", _acu[y - 1], f"MD_AcuF{y}", "Medical cost multiplier by contract year (e.g. post-redetermination acuity shift); 1.0 = none." if y == 1 else "", FMT_F4, False) for y in range(1, c["projection_years"] + 1)]
    rows += [(f"Acuity factor used, year {y} (formula)", f"=IF(F_MDTerms=1,MD_AcuF{y},1)", f"MD_AcuU{y}", "", FMT_F4, False) for y in range(1, c["projection_years"] + 1)]
    rows += [(f"Risk-adjustment catch-up factor, year {y} (formula)", f"=IF(AND(F_MDTerms=1,{y}-MD_RALag>=1),1+MD_RACap*(INDEX(MD_AcuPath,{y}-MD_RALag)-1),1)", f"MD_RAP{y}", "Revenue recognizes capture x (acuity path - 1) after the lag." if y == 1 else "", FMT_F4, False) for y in range(1, c["projection_years"] + 1)]
    rows += [("Withhold used (formula)", "=IF(F_MDTerms=1,MD_WH,0)", "MD_WHe", "", FMT_PCT, False),
             ("Withhold earn-back used (formula)", "=IF(F_MDTerms=1,MD_WHEarn,0)", "MD_WHEe", "", FMT_PCT, False),
             ("Directed payments PMPM used (formula)", "=IF(F_MDTerms=1,MD_DP1,0)", "MD_DPe", "", FMT_USD2, False),
             ("MLR rule used (formula)", "=IF(F_MDTerms=1,MD_MLRRule,0)", "MD_Rulee", "", "0", False)]
# Scenario levers are not in the JSON: they exist only in the workbook for what-if testing.
# Multipliers default to 1.0 and add-ons to 0.0, so the base case is unaffected; the Dashboard
# checks they are neutral before the Python tie-out is read as valid.
rows += [("SCENARIO LEVERS (set to 1.0 / 0.0 for the base case)", None, None, None, None, False),
         ("Med Supp lapse multiplier", 1.0, "MS_LapseMult", "Try 0.8 or 1.2 to see the effect of persistency on value.", FMT_F4, True),
         ("Med Supp claim trend add-on", 0.0, "MS_TrendAdd", "Try +0.01 for one extra point of trend every year.", FMT_PCT, True),
         ("Med Supp rate increase add-on", 0.0, "MS_RateAdd", "Try −0.01 if regulators approve less than filed.", FMT_PCT, True),
         ("D&V lapse multiplier", 1.0, "DV_LapseMult", "", FMT_F4, True),
         ("Medicaid rate update add-on", 0.0, "MD_RateAdd", "", FMT_PCT, True),
         ("Medicaid trend add-on", 0.0, "MD_TrendAdd", "", FMT_PCT, True)]

# v4.1: every input's note states its basis from the assumption register (config/assumption_basis.json,
# docs/ASSUMPTION_BASIS.md), so the workbook and the register can never disagree. Formula rows and
# scenario levers keep their descriptive notes.
_BASIS = {e["key"]: e for e in json.loads((ROOT / "config" / "assumption_basis.json").read_text())["entries"]}
_STATUS = {"P": "Public source", "S": "Statute / regulation", "D": "Derived for consistency", "J": "Judgment",
           "E": "Experience", "M": "Model setting"}
_N2K = {"DiscRate": "global.discount_rate", "ExpInfl": "global.expense_inflation",
        "PT_Tax": "global.profit_test.federal_income_tax_rate", "PT_Earn": "global.profit_test.investment_earned_rate",
        "MS_DAC": "global.profit_test.dac_tax_rate_by_line*", "DV_DAC": "global.profit_test.dac_tax_rate_by_line*",
        "MS_ShopShare": "MEDSUPP.antiselection.shopper_share_at_issue", "MS_ShopCost": "MEDSUPP.antiselection.shopper_relative_claim_cost",
        "MS_ShopProp": "MEDSUPP.antiselection.shopper_shock_lapse_propensity",
        "MS_ShockRef": "MEDSUPP.dynamic_shock_lapse.reference_rate_increase",
        "MS_ShockSlope": "MEDSUPP.dynamic_shock_lapse.shock_lapse_per_unit_rate_increase",
        "MD_WH": "MEDICAID.contract_terms.quality_withhold.withhold_pct",
        "MD_WHEarn": "MEDICAID.contract_terms.quality_withhold.expected_earn_back",
        "MD_DP1": "MEDICAID.contract_terms*", "MD_MLRRule": "MEDICAID.contract_terms*", "MD_RALag": "MEDICAID.contract_terms*",
        "MD_Acu": "MEDICAID.risk_adjustment.plan_acuity", "MD_RACap": "MEDICAID.risk_adjustment.revenue_capture",
        "DV_CatS1": "DENTAL_VISION.benefit_categories*", "DV_CatW1": "DENTAL_VISION.benefit_categories*"}
_PFX = {"MS": "MEDSUPP", "DV": "DENTAL_VISION", "MD": "MEDICAID"}
_FIELD = {"Years": "projection_years", "Cohort": "cohort_size", "IssueAge": "issue_age", "Prem1": "annual_premium_year1",
          "PremAge": "premium_age_step", "RateInc": "annual_rate_increase", "Clm1": "annual_claim_cost_year1_ultimate",
          "ClmAge": "claim_age_slope", "Trend": "claim_trend", "Sel1": "selection_factors", "Lapse1": "lapse_by_duration",
          "Shock": "shock_lapse_add_from_year2", "Q65": "mortality_q65", "MortG": "mortality_growth",
          "FYComm": "first_year_commission", "RenComm": "renewal_commission", "PremTax": "premium_tax",
          "Acq": "acquisition_cost_per_policy", "Maint": "maintenance_cost_per_policy", "RC": "required_capital_pct_of_annual_claims",
          "Pay0": "claim_payment_pattern", "PentUp": "first_year_pent_up_factor", "Term": "contract_term_years",
          "RenewProb": "renewal_probability", "Start": "starting_members", "Disenroll": "annual_disenrollment",
          "NewMembers": "annual_new_members", "Cap1": "capitation_pmpm_year1", "RateUpd": "annual_rate_update",
          "MCR1": "medical_cost_ratio_year1", "Acuity": "new_state_acuity_load_year1", "MLRFloor": "mlr_floor",
          "Admin1": "admin_pmpm_year1", "Startup": "startup_cost_year1", "Sh1": "rate_cells.member_share",
          "CapR1": "rate_cells.capitation_relativity", "MedR1": "rate_cells.loss_ratio_relativity",
          "ChR1": "rate_cells.disenrollment_relativity"}


def _basis_note(nm, note):
    key = _N2K.get(nm)
    if key is None and nm and "_" in nm:
        pf, fld = nm.split("_", 1)
        if pf in _PFX and fld in _FIELD:
            key = f"{_PFX[pf]}.{_FIELD[fld]}"
    e = _BASIS.get(key) if key else None
    if e is None:
        return note
    rng = f" Range: {e['sensitivity']}." if e.get("sensitivity") else ""
    lab = _STATUS.get(e["status"], e["status"])
    b_ = e["basis"]
    if b_.lower().startswith(lab.lower().split()[0]):           # basis already names its status
        return f"{b_}{rng}"
    return f"{lab}: {b_}{rng}"


_unmatched = [nm for _l, v_, nm, _n, _f, _k in rows
              if v_ is not None and not (isinstance(v_, str) and v_.startswith("=")) and nm
              and not nm.endswith("Mult") and not nm.endswith("Add") and _basis_note(nm, None) is None
              and not nm.startswith("F_") and not re.match(r"(MS|DV|MD)_(Sel|Lapse|Pay)\d", nm)
              and not re.match(r"(DV_Cat(S|TA|PU|W)|MD_(Sh|CapR|MedR|ChR|AcuF)|MD_Cor(T|B1|B2|S2|S3)|MD_DPTrend)\d*$", nm)]
assert not _unmatched, f"workbook inputs without an assumption-basis note: {_unmatched}"
rows = [(l_, v_, nm, (_basis_note(nm, n_) if v_ is not None and not (isinstance(v_, str) and str(v_).startswith("=")) else n_), f_, k_)
        for l_, v_, nm, n_, f_, k_ in rows]

# Write rows starting below the header; register each named range as an absolute reference.
_ROWOF = {}
r = 5
for label, val, nm, note, fmt, key in rows:
    if val is None:
        cl = wa.cell(r, 1, label); cl.font = f_sec
        r += 1; continue
    wa.cell(r, 1, label).font = f_base
    v = wa.cell(r, 2, val); v.number_format = fmt; v.border = box
    if isinstance(val, str) and val.startswith("="):
        v.font = f_base                      # derived value: black formula, no input fill
    else:
        v.font = f_input; v.fill = fill_key if key else fill_input
    wa.cell(r, 3, nm).font = Font(name=F, size=9, color=GREY)
    nc = wa.cell(r, 4, note); nc.font = Font(name=F, size=9); nc.alignment = Alignment(wrap_text=True, vertical="top")
    name(nm, f"Assumptions!$B${r}")
    _ROWOF[nm] = r
    r += 1
wa.freeze_panes = "A5"
# v4 path ranges (contiguous rows) for INDEX lookups by contract year.
if "MD_AcuF1" in _ROWOF:
    _Y = A["MEDICAID"]["projection_years"]
    name("MD_AcuPath", f"Assumptions!$B${_ROWOF['MD_AcuF1']}:$B${_ROWOF[f'MD_AcuF{_Y}']}")
    name("MD_AcuUsePath", f"Assumptions!$B${_ROWOF['MD_AcuU1']}:$B${_ROWOF[f'MD_AcuU{_Y}']}")
    name("MD_RAPath", f"Assumptions!$B${_ROWOF['MD_RAP1']}:$B${_ROWOF[f'MD_RAP{_Y}']}")


# =============================================================================
# MONTHLY ENGINE SHEETS + ANNUAL SUMMARY SHEETS
# Each line has a monthly engine (all cash-flow mechanics) and an annual sheet that only aggregates
# it with SUMIFS on policy / contract year and adds items that are annual by nature (acquisition or
# start-up at issue, Medicaid MLR remittance and renewal weighting) plus the results block.
# =============================================================================
M0 = 5          # first data row on monthly sheets (row 4 = headers)
R0 = 17         # first data row on annual sheets (row 16 = headers)


# Both sheet builders take a column specification list of (header, formula_fn, number_format, width).
# formula_fn(r, k) returns the cell content for worksheet row r and month index k (or policy year y
# on annual sheets). Defining a sheet as a list of column formulas keeps the spreadsheet logic
# readable in one place, in the same left-to-right order as the calculation.
def monthly_sheet(name_, heading, sub, cols, K):
    ws = wb.create_sheet(name_)
    title(ws, heading, sub)
    header_row(ws, M0 - 1, 1, [c[0] for c in cols])
    for j, (_, _, fmt, w) in enumerate(cols, start=1):
        ws.column_dimensions[L(j)].width = w
    for k in range(K):
        r = M0 + k
        for j, (_, f, fmt, _) in enumerate(cols, start=1):
            c = ws.cell(r, j, f(r, k))
            c.font = f_base; c.number_format = fmt
    ws.freeze_panes = ws.cell(M0, 4)
    return ws


def annual_sheet(name_, heading, sub, cols, Y):
    ws = wb.create_sheet(name_)
    title(ws, heading, sub)
    header_row(ws, R0 - 1, 1, [c[0] for c in cols])
    for j, (_, _, fmt, w) in enumerate(cols, start=1):
        ws.column_dimensions[L(j)].width = w
    for y in range(1, Y + 1):
        r = R0 + y - 1
        for j, (_, f, fmt, _) in enumerate(cols, start=1):
            c = ws.cell(r, j, f(r, y))
            c.font = f_base; c.number_format = fmt; c.border = box
            if y % 2 == 0: c.fill = fill_band
    ws.freeze_panes = ws.cell(R0, 2)
    return ws


# Results block in rows 5+ of each annual sheet: label merged over A:C, value merged over D:E.
# Downstream sheets reference these fixed cells (D5 = NPV, D6 = per unit, D9 = PV loss ratio,
# D10 = margin, D11 = break-even year), so the item order below must not change.
def summary_block(ws, items, top=4, col=1):
    ws.cell(top, col, "Results").font = f_sec
    for k, (lab, f, fmt) in enumerate(items):
        rr = top + 1 + k
        a = ws.cell(rr, col, lab); a.font = f_bold; a.border = box; a.fill = fill_sum
        ws.merge_cells(start_row=rr, start_column=col, end_row=rr, end_column=col + 2)
        b = ws.cell(rr, col + 3, f); b.font = f_bold; b.number_format = fmt; b.border = box; b.fill = fill_sum
        ws.merge_cells(start_row=rr, start_column=col + 3, end_row=rr, end_column=col + 4)


def notes(ws, lines_):
    ws["G5"] = "Reading the results"; ws["G5"].font = f_sec
    for k, t in enumerate(lines_):
        ws.cell(6 + k, 7, "• " + t).font = f_base


# Absolute range covering the K data rows of one column on a monthly sheet (used in SUMIFS).
def rng(sheet, col, K):
    return f"{sheet}!${col}${M0}:${col}${M0 + K - 1}"


# ---------------------------------------------------------------- Med Supp
# Monthly engine, one row per month k = 0 .. 12*years - 1.
#   Decrements. Mortality is a Gompertz curve on attained age, capped at 0.35 annually, converted
#   to a monthly rate. Annual lapse is selected by policy duration (1, 2, 3+) times the lapse lever.
#   Anniversary shock lapse: in month 1 of each renewal year (year 2+) the filed rate increase
#   takes effect and extra lapse MS_Shock is added to the year's total, G = base + shock. The
#   month-1 rate is set so that month 1 followed by eleven months at the base monthly rate gives
#   exactly the year's total survival:  1 - (1 - G) / (1 - F)^(11/12).
#   Timing. Deaths occur mid-month on average; lapses take effect at month end on the survivors
#   (in force - deaths), since the member has paid for the month. Exposure = in force - 1/2 deaths.
#   Amounts. Premium is attained-age rated and grows with the filed rate increase each policy year;
#   claim cost grows with age slope and trend and is scaled by the select factor (years 1, 2, 3+).
#   Cash flows. Premium is billed on start-of-month in force (in advance); claims and maintenance
#   use exposure. Commission and premium tax are a percentage of premium.
#   Discounting. Premium-related items at v^(k/12); claims and maintenance at v^((k + 0.5)/12).
#   Acquisition cost is not in the monthly rows: it is paid at issue and deducted on the annual sheet.
MSY = A["MEDSUPP"]["projection_years"]; MSK = 12 * MSY
# v3 columns: the block is split into shoppers (K) and non-shoppers (L). Both take the base monthly
# rate (J); the block's extra (shock) lapse is split so a shopper is MS_ShopProp times as likely as a
# non-shopper to take it, so the block's monthly lapse still equals I (the annual study) while the
# shopper rate stays below 100% (true in every scenario here; the engine warns otherwise). With
# F_AntiSel = 0 there is one class at relativity 1: the version 2 sheet.
# Dynamic shock (G): MAX(0, MS_Shock + MS_ShockSlope x (rate increase - MS_ShockRef)) when F_DynLapse = 1.
MS_SHOCK_EFF = "IF(F_DynLapse=1,MAX(0,MS_Shock+MS_ShockSlope*(MS_RateInc+MS_RateAdd-MS_ShockRef)),MS_Shock)"
# Extra (shock) lapse rate for a non-shopper this month: block extra lapses / (propensity x shoppers
# + non-shoppers); a shopper's extra rate is propensity times this. Survivors of death = K - M, L - N.
MS_X = lambda r: f"IF(MS_PropEff*(K{r}-M{r})+(L{r}-N{r})=0,0,(K{r}-M{r}+L{r}-N{r})*(I{r}-J{r})/(MS_PropEff*(K{r}-M{r})+(L{r}-N{r})))"
ms_m_cols = [
    ("Month index k", lambda r, k: k, "0", 8),
    ("Policy year", lambda r, k: f"=INT(A{r}/12)+1", "0", 7),
    ("Month of policy year", lambda r, k: f"=MOD(A{r},12)+1", "0", 8),
    ("Attained age", lambda r, k: f"=MS_IssueAge+B{r}-1", "0", 8),
    ("Monthly death rate", lambda r, k: f"=1-(1-MIN(MS_Q65*EXP(MS_MortG*(D{r}-65)),0.35))^(1/12)", "0.0000%", 10),
    ("Base annual lapse", lambda r, k: f"=MIN(CHOOSE(MIN(B{r},3),MS_Lapse1,MS_Lapse2,MS_Lapse3)*MS_LapseMult,1)", FMT_PCT2, 9),
    ("Shock lapse this year (dynamic)", lambda r, k: f"=IF(B{r}>=2,{MS_SHOCK_EFF},0)", FMT_PCT2, 9),
    ("Year's total lapse (base + shock)", lambda r, k: f"=MIN(F{r}+G{r},1)", FMT_PCT2, 10),
    ("Block monthly lapse (shock in month 1)", lambda r, k: f"=IF(C{r}=1,1-(1-H{r})/(1-F{r})^(11/12),1-(1-F{r})^(1/12))", "0.0000%", 10),
    ("Base monthly lapse", lambda r, k: f"=1-(1-F{r})^(1/12)", "0.0000%", 10),
    ("Shoppers in force, start", lambda r, k: "=MS_Cohort*MS_ShareEff" if k == 0 else f"=K{r-1}-M{r-1}-P{r-1}", FMT_NUM1, 10),
    ("Non-shoppers in force, start", lambda r, k: "=MS_Cohort*(1-MS_ShareEff)" if k == 0 else f"=L{r-1}-N{r-1}-O{r-1}", FMT_NUM1, 10),
    ("Shopper deaths", lambda r, k: f"=K{r}*E{r}", "0.000", 8),
    ("Non-shopper deaths", lambda r, k: f"=L{r}*E{r}", "0.000", 8),
    ("Non-shopper lapses (base + x)", lambda r, k: f"=(L{r}-N{r})*(J{r}+{MS_X(r)})", "0.000", 9),
    ("Shopper lapses (base + propensity × x)", lambda r, k: f"=(K{r}-M{r})*MIN(J{r}+MS_PropEff*{MS_X(r)},1)", "0.000", 9),
    ("In force, start of month", lambda r, k: f"=K{r}+L{r}", FMT_NUM1, 10),
    ("Deaths", lambda r, k: f"=M{r}+N{r}", "0.000", 8),
    ("Lapses (end of month)", lambda r, k: f"=O{r}+P{r}", "0.000", 9),
    ("Exposure (in force − ½ deaths)", lambda r, k: f"=Q{r}-0.5*R{r}", FMT_NUM1, 10),
    ("Annual premium per policy", lambda r, k: f"=MS_Prem1*(1+MS_PremAge)^(B{r}-1)*(1+MS_RateInc+MS_RateAdd)^(B{r}-1)", FMT_USD, 10),
    ("Annual claim cost per policy (issue-mix average)", lambda r, k: f"=MS_Clm1*(1+MS_ClmAge)^(B{r}-1)*(1+MS_Trend+MS_TrendAdd)^(B{r}-1)*CHOOSE(MIN(B{r},3),MS_Sel1,MS_Sel2,MS_Sel3)", FMT_USD, 10),
    ("Premium (in advance)", lambda r, k: f"=Q{r}*U{r}/12", FMT_USD, 11),
    ("Claims (class-weighted)", lambda r, k: f"=((K{r}-0.5*M{r})*MS_RelS+(L{r}-0.5*N{r})*MS_RelN)*V{r}/12", FMT_USD, 11),
    ("Commission", lambda r, k: f"=W{r}*IF(B{r}=1,MS_FYComm,MS_RenComm)", FMT_USD, 10),
    ("Premium tax", lambda r, k: f"=W{r}*MS_PremTax", FMT_USD, 9),
    ("Maintenance", lambda r, k: f"=T{r}*MS_Maint*(1+ExpInfl)^(B{r}-1)/12", FMT_USD, 9),
    ("Discount, start of month", lambda r, k: f"=(1+DiscRate)^(-A{r}/12)", FMT_F4, 9),
    ("Discount, mid-month", lambda r, k: f"=(1+DiscRate)^(-(A{r}+0.5)/12)", FMT_F4, 9),
    ("PV of month's cash flow", lambda r, k: f"=(W{r}-Y{r}-Z{r})*AB{r}-(X{r}+AA{r})*AC{r}", FMT_USD, 11),
    ("PV of premium", lambda r, k: f"=W{r}*AB{r}", FMT_USD, 11),
    ("PV of claims", lambda r, k: f"=X{r}*AC{r}", FMT_USD, 11),
    ("Claim relativity of in-force block", lambda r, k: f"=IF(T{r}=0,0,X{r}/(T{r}*V{r}/12))", FMT_F4, 9),
]
wmm = monthly_sheet("MedSupp_Monthly", "Medicare Supplement — monthly engine",
                    "One row per month for 20 years (240 rows). Read left to right: decrements by health class → in force → amounts → cash flows → discounting.", ms_m_cols, MSK)
# Annual view: each policy year is a SUMIFS of its twelve months on column B (policy year).
MSm = lambda c: rng("MedSupp_Monthly", c, MSK)
msl = M0 + MSK - 1
ms_a_cols = [
    ("Year", lambda r, y: y, "0", 7),
    ("In force, start", lambda r, y: f"=SUMIFS({MSm('Q')},{MSm('B')},A{r},{MSm('C')},1)", FMT_NUM1, 11),
    ("In force, end", lambda r, y: f"=B{r+1}" if y < MSY else f"=MedSupp_Monthly!Q{msl}-MedSupp_Monthly!R{msl}-MedSupp_Monthly!S{msl}", FMT_NUM1, 11),
    ("Exposure (policy-years)", lambda r, y: f"=SUMIFS({MSm('T')},{MSm('B')},A{r})/12", FMT_NUM1, 11),
    ("Premium", lambda r, y: f"=SUMIFS({MSm('W')},{MSm('B')},A{r})", FMT_USD, 13),
    ("Claims", lambda r, y: f"=SUMIFS({MSm('X')},{MSm('B')},A{r})", FMT_USD, 13),
    ("Commission", lambda r, y: f"=SUMIFS({MSm('Y')},{MSm('B')},A{r})", FMT_USD, 12),
    ("Premium tax", lambda r, y: f"=SUMIFS({MSm('Z')},{MSm('B')},A{r})", FMT_USD, 11),
    ("Maintenance", lambda r, y: f"=SUMIFS({MSm('AA')},{MSm('B')},A{r})", FMT_USD, 11),
    ("Acquisition (paid at issue)", lambda r, y: f"=IF(A{r}=1,MS_Cohort*MS_Acq,0)", FMT_USD, 12),
    ("Profit (cash)", lambda r, y: f"=E{r}-F{r}-G{r}-H{r}-I{r}-J{r}", FMT_USD, 13),
    ("PV of profit", lambda r, y: f"=SUMIFS({MSm('AD')},{MSm('B')},A{r})-J{r}", FMT_USD, 13),
    ("Cumulative PV profit", lambda r, y: f"=L{r}" if y == 1 else f"=M{r-1}+L{r}", FMT_USD, 13),
    ("Loss ratio (year)", lambda r, y: f"=IF(E{r}=0,0,F{r}/E{r})", FMT_PCT, 9),
    ("PV of premium", lambda r, y: f"=SUMIFS({MSm('AE')},{MSm('B')},A{r})", FMT_USD, 13),
    ("PV of claims", lambda r, y: f"=SUMIFS({MSm('AF')},{MSm('B')},A{r})", FMT_USD, 13),
    ("Break-even helper", lambda r, y: f"=IF(M{r}>0,A{r},999)", "0", 9),
    ("Shoppers, share of in force (start)", lambda r, y: f"=SUMIFS({MSm('K')},{MSm('B')},A{r},{MSm('C')},1)/B{r}", FMT_PCT, 10),
    ("Claim relativity (month 1)", lambda r, y: f"=SUMIFS({MSm('AG')},{MSm('B')},A{r},{MSm('C')},1)", FMT_F4, 10),
]
wm = annual_sheet("MedSupp_NPV", "Medicare Supplement — cohort NPV (annual view)",
                  "Per 1,000 Plan G policies issued at age 65. Each year sums its 12 months from MedSupp_Monthly.", ms_a_cols, MSY)
last = R0 + MSY - 1
summary_block(wm, [
    ("NPV per 1,000 policies", f"=SUM(L{R0}:L{last})", FMT_USD),
    ("NPV per policy issued", "=D5/MS_Cohort", FMT_USD2),
    ("PV of premium", f"=SUM(O{R0}:O{last})", FMT_USD),
    ("PV of claims", f"=SUM(P{R0}:P{last})", FMT_USD),
    ("PV loss ratio (lifetime)", "=D8/D7", FMT_PCT2),
    ("NPV margin (NPV ÷ PV premium)", "=D5/D7", FMT_PCT2),
    ("Break-even year (cumulative PV > 0)", f'=IF(MIN(Q{R0}:Q{last})=999,"Not within horizon",MIN(Q{R0}:Q{last}))', "0"),
    ("Policies still in force after 10 years", f"=C{R0+9}", FMT_NUM1),
    ("DE NPV (after tax, after cost of capital)", "=MedSupp_Profit!$C$2", FMT_USD),
    ("DE margin (DE NPV ÷ PV premium)", "=D13/D7", FMT_PCT2),
])
notes(wm, ["Year 1 loses money: first-year commission and acquisition cost exceed the first-year margin.",
           "Value is earned in later years, so it depends on persistency and on rate increases keeping pace with claims.",
           "Anti-selective shock lapse: shoppers leave at each rate increase, so the claim relativity of the block rises (columns R–S).",
           "Lifetime PV loss ratio must also satisfy regulatory minimums (65% individual / 75% group, NAIC).",
           "See Sensitivity for which assumptions move NPV the most."])

# ---------------------------------------------------------------- Dental & Vision
# Same structure as Med Supp with simpler decrements: no mortality (short-duration product), so
# exposure equals start-of-month in force; lapse is by duration, capped at 95% annually, with no
# anniversary shock. Year-1 claims carry the pent-up demand factor (new members catching up on
# deferred dental care). Premium and claim costs are not age-rated.
DVY = A["DENTAL_VISION"]["projection_years"]; DVK = 12 * DVY
# v4 benefit categories: sum over categories of share x (1 + trend + add-on)^(year - 1) x year-1
# pent-up (line factor x normalized relativity) x (0 during the category's waiting period).
# Column A is the month index k (months since issue), B the policy year.
DV_NCAT = len(A["DENTAL_VISION"].get("benefit_categories", {}).get("categories", []))
def DV_CATSUM(r, trend, pent, year_col="B", k_col="A"):
    # v4.1: pent-up applies to the category's first 12 months of eligibility (deferred by the waiting period).
    return "+".join(f"DV_CatS{c}*(1+{trend}+DV_CatTA{c})^({year_col}{r}-1)*IF(AND({k_col}{r}>=DV_CatW{c},{k_col}{r}<DV_CatW{c}+12),({pent})*DV_CatPR{c},1)*IF({k_col}{r}>=DV_CatW{c},1,0)"
                    for c in range(1, DV_NCAT + 1))
dv_m_cols = [
    ("Month index k", lambda r, k: k, "0", 8),
    ("Policy year", lambda r, k: f"=INT(A{r}/12)+1", "0", 7),
    ("Month of policy year", lambda r, k: f"=MOD(A{r},12)+1", "0", 8),
    ("Annual lapse", lambda r, k: f"=MIN(CHOOSE(MIN(B{r},3),DV_Lapse1,DV_Lapse2,DV_Lapse3)*DV_LapseMult,0.95)", FMT_PCT2, 9),
    ("Monthly lapse", lambda r, k: f"=1-(1-D{r})^(1/12)", "0.0000%", 10),
    ("In force, start of month", lambda r, k: "=DV_Cohort" if k == 0 else f"=F{r-1}-G{r-1}", FMT_NUM1, 10),
    ("Lapses (end of month)", lambda r, k: f"=F{r}*E{r}", "0.000", 9),
    ("Exposure", lambda r, k: f"=F{r}", FMT_NUM1, 9),
    ("Annual premium per policy", lambda r, k: f"=DV_Prem1*(1+DV_RateInc)^(B{r}-1)", FMT_USD, 10),
    ("Annual claim cost per policy", lambda r, k: f"=IF(F_DVCats=1,DV_Clm1*({DV_CATSUM(r, 'DV_Trend', 'DV_PentUp')}),DV_Clm1*(1+DV_Trend)^(B{r}-1)*IF(B{r}=1,DV_PentUp,1))" if DV_NCAT else f"=DV_Clm1*(1+DV_Trend)^(B{r}-1)*IF(B{r}=1,DV_PentUp,1)", FMT_USD, 10),
    ("Premium (in advance)", lambda r, k: f"=F{r}*I{r}/12", FMT_USD, 10),
    ("Claims", lambda r, k: f"=H{r}*J{r}/12", FMT_USD, 10),
    ("Commission", lambda r, k: f"=K{r}*IF(B{r}=1,DV_FYComm,DV_RenComm)", FMT_USD, 10),
    ("Premium tax", lambda r, k: f"=K{r}*DV_PremTax", FMT_USD, 9),
    ("Maintenance", lambda r, k: f"=H{r}*DV_Maint*(1+ExpInfl)^(B{r}-1)/12", FMT_USD, 9),
    ("Discount, start of month", lambda r, k: f"=(1+DiscRate)^(-A{r}/12)", FMT_F4, 9),
    ("Discount, mid-month", lambda r, k: f"=(1+DiscRate)^(-(A{r}+0.5)/12)", FMT_F4, 9),
    ("PV of month's cash flow", lambda r, k: f"=(K{r}-M{r}-N{r})*P{r}-(L{r}+O{r})*Q{r}", FMT_USD, 11),
    ("PV of premium", lambda r, k: f"=K{r}*P{r}", FMT_USD, 10),
    ("PV of claims", lambda r, k: f"=L{r}*Q{r}", FMT_USD, 10),
]
wdm = monthly_sheet("DentalVision_Monthly", "Dental & Vision — monthly engine", "One row per month for 10 years (120 rows).", dv_m_cols, DVK)
DVm = lambda c: rng("DentalVision_Monthly", c, DVK)
dvl = M0 + DVK - 1
dv_a_cols = [
    ("Year", lambda r, y: y, "0", 7),
    ("In force, start", lambda r, y: f"=SUMIFS({DVm('F')},{DVm('B')},A{r},{DVm('C')},1)", FMT_NUM1, 11),
    ("In force, end", lambda r, y: f"=B{r+1}" if y < DVY else f"=DentalVision_Monthly!F{dvl}-DentalVision_Monthly!G{dvl}", FMT_NUM1, 11),
    ("Exposure (policy-years)", lambda r, y: f"=SUMIFS({DVm('H')},{DVm('B')},A{r})/12", FMT_NUM1, 11),
    ("Premium", lambda r, y: f"=SUMIFS({DVm('K')},{DVm('B')},A{r})", FMT_USD, 12),
    ("Claims", lambda r, y: f"=SUMIFS({DVm('L')},{DVm('B')},A{r})", FMT_USD, 12),
    ("Commission", lambda r, y: f"=SUMIFS({DVm('M')},{DVm('B')},A{r})", FMT_USD, 11),
    ("Premium tax", lambda r, y: f"=SUMIFS({DVm('N')},{DVm('B')},A{r})", FMT_USD, 11),
    ("Maintenance", lambda r, y: f"=SUMIFS({DVm('O')},{DVm('B')},A{r})", FMT_USD, 11),
    ("Acquisition (paid at issue)", lambda r, y: f"=IF(A{r}=1,DV_Cohort*DV_Acq,0)", FMT_USD, 11),
    ("Profit (cash)", lambda r, y: f"=E{r}-F{r}-G{r}-H{r}-I{r}-J{r}", FMT_USD, 12),
    ("PV of profit", lambda r, y: f"=SUMIFS({DVm('R')},{DVm('B')},A{r})-J{r}", FMT_USD, 12),
    ("Cumulative PV profit", lambda r, y: f"=L{r}" if y == 1 else f"=M{r-1}+L{r}", FMT_USD, 12),
    ("Loss ratio (year)", lambda r, y: f"=IF(E{r}=0,0,F{r}/E{r})", FMT_PCT, 9),
    ("PV of premium", lambda r, y: f"=SUMIFS({DVm('S')},{DVm('B')},A{r})", FMT_USD, 12),
    ("PV of claims", lambda r, y: f"=SUMIFS({DVm('T')},{DVm('B')},A{r})", FMT_USD, 12),
    ("Break-even helper", lambda r, y: f"=IF(M{r}>0,A{r},999)", "0", 9),
]
wd = annual_sheet("DentalVision_NPV", "Dental & Vision — cohort NPV (annual view)",
                  "Per 1,000 individual policies. Each year sums its 12 months from DentalVision_Monthly.", dv_a_cols, DVY)
dl = R0 + DVY - 1
summary_block(wd, [
    ("NPV per 1,000 policies", f"=SUM(L{R0}:L{dl})", FMT_USD),
    ("NPV per policy issued", "=D5/DV_Cohort", FMT_USD2),
    ("PV of premium", f"=SUM(O{R0}:O{dl})", FMT_USD),
    ("PV of claims", f"=SUM(P{R0}:P{dl})", FMT_USD),
    ("PV loss ratio (lifetime)", "=D8/D7", FMT_PCT2),
    ("NPV margin (NPV ÷ PV premium)", "=D5/D7", FMT_PCT2),
    ("Break-even year (cumulative PV > 0)", f'=IF(MIN(Q{R0}:Q{dl})=999,"Not within horizon",MIN(Q{R0}:Q{dl}))', "0"),
    ("Policies still in force after 3 years", f"=C{R0+2}", FMT_NUM1),
    ("DE NPV (after tax, after cost of capital)", "=DentalVision_Profit!$C$2", FMT_USD),
    ("DE margin (DE NPV ÷ PV premium)", "=D13/D7", FMT_PCT2),
])
notes(wd, ["Year 1 loses money: pent-up claims plus first-year commission and acquisition.",
           "Thin margin: value depends on keeping members past year 1 and on waiting periods.",
           "Biggest swing factors: the claim-cost level (all years, through trend) and the year-1-only pent-up factor (see Sensitivity)."])

# ---------------------------------------------------------------- Medicaid
# Medicaid is an open block, not a closed cohort: members leave through monthly churn
# (redeterminations, eligibility loss) and new members join at MD_NewMembers / 12 per month.
# Joiners are added to next month's start count, so they churn from the following month.
# Member months = start - 1/2 leavers + 1/2 joiners (both assumed to move mid-month).
# Capitation PMPM grows with the state rate update; medical PMPM is MCR x capitation in year 1,
# loaded for new-state acuity in year 1 only, then grows with medical trend. Capitation is
# received and costs paid through the month, so all operating items are discounted mid-month.
MDY = A["MEDICAID"]["projection_years"]; MDK = 12 * MDY
MD_TERMS = bool(A["MEDICAID"].get("contract_terms"))
# Settlement to the state on MLR base X and medical F (row r): floor or symmetric risk corridor.
def MD_SETTLE(X, F_):
    m_ = f"({F_}/{X})"
    gain = f"((1-MD_CorS2)*MIN(MAX(MD_CorT-MD_CorB1-{m_},0),MD_CorB2-MD_CorB1)+(1-MD_CorS3)*MAX(MD_CorT-MD_CorB2-{m_},0))"
    loss = f"((1-MD_CorS2)*MIN(MAX({m_}-MD_CorT-MD_CorB1,0),MD_CorB2-MD_CorB1)+(1-MD_CorS3)*MAX({m_}-MD_CorT-MD_CorB2,0))"
    return f"IF(MD_Rulee=1,{X}*({gain}-{loss}),MAX(0,MD_MLRFloor*{X}-{F_}))"
CELLS = A["MEDICAID"]["rate_cells"]["cells"]
# v3 columns: membership is tracked per rate cell (E:G) with its own churn (I:K) and the joiner mix.
# Revenue uses capitation-relativity-weighted member months (N) and medical uses cost-weighted member
# months (O), each times the blended PMPM and the risk-adjustment factor. With F_RateCells = 0 the
# first cell holds everyone at relativity 1, which is the version 2 sheet.
MDQ = lambda c: f"(1-(1-MIN(MD_Disenroll*MD_QR{c},1))^(1/12))"
MDMM = lambda r, c, col, lcol: f"({col}{r}-0.5*{lcol}{r}+0.5*L{r}*MD_S{c})"
md_m_cols = [
    ("Month index k", lambda r, k: k, "0", 8),
    ("Contract year", lambda r, k: f"=INT(A{r}/12)+1", "0", 7),
    ("Month of year", lambda r, k: f"=MOD(A{r},12)+1", "0", 7),
    ("Members, start of month", lambda r, k: f"=E{r}+F{r}+G{r}", FMT_NUM1, 11),
    (f"Members {CELLS[0]}", lambda r, k: "=MD_Start*MD_S1" if k == 0 else f"=E{r-1}-I{r-1}+MD_NewMembers/12*MD_S1", FMT_NUM1, 10),
    (f"Members {CELLS[1]}", lambda r, k: "=MD_Start*MD_S2" if k == 0 else f"=F{r-1}-J{r-1}+MD_NewMembers/12*MD_S2", FMT_NUM1, 10),
    (f"Members {CELLS[2]}", lambda r, k: "=MD_Start*MD_S3" if k == 0 else f"=G{r-1}-K{r-1}+MD_NewMembers/12*MD_S3", FMT_NUM1, 10),
    ("Leavers (monthly churn)", lambda r, k: f"=I{r}+J{r}+K{r}", FMT_NUM1, 10),
    (f"Leavers {CELLS[0]}", lambda r, k: f"=E{r}*{MDQ(1)}", FMT_NUM1, 9),
    (f"Leavers {CELLS[1]}", lambda r, k: f"=F{r}*{MDQ(2)}", FMT_NUM1, 9),
    (f"Leavers {CELLS[2]}", lambda r, k: f"=G{r}*{MDQ(3)}", FMT_NUM1, 9),
    ("Joiners", lambda r, k: "=MD_NewMembers/12", FMT_NUM1, 9),
    ("Member months", lambda r, k: f"=D{r}-0.5*H{r}+0.5*L{r}", FMT_NUM1, 10),
    ("Capitation-weighted member months", lambda r, k: "=" + "+".join(f"{MDMM(r, c, col, lc)}*MD_CR{c}" for c, col, lc in [(1, "E", "I"), (2, "F", "J"), (3, "G", "K")]), FMT_NUM1, 11),
    ("Cost-weighted member months", lambda r, k: "=" + "+".join(f"{MDMM(r, c, col, lc)}*MD_CR{c}*MD_MR{c}" for c, col, lc in [(1, "E", "I"), (2, "F", "J"), (3, "G", "K")]), FMT_NUM1, 11),
    ("Capitation PMPM (blended)", lambda r, k: f"=MD_Cap1*(1+MD_RateUpd+MD_RateAdd)^(B{r}-1)", FMT_USD2, 10),
    ("Medical PMPM (blended)", lambda r, k: f"=MD_Cap1*MD_MCR1*(1+MD_Trend+MD_TrendAdd)^(B{r}-1)*IF(B{r}=1,1+MD_Acuity,1)", FMT_USD2, 10),
    ("Revenue (received)", lambda r, k: f"=Z{r}*(1-MD_WHe)+AA{r}" if MD_TERMS else f"=N{r}*P{r}*MD_RARev", FMT_USD, 12),
    ("Medical costs", lambda r, k: f"=O{r}*Q{r}*MD_RAMed*INDEX(MD_AcuUsePath,B{r})+AA{r}" if MD_TERMS else f"=O{r}*Q{r}*MD_RAMed", FMT_USD, 12),
    ("Premium tax", lambda r, k: f"=(Z{r}+AA{r})*MD_PremTax" if MD_TERMS else f"=R{r}*MD_PremTax", FMT_USD, 10),
    ("Admin", lambda r, k: f"=M{r}*MD_Admin1*(1+ExpInfl)^(B{r}-1)", FMT_USD, 10),
    ("Discount, mid-month", lambda r, k: f"=(1+DiscRate)^(-(A{r}+0.5)/12)", FMT_F4, 9),
    ("PV of operating cash flow", lambda r, k: f"=(R{r}-S{r}-T{r}-U{r})*V{r}", FMT_USD, 12),
    ("PV of revenue", lambda r, k: f"=R{r}*V{r}", FMT_USD, 12),
    ("PV of medical", lambda r, k: f"=S{r}*V{r}", FMT_USD, 12),
] + ([
    # v4 contract terms (columns appended at the right so no v3 reference moves).
    ("Capitation, gross of withhold (v4)", lambda r, k: f"=N{r}*P{r}*MD_RARev*INDEX(MD_RAPath,B{r})", FMT_USD, 12),
    ("Directed payments, pass-through (v4)", lambda r, k: f"=M{r}*MD_DPe*(1+MD_DPTrend)^(B{r}-1)", FMT_USD, 12),
] if MD_TERMS else [])
wmdm = monthly_sheet("Medicaid_Monthly", "Medicaid — monthly engine",
                     "One row per month for 10 years, by rate cell. New members join through the year in the joiner mix and churn from the following month.", md_m_cols, MDK)
# Annual contract-year view. Additional mechanics beyond the cohort sheets:
#   MLR remittance per contract year = MAX(0, MD_MLRFloor x revenue - medical), paid at y + 0.5.
#   Renewal: years within the initial term are in force with probability 1; later years are
#   weighted by MD_RenewProb. Start-up cost is incurred before go-live regardless of renewal.
MDm = lambda c: rng("Medicaid_Monthly", c, MDK)
mdl = M0 + MDK - 1
md_a_cols = [
    ("Year", lambda r, y: y, "0", 7),
    ("Members, start", lambda r, y: f"=SUMIFS({MDm('D')},{MDm('B')},A{r},{MDm('C')},1)", FMT_NUM, 11),
    ("Members, end", lambda r, y: f"=B{r+1}" if y < MDY else f"=Medicaid_Monthly!D{mdl}-Medicaid_Monthly!H{mdl}+Medicaid_Monthly!L{mdl}", FMT_NUM, 11),
    ("Member months", lambda r, y: f"=SUMIFS({MDm('M')},{MDm('B')},A{r})", FMT_NUM, 12),
    ("Revenue", lambda r, y: f"=SUMIFS({MDm('R')},{MDm('B')},A{r})", FMT_USD, 14),
    ("Medical costs", lambda r, y: f"=SUMIFS({MDm('S')},{MDm('B')},A{r})", FMT_USD, 14),
    ("Net settlement to state (MLR / corridor less withhold earned)" if MD_TERMS else "MLR remittance", lambda r, y: f"=AB{r}-Z{r}" if MD_TERMS else f"=MAX(0,MD_MLRFloor*E{r}-F{r})", FMT_USD, 12),
    ("Premium tax", lambda r, y: f"=SUMIFS({MDm('T')},{MDm('B')},A{r})", FMT_USD, 12),
    ("Admin", lambda r, y: f"=SUMIFS({MDm('U')},{MDm('B')},A{r})", FMT_USD, 12),
    ("Start-up (paid before go-live)", lambda r, y: f"=IF(A{r}=1,MD_Startup,0)", FMT_USD, 12),
    ("Profit (if contract in force)", lambda r, y: f"=E{r}-F{r}-G{r}-H{r}-I{r}-J{r}", FMT_USD, 14),
    ("Probability in force", lambda r, y: f"=IF(A{r}<=MD_Term,1,MD_RenewProb)", FMT_PCT, 10),
    ("Expected profit", lambda r, y: f"=(K{r}+J{r})*L{r}-J{r}", FMT_USD, 14),
    ("PV of expected profit", lambda r, y: f"=L{r}*(SUMIFS({MDm('W')},{MDm('B')},A{r})-G{r}*(1+DiscRate)^(-(A{r}+0.5)))-J{r}", FMT_USD, 14),
    ("Cumulative PV profit", lambda r, y: f"=N{r}" if y == 1 else f"=O{r-1}+N{r}", FMT_USD, 14),
    ("Medical loss ratio", lambda r, y: f"=IF(E{r}=0,0,F{r}/E{r})", FMT_PCT, 9),
    ("Break-even helper", lambda r, y: f"=IF(O{r}>0,A{r},999)", "0", 9),
    ("PV of revenue (prob-weighted)", lambda r, y: f"=L{r}*SUMIFS({MDm('X')},{MDm('B')},A{r})", FMT_USD, 14),
    ("PV of medical (prob-weighted)", lambda r, y: f"=L{r}*SUMIFS({MDm('Y')},{MDm('B')},A{r})", FMT_USD, 14),
    ("Capitation PMPM", lambda r, y: f"=E{r}/D{r}", FMT_USD2, 10),
    ("Medical PMPM", lambda r, y: f"=F{r}/D{r}", FMT_USD2, 10),
    (f"Members {CELLS[0]} (start)", lambda r, y: f"=SUMIFS({MDm('E')},{MDm('B')},A{r},{MDm('C')},1)", FMT_NUM, 10),
    (f"Members {CELLS[1]} (start)", lambda r, y: f"=SUMIFS({MDm('F')},{MDm('B')},A{r},{MDm('C')},1)", FMT_NUM, 10),
    (f"Members {CELLS[2]} (start)", lambda r, y: f"=SUMIFS({MDm('G')},{MDm('B')},A{r},{MDm('C')},1)", FMT_NUM, 10),
] + ([
    ("Gross capitation (v4)", lambda r, y: f"=SUMIFS({MDm('Z')},{MDm('B')},A{r})", FMT_USD, 14),
    ("Withhold earned back (v4, paid +6 months)", lambda r, y: f"=MD_WHe*MD_WHEe*Y{r}", FMT_USD, 12),
    ("MLR revenue base (v4)", lambda r, y: f"=E{r}+Z{r}", FMT_USD, 14),
    ("MLR floor / corridor amount to state (v4)", lambda r, y: "=" + MD_SETTLE(f"AA{r}", f"F{r}"), FMT_USD, 12),
] if MD_TERMS else [])
wmd = annual_sheet("Medicaid_NPV", "Medicaid — new-state contract NPV (annual view)",
                   "10,000 starting members. Each year sums its 12 months from Medicaid_Monthly; the MLR remittance is settled on the year and paid six months later.", md_a_cols, MDY)
ml = R0 + MDY - 1
summary_block(wmd, [
    ("Contract NPV", f"=SUM(N{R0}:N{ml})", FMT_USD),
    ("NPV per starting member", "=D5/MD_Start", FMT_USD2),
    ("PV of revenue (probability-weighted)", f"=SUM(R{R0}:R{ml})", FMT_USD),
    ("PV of medical costs (probability-weighted)", f"=SUM(S{R0}:S{ml})", FMT_USD),
    ("PV medical loss ratio", "=D8/D7", FMT_PCT2),
    ("NPV margin (NPV ÷ PV revenue)", "=D5/D7", FMT_PCT2),
    ("Break-even year (cumulative PV > 0)", f'=IF(MIN(Q{R0}:Q{ml})=999,"Not within horizon",MIN(Q{R0}:Q{ml}))', "0"),
    ("Years with a settlement paid to the state" if MD_TERMS else "Years where MLR floor binds", f'=COUNTIF({"AB" if MD_TERMS else "G"}{R0}:{"AB" if MD_TERMS else "G"}{ml},">0")', "0"),
    ("DE NPV (after tax, after cost of capital)", "=Medicaid_Profit!$C$2", FMT_USD),
    ("DE margin (DE NPV ÷ PV revenue)", "=D13/D7", FMT_PCT2),
])
notes(wmd, ["Year 1 loses money: start-up cost (paid before go-live) plus higher first-year acuity.",
            "Membership settles near 2,800 joiners ÷ monthly churn, about 9,850 members.",
            "Margin depends on the gap between rate updates and medical trend (biggest swing factor).",
            "The 85% MLR floor caps upside: if claims run low, the gain is remitted to the state."])

# =============================================================================
# PROFIT TEST SHEETS: cash flows -> after-tax distributable earnings (DE)
# =============================================================================
# One row per month for the projection plus runoff (claims incurred late are paid after the last
# projection month; the last Medicaid remittance is paid six months after the final year).
#   Paid claims    = sum over lags j of pattern_j x incurred claims j months earlier
#   Claim reserve  = last month's reserve + incurred - paid
#   Required cap.  = capital % x 12 x paid claims (tapers with the claim runoff)
#   Book profit    = start cash x (1+j) + (mid cash - paid) x (1+j)^0.5 - increase in reserve
#                    - remittance accrued + j x (reserve + MLR liability last month)
#   Tax            = tax rate x (book profit + j x capital last month)
#   DE             = book profit + j x capital last month - tax - increase in required capital
#   DE NPV         = time-0 cost x (1 - tax rate) + sum of DE x v^(k+1)
# Medicaid flows are weighted by the probability the contract is in force in the year they are
# incurred, so claims incurred in year 5 are paid even if the contract is not renewed.
PT_RUNOFF = 9
def pt_paid(prefix, r, k, inc_col):
    return "=" + "+".join(f"{prefix}_Pay{j}*{inc_col}{r - j}" for j in range(0, min(k, 8) + 1))


def profit_annual(ws, H, Y, col0, de_col, pv_col, paid_col, bp_col, tax_col, rc_col, t0):
    """Annual roll-up to the right of the monthly profit rows (year Y+1 = runoff)."""
    c = [L(col0 + j) for j in range(6)]
    header_row(ws, M0 - 1, col0, ["Year", "Paid claims", "Book profit (pre-tax)", "Federal tax", "Distributable earnings", "PV of DE"])
    yr = f"$B${M0}:$B${M0 + H - 1}"
    for y in range(1, Y + 2):
        r = M0 + y - 1
        ws[f"{c[0]}{r}"] = y
        ws[f"{c[1]}{r}"] = f"=SUMIFS(${paid_col}${M0}:${paid_col}${M0 + H - 1},{yr},{c[0]}{r})"
        ws[f"{c[2]}{r}"] = f"=SUMIFS(${bp_col}${M0}:${bp_col}${M0 + H - 1},{yr},{c[0]}{r})"
        ws[f"{c[3]}{r}"] = f"=SUMIFS(${tax_col}${M0}:${tax_col}${M0 + H - 1},{yr},{c[0]}{r})"
        ws[f"{c[4]}{r}"] = f"=SUMIFS(${de_col}${M0}:${de_col}${M0 + H - 1},{yr},{c[0]}{r})" + (f"+{t0}" if y == 1 else "")
        ws[f"{c[5]}{r}"] = f"=SUMIFS(${pv_col}${M0}:${pv_col}${M0 + H - 1},{yr},{c[0]}{r})" + (f"+{t0}" if y == 1 else "")
        for j in range(6):
            cc = ws[f"{c[j]}{r}"]; cc.border = box; cc.font = f_base
            cc.number_format = "0" if j == 0 else FMT_USD
        ws.column_dimensions[c[j]].width = 14
    ws.cell(2, 1, "DE NPV (after tax, after cost of capital)").font = f_bold
    v = ws.cell(2, 3, f"=SUM({c[5]}{M0}:{c[5]}{M0 + Y})"); v.font = f_bold; v.number_format = FMT_USD; v.fill = fill_sum; v.border = box
    return f"{c[5]}"


def profit_cohort(name_, heading, prefix, src, K, Y, start_cash, mid_cash, inc, t0, prem_col):
    H = K + PT_RUNOFF
    cols = [
        ("Month index k", lambda r, k: k, "0", 8),
        ("Year (runoff = year after projection)", lambda r, k: f"=INT(A{r}/12)+1", "0", 8),
        ("Start-of-month cash (premium − commission − tax)", lambda r, k: start_cash(r) if k < K else 0, FMT_USD, 12),
        ("Mid-month cash excl. claims (− maintenance)", lambda r, k: mid_cash(r) if k < K else 0, FMT_USD, 12),
        ("Incurred claims", lambda r, k: inc(r) if k < K else 0, FMT_USD, 12),
        ("Paid claims (lag pattern)", lambda r, k: pt_paid(prefix, r, k, "E"), FMT_USD, 12),
        ("Claim reserve, end of month", lambda r, k: f"=E{r}-F{r}" if k == 0 else f"=G{r-1}+E{r}-F{r}", FMT_USD, 12),
        ("Required capital, end of month (on paid claims)", lambda r, k: f"={prefix}_RC*12*F{r}", FMT_USD, 12),
        ("Investment income on reserves", lambda r, k: 0 if k == 0 else f"=PT_J*G{r-1}", FMT_USD, 11),
        ("Investment income on capital", lambda r, k: 0 if k == 0 else f"=PT_J*H{r-1}", FMT_USD, 11),
        ("Book profit (pre-tax)", lambda r, k: f"=C{r}*(1+PT_J)+(D{r}-F{r})*(1+PT_J)^0.5-(G{r}-{0 if k == 0 else f'G{r-1}'})+I{r}", FMT_USD, 12),
        ("Federal income tax", lambda r, k: f"=PT_Tax*(K{r}+J{r})", FMT_USD, 11),
        ("Distributable earnings", lambda r, k: f"=K{r}+J{r}-L{r}-(H{r}-{0 if k == 0 else f'H{r-1}'})", FMT_USD, 12),
        ("Discount to month end", lambda r, k: f"=(1+DiscRate)^(-(A{r}+1)/12)", FMT_F4, 9),
        ("PV of DE", lambda r, k: f"=M{r}*N{r}", FMT_USD, 12),
    ]
    ws = monthly_sheet(name_, heading, "", cols, H)
    ws["A3"] = "Claims paid on the lag pattern, claim reserve, required capital, investment income and tax. Rows after the projection are claim runoff."
    ws["A3"].font = f_sub
    ws["A2"] = None
    profit_annual(ws, H, Y, 17, "M", "O", "F", "K", "L", "H", t0)
    dac_table(ws, prefix, src, K, Y, prem_col)
    return ws


def dac_table(ws, prefix, src, K, Y, prem_col):
    """IRC §848 DAC tax (v4.1): capitalized = rate x net premiums by tax (policy) year; amortized over
    180 months with the half-year convention (1/30, 1/15 x 14, 1/30); extra tax = tax rate x
    (capitalized - amortized), paid at the end of the year, discounted at the hurdle rate. The table
    runs 15 years past the projection; its PV is deducted in the DE NPV cell (C2)."""
    c0 = 25
    cols = [L(c0 + j) for j in range(6)]
    header_row(ws, M0 - 1, c0, ["Tax year", "Net premiums", "DAC capitalized", "DAC amortized", "Extra tax (DAC)", "PV of extra tax"])
    yr = f"{src}!$B${M0}:$B${M0 + K - 1}"
    prem = f"{src}!${prem_col}${M0}:${prem_col}${M0 + K - 1}"
    T_ = Y + 15
    W = ["/30"] + ["/15"] * 14 + ["/30"]
    for t in range(1, T_ + 1):
        r = M0 + t - 1
        ws[f"{cols[0]}{r}"] = t
        ws[f"{cols[1]}{r}"] = f"=SUMIFS({prem},{yr},{cols[0]}{r})" if t <= Y else 0
        ws[f"{cols[2]}{r}"] = f"={prefix}_DAC*{cols[1]}{r}"
        terms = [f"{cols[2]}{M0 + y - 1}{W[t - y]}" for y in range(max(1, t - 15), min(t, Y) + 1)]
        ws[f"{cols[3]}{r}"] = "=" + ("+".join(terms) if terms else "0")
        ws[f"{cols[4]}{r}"] = f"=PT_Tax*({cols[2]}{r}-{cols[3]}{r})"
        ws[f"{cols[5]}{r}"] = f"={cols[4]}{r}*(1+DiscRate)^(-{cols[0]}{r})"
        for j in range(6):
            cc = ws[f"{cols[j]}{r}"]; cc.border = box; cc.font = f_base; cc.number_format = "0" if j == 0 else FMT_USD
    for j in range(6):
        ws.column_dimensions[cols[j]].width = 14
    ws.cell(2, 5, "PV of DAC tax (deducted)").font = f_bold
    pv = ws.cell(2, 7, f"=SUM({cols[5]}{M0}:{cols[5]}{M0 + T_ - 1})"); pv.number_format = FMT_USD; pv.border = box
    ws["C2"] = ws["C2"].value + "-G2"


wpm = profit_cohort("MedSupp_Profit", "Medicare Supplement — profit test (after-tax distributable earnings)", "MS", "MedSupp_Monthly", MSK, MSY,
                    lambda r: f"=MedSupp_Monthly!W{r}-MedSupp_Monthly!Y{r}-MedSupp_Monthly!Z{r}",
                    lambda r: f"=-MedSupp_Monthly!AA{r}", lambda r: f"=MedSupp_Monthly!X{r}",
                    "-MS_Cohort*MS_Acq*(1-PT_Tax)", "W")
wpd = profit_cohort("DentalVision_Profit", "Dental & Vision — profit test (after-tax distributable earnings)", "DV", "DentalVision_Monthly", DVK, DVY,
                    lambda r: f"=DentalVision_Monthly!K{r}-DentalVision_Monthly!M{r}-DentalVision_Monthly!N{r}",
                    lambda r: f"=-DentalVision_Monthly!O{r}", lambda r: f"=DentalVision_Monthly!L{r}",
                    "-DV_Cohort*DV_Acq*(1-PT_Tax)", "K")

# Medicaid: probability weights from Medicaid_NPV column L, remittance from column G (rows 17..).
MDH = MDK + PT_RUNOFF
PROB = f"Medicaid_NPV!$L${R0}:$L${R0 + MDY - 1}"
REM = f"Medicaid_NPV!$G${R0}:$G${R0 + MDY - 1}"
md_p_cols = [
    ("Month index k", lambda r, k: k, "0", 8),
    ("Contract year (runoff = year 11)", lambda r, k: f"=INT(A{r}/12)+1", "0", 8),
    ("Probability in force (year incurred)", lambda r, k: f"=INDEX({PROB},B{r})" if k < MDK else 0, FMT_PCT, 9),
    ("Mid-month cash excl. medical (revenue − tax − admin), weighted", lambda r, k: f"=C{r}*(Medicaid_Monthly!R{r}-Medicaid_Monthly!T{r}-Medicaid_Monthly!U{r})" if k < MDK else 0, FMT_USD, 13),
    ("Incurred medical, weighted", lambda r, k: f"=C{r}*Medicaid_Monthly!S{r}" if k < MDK else 0, FMT_USD, 13),
    ("Paid medical (lag pattern)", lambda r, k: pt_paid("MD", r, k, "E"), FMT_USD, 13),
    ("Claim reserve, end of month", lambda r, k: f"=E{r}-F{r}" if k == 0 else f"=G{r-1}+E{r}-F{r}", FMT_USD, 13),
    ("MLR remittance accrued (year end), weighted", lambda r, k: f"=INDEX({REM},B{r})*INDEX({PROB},B{r})" if (k + 1) % 12 == 0 and k < MDK else 0, FMT_USD, 12),
    ("MLR remittance paid (+6 months), weighted", lambda r, k: f"=INDEX({REM},{(k - 5) // 12})*INDEX({PROB},{(k - 5) // 12})" if k >= 17 and (k - 5) % 12 == 0 and (k - 5) // 12 <= MDY else 0, FMT_USD, 12),
    ("MLR liability, end of month", lambda r, k: f"=H{r}-I{r}" if k == 0 else f"=J{r-1}+H{r}-I{r}", FMT_USD, 12),
    ("Required capital, end of month (on paid claims)", lambda r, k: f"=MD_RC*12*F{r}", FMT_USD, 13),
    ("Investment income on reserves + MLR liability", lambda r, k: 0 if k == 0 else f"=PT_J*(G{r-1}+J{r-1})", FMT_USD, 12),
    ("Investment income on capital", lambda r, k: 0 if k == 0 else f"=PT_J*K{r-1}", FMT_USD, 12),
    ("Book profit (pre-tax)", lambda r, k: f"=(D{r}-F{r})*(1+PT_J)^0.5-(G{r}-{0 if k == 0 else f'G{r-1}'})-H{r}+L{r}", FMT_USD, 13),
    ("Federal income tax", lambda r, k: f"=PT_Tax*(N{r}+M{r})", FMT_USD, 12),
    ("Distributable earnings", lambda r, k: f"=N{r}+M{r}-O{r}-(K{r}-{0 if k == 0 else f'K{r-1}'})", FMT_USD, 13),
    ("Discount to month end", lambda r, k: f"=(1+DiscRate)^(-(A{r}+1)/12)", FMT_F4, 9),
    ("PV of DE", lambda r, k: f"=P{r}*Q{r}", FMT_USD, 13),
]
wpmd = monthly_sheet("Medicaid_Profit", "Medicaid — profit test (after-tax distributable earnings)", "", md_p_cols, MDH)
wpmd["A3"] = "Probability-weighted by contract year incurred. MLR remittance accrued at year end and paid six months later. Rows after month 120 are runoff."
wpmd["A3"].font = f_sub
wpmd["A2"] = None
profit_annual(wpmd, MDH, MDY, 20, "P", "R", "F", "N", "O", "K", "-MD_Startup*(1-PT_Tax)")


# =============================================================================
# SENSITIVITY: Sens_Calc re-runs the monthly model per scenario (live formulas)
# =============================================================================
# Each scenario is a full monthly re-projection in its own block of columns, rather than an Excel
# data table or a re-run of the main sheets. Reasons: (1) every scenario NPV is an ordinary formula
# a reviewer can trace, with the shock visible in its own blue cells; (2) all 33 scenarios are
# live at once and recalculate without What-If tools; (3) each block can be reconciled to the
# matching Python sensitivity run; (4) the base block of each line must equal the main sheet,
# which tests the compact formulas against the full engine.
# Layout: row 1 block label, row 2 NPV, row 3 shock labels, row 4 shock values (blue), row 5 column
# headers, rows 6+ months. Columns A-E (month index, year, month, age, death rate) are shared.
# The blocks apply their own shock cells to the base named inputs and do not read the scenario
# levers on Assumptions, so sensitivities are always measured from the base assumption set.
wsc = wb.create_sheet("Sens_Calc")
title(wsc, "Sensitivity calculations — one monthly re-projection per scenario",
      "Each block has its own shock cells (blue, row 4). Shared columns A–E. Base blocks reproduce the main sheets exactly.")
SR0 = 6                                   # first month row
SC_K = MSK                                # 240 rows (D&V / Medicaid use the first 120)
header_row(wsc, 5, 1, ["k", "Year", "Month", "Age", "Monthly death rate"])
for k in range(SC_K):
    r = SR0 + k
    wsc.cell(r, 1, k)
    wsc.cell(r, 2, f"=INT(A{r}/12)+1"); wsc.cell(r, 3, f"=MOD(A{r},12)+1")
    wsc.cell(r, 4, f"=MS_IssueAge+B{r}-1")
    wsc.cell(r, 5, f"=1-(1-MIN(MS_Q65*EXP(MS_MortG*(D{r}-65)),0.35))^(1/12)")
for col in "ABCDE":
    wsc.column_dimensions[col].width = 8
col_ptr = 6
npv_ref = {}


# Writes the label, NPV caption, shock captions and blue shock values for a block starting at c0.
def block_header(c0, label, heads, vals, fmts, width):
    wsc.cell(1, c0, label).font = f_bold
    wsc.cell(2, c0, "NPV").font = f_bold
    for j, (h, v, fm) in enumerate(zip(heads, vals, fmts)):
        wsc.cell(3, c0 + j, h).font = Font(name=F, size=9, color=GREY)
        cc = wsc.cell(4, c0 + j, v); cc.font = f_input; cc.number_format = fm
    for j in range(width):
        wsc.column_dimensions[L(c0 + j)].width = 12


# Med Supp block. Shocks: lm lapse multiplier, ta trend add-on, ra rate-increase add-on,
# da discount-rate add-on, cm multiplier on the claim-cost level (carries into every later year
# through age slope and trend), sc shopper relative claim cost (anti-selection strength; the base
# block links to MS_ShopCost). Decrements replicate MedSupp_Monthly with two health classes and the
# dynamic anniversary shock lapse; the cash-flow algebra is collapsed into one PV column.
# Returns an absolute reference to the block's NPV cell.
def ms_scn(label, lm=1.0, ta=0.0, ra=0.0, da=0.0, cm=1.0, sc=None):
    global col_ptr
    c0 = col_ptr; C = [L(c0 + j) for j in range(7)]
    block_header(c0, "Med Supp — " + label, ["Lapse ×", "Trend +", "Rate inc +", "Disc rate +", "Claim ×", "Shopper cost"],
                 [lm, ta, ra, da, cm, "=MS_ShopCost" if sc is None else sc],
                 [FMT_F4, FMT_PCT, FMT_PCT, FMT_PCT, FMT_F4, FMT_F4], 7)
    if sc is None:
        wsc.cell(4, c0 + 5).font = f_link
    LM, TA, RA, DA, CM, SC = [f"${C[j]}$4" for j in range(6)]
    RELS = f"IF(F_AntiSel=1,{SC},1)"
    RELN = f"IF(F_AntiSel=1,(1-MS_ShopShare*{SC})/(1-MS_ShopShare),1)"
    SHOCK = f"IF(F_DynLapse=1,MAX(0,MS_Shock+MS_ShockSlope*(MS_RateInc+{RA}-MS_ShockRef)),MS_Shock)"
    for j, h in enumerate(["Base lapse", "Block monthly lapse", "Shoppers", "Non-shoppers", "Non-shopper lapses", "Shopper lapses", "PV cash"]):
        wsc.cell(5, c0 + j, h).font = f_bold
    for k in range(MSK):
        r = SR0 + k
        wsc[f"{C[0]}{r}"] = f"=MIN(CHOOSE(MIN($B{r},3),MS_Lapse1,MS_Lapse2,MS_Lapse3)*{LM},1)"
        wsc[f"{C[1]}{r}"] = f"=IF($C{r}=1,1-(1-MIN({C[0]}{r}+IF($B{r}>=2,{SHOCK},0),1))/(1-{C[0]}{r})^(11/12),1-(1-{C[0]}{r})^(1/12))"
        wsc[f"{C[2]}{r}"] = "=MS_Cohort*MS_ShareEff" if k == 0 else f"={C[2]}{r-1}-{C[2]}{r-1}*$E{r-1}-{C[5]}{r-1}"
        wsc[f"{C[3]}{r}"] = "=MS_Cohort*(1-MS_ShareEff)" if k == 0 else f"={C[3]}{r-1}-{C[3]}{r-1}*$E{r-1}-{C[4]}{r-1}"
        SS_, SN_ = f"({C[2]}{r}-{C[2]}{r}*$E{r})", f"({C[3]}{r}-{C[3]}{r}*$E{r})"
        BM_ = f"(1-(1-{C[0]}{r})^(1/12))"
        X_ = f"IF(MS_PropEff*{SS_}+{SN_}=0,0,({SS_}+{SN_})*({C[1]}{r}-{BM_})/(MS_PropEff*{SS_}+{SN_}))"
        wsc[f"{C[4]}{r}"] = f"={SN_}*({BM_}+{X_})"
        wsc[f"{C[5]}{r}"] = f"={SS_}*MIN({BM_}+MS_PropEff*{X_},1)"
        wsc[f"{C[6]}{r}"] = (f"=({C[2]}{r}+{C[3]}{r})*MS_Prem1*(1+MS_PremAge)^($B{r}-1)*(1+MS_RateInc+{RA})^($B{r}-1)/12"
                             f"*(1-IF($B{r}=1,MS_FYComm,MS_RenComm)-MS_PremTax)*(1+DiscRate+{DA})^(-$A{r}/12)"
                             f"-(((({C[2]}{r}-0.5*{C[2]}{r}*$E{r})*{RELS}+({C[3]}{r}-0.5*{C[3]}{r}*$E{r})*{RELN})"
                             f"*MS_Clm1*{CM}*(1+MS_ClmAge)^($B{r}-1)*(1+MS_Trend+{TA})^($B{r}-1)*CHOOSE(MIN($B{r},3),MS_Sel1,MS_Sel2,MS_Sel3))"
                             f"+({C[2]}{r}+{C[3]}{r}-0.5*({C[2]}{r}+{C[3]}{r})*$E{r})*MS_Maint*(1+ExpInfl)^($B{r}-1))/12*(1+DiscRate+{DA})^(-($A{r}+0.5)/12)")
    cc = wsc.cell(2, c0 + 1, f"=SUM({C[6]}{SR0}:{C[6]}{SR0 + MSK - 1})-MS_Cohort*MS_Acq"); cc.font = f_bold; cc.number_format = FMT_USD; cc.fill = fill_sum
    col_ptr += 7
    return f"Sens_Calc!${C[1]}$2"


# D&V block. Shocks: lm lapse multiplier, pa add-on to the year-1 pent-up factor, ta trend add-on,
# da discount-rate add-on, cm claim cost multiplier. In force rolls forward as in force x
# (1 - monthly lapse), equivalent to the main sheet's in force - lapses.
def dv_scn(label, lm=1.0, pa=0.0, ta=0.0, da=0.0, cm=1.0):
    global col_ptr
    c0 = col_ptr; C = [L(c0 + j) for j in range(5)]
    block_header(c0, "D&V — " + label, ["Lapse ×", "Pent-up +", "Trend +", "Disc rate +", "Claim ×"], [lm, pa, ta, da, cm],
                 [FMT_F4, FMT_F4, FMT_PCT, FMT_PCT, FMT_F4], 5)
    LM, PA, TA, DA, CM = [f"${C[j]}$4" for j in range(5)]
    for j, h in enumerate(["Monthly lapse", "In force", "PV cash"]):
        wsc.cell(5, c0 + j, h).font = f_bold
    for k in range(DVK):
        r = SR0 + k
        wsc[f"{C[0]}{r}"] = f"=1-(1-MIN(CHOOSE(MIN($B{r},3),DV_Lapse1,DV_Lapse2,DV_Lapse3)*{LM},0.95))^(1/12)"
        wsc[f"{C[1]}{r}"] = "=DV_Cohort" if k == 0 else f"={C[1]}{r-1}*(1-{C[0]}{r-1})"
        wsc[f"{C[2]}{r}"] = (f"={C[1]}{r}*DV_Prem1*(1+DV_RateInc)^($B{r}-1)/12*(1-IF($B{r}=1,DV_FYComm,DV_RenComm)-DV_PremTax)"
                             f"*(1+DiscRate+{DA})^(-$A{r}/12)"
                             f"-{C[1]}{r}*(DV_Clm1*{CM}*" + (f"IF(F_DVCats=1,{DV_CATSUM(r, f'DV_Trend+{TA}', f'DV_PentUp+{PA}', '$B', '$A')},(1+DV_Trend+{TA})^($B{r}-1)*IF($B{r}=1,DV_PentUp+{PA},1))" if DV_NCAT else f"(1+DV_Trend+{TA})^($B{r}-1)*IF($B{r}=1,DV_PentUp+{PA},1)") +
                             f"+DV_Maint*(1+ExpInfl)^($B{r}-1))/12*(1+DiscRate+{DA})^(-($A{r}+0.5)/12)")
    cc = wsc.cell(2, c0 + 1, f"=SUM({C[2]}{SR0}:{C[2]}{SR0 + DVK - 1})-DV_Cohort*DV_Acq"); cc.font = f_bold; cc.number_format = FMT_USD; cc.fill = fill_sum
    col_ptr += 5
    return f"Sens_Calc!${C[1]}$2"


# The MLR remittance is tested on contract-year totals, so each Medicaid block also carries a small
# annual table below the 240 monthly rows.
MD_ANN0 = SR0 + SC_K + 4                 # annual mini-table rows for Medicaid blocks


# Medicaid block. Shocks: ra rate-update add-on, ta trend add-on, ma add-on to the starting medical
# cost ratio, pa add-on to renewal probability (clipped to [0, 1]), dm disenrollment multiplier, aa
# add-on to plan acuity (risk adjustment). Monthly rows track members per rate cell; the annual
# table applies the MLR remittance (paid at y + 0.5) and the renewal weighting, and the block NPV
# deducts start-up at time 0, matching Medicaid_NPV.
def md_scn(label, ra=0.0, ta=0.0, ma=0.0, pa=0.0, dm=1.0, aa=0.0):
    global col_ptr
    W_ = 11 if MD_TERMS else 9
    c0 = col_ptr; C = [L(c0 + j) for j in range(W_)]
    block_header(c0, "Medicaid — " + label, ["Rate upd +", "Trend +", "MCR +", "Renew prob +", "Disenroll ×", "Acuity +"], [ra, ta, ma, pa, dm, aa],
                 [FMT_PCT, FMT_PCT, FMT_PCT, FMT_PCT, FMT_F4, FMT_PCT], W_)
    RA, TA, MA, PA, DM, AA = [f"${C[j]}$4" for j in range(6)]
    Q = lambda c: f"(1-(1-MIN(MD_Disenroll*{DM}*MD_QR{c},1))^(1/12))"
    RAREV = f"IF(F_RateCells=1,1+MD_RACap*(MD_Acu+{AA}-1),1)"
    RAMED = f"IF(F_RateCells=1,MD_Acu+{AA},1)"
    heads = [f"Members {CELLS[0]}", f"Members {CELLS[1]}", f"Members {CELLS[2]}", "Member months", "Cap-weighted MM", "Cost-weighted MM", "Revenue", "Medical", "PV operating"]
    if MD_TERMS:
        heads += ["Capitation gross (v4)", "Directed payments (v4)"]
    for j, h in enumerate(heads):
        wsc.cell(5, c0 + j, h).font = f_bold
    for k in range(MDK):
        r = SR0 + k
        mm = []
        for c in (1, 2, 3):
            col = C[c - 1]
            wsc[f"{col}{r}"] = f"=MD_Start*MD_S{c}" if k == 0 else f"={col}{r-1}-{col}{r-1}*{Q(c)}+MD_NewMembers/12*MD_S{c}"
            mm.append(f"({col}{r}-0.5*{col}{r}*{Q(c)}+0.5*MD_NewMembers/12*MD_S{c})")
        wsc[f"{C[3]}{r}"] = "=" + "+".join(mm)
        wsc[f"{C[4]}{r}"] = "=" + "+".join(f"{mm[c-1]}*MD_CR{c}" for c in (1, 2, 3))
        wsc[f"{C[5]}{r}"] = "=" + "+".join(f"{mm[c-1]}*MD_CR{c}*MD_MR{c}" for c in (1, 2, 3))
        med_ = f"{C[5]}{r}*MD_Cap1*(MD_MCR1+{MA})*(1+MD_Trend+{TA})^($B{r}-1)*IF($B{r}=1,1+MD_Acuity,1)*{RAMED}"
        if MD_TERMS:
            wsc[f"{C[9]}{r}"] = f"={C[4]}{r}*MD_Cap1*(1+MD_RateUpd+{RA})^($B{r}-1)*{RAREV}*INDEX(MD_RAPath,$B{r})"
            wsc[f"{C[10]}{r}"] = f"={C[3]}{r}*MD_DPe*(1+MD_DPTrend)^($B{r}-1)"
            wsc[f"{C[6]}{r}"] = f"={C[9]}{r}*(1-MD_WHe)+{C[10]}{r}"
            wsc[f"{C[7]}{r}"] = f"={med_}*INDEX(MD_AcuUsePath,$B{r})+{C[10]}{r}"
            tax_ = f"({C[9]}{r}+{C[10]}{r})*MD_PremTax"
        else:
            wsc[f"{C[6]}{r}"] = f"={C[4]}{r}*MD_Cap1*(1+MD_RateUpd+{RA})^($B{r}-1)*{RAREV}"
            wsc[f"{C[7]}{r}"] = f"={med_}"
            tax_ = f"{C[6]}{r}*MD_PremTax"
        wsc[f"{C[8]}{r}"] = f"=({C[6]}{r}-{C[7]}{r}-{tax_}-{C[3]}{r}*MD_Admin1*(1+ExpInfl)^($B{r}-1))*(1+DiscRate)^(-($A{r}+0.5)/12)"
    ann_heads = ["Year", "Revenue", "Medical", "PV operating", "Net settlement" if MD_TERMS else "Remittance", "PV contribution"]
    if MD_TERMS:
        ann_heads += ["Gross capitation", "Withhold earned"]
    for j, h in enumerate(ann_heads):
        wsc.cell(MD_ANN0 - 1, c0 + j, h).font = f_bold
    yr = f"$B${SR0}:$B${SR0 + MDK - 1}"
    for y in range(1, MDY + 1):
        r = MD_ANN0 + y - 1
        wsc[f"{C[0]}{r}"] = y
        wsc[f"{C[1]}{r}"] = f"=SUMIFS({C[6]}${SR0}:{C[6]}${SR0 + MDK - 1},{yr},{C[0]}{r})"
        wsc[f"{C[2]}{r}"] = f"=SUMIFS({C[7]}${SR0}:{C[7]}${SR0 + MDK - 1},{yr},{C[0]}{r})"
        wsc[f"{C[3]}{r}"] = f"=SUMIFS({C[8]}${SR0}:{C[8]}${SR0 + MDK - 1},{yr},{C[0]}{r})"
        if MD_TERMS:
            wsc[f"{C[6]}{r}"] = f"=SUMIFS({C[9]}${SR0}:{C[9]}${SR0 + MDK - 1},{yr},{C[0]}{r})"
            wsc[f"{C[7]}{r}"] = f"=MD_WHe*MD_WHEe*{C[6]}{r}"
            wsc[f"{C[4]}{r}"] = "=" + MD_SETTLE(f"({C[1]}{r}+{C[7]}{r})", f"{C[2]}{r}") + f"-{C[7]}{r}"
        else:
            wsc[f"{C[4]}{r}"] = f"=MAX(0,MD_MLRFloor*{C[1]}{r}-{C[2]}{r})"
        wsc[f"{C[5]}{r}"] = f"=IF({C[0]}{r}<=MD_Term,1,MIN(1,MAX(0,MD_RenewProb+{PA})))*({C[3]}{r}-{C[4]}{r}*(1+DiscRate)^(-({C[0]}{r}+0.5)))"
    cc = wsc.cell(2, c0 + 1, f"=SUM({C[5]}{MD_ANN0}:{C[5]}{MD_ANN0 + MDY - 1})-MD_Startup"); cc.font = f_bold; cc.number_format = FMT_USD; cc.fill = fill_sum
    col_ptr += W_
    return f"Sens_Calc!${C[1]}$2"


# Scenario definitions: (label, shocks). Base + one favorable/adverse pair per tornado driver
# (6 Med Supp, 5 D&V, 6 Medicaid drivers), in the same order as SENS in the Python engine.
# Labels that state an absolute level (Pent-up 1.15 / 1.40, Renewal 100% / 50%) are add-ons relative
# to the current base assumption; if the base value in the JSON changes, update these labels.
ms_specs = [("Base", {}), ("Lapse ×0.8", dict(lm=0.8)), ("Lapse ×1.2", dict(lm=1.2)), ("Trend −1%", dict(ta=-0.01)), ("Trend +1%", dict(ta=0.01)),
            ("Rate increase +1%", dict(ra=0.01)), ("Rate increase −1%", dict(ra=-0.01)), ("Discount −2%", dict(da=-0.02)), ("Discount +2%", dict(da=0.02)),
            ("Claims −5%", dict(cm=0.95)), ("Claims +5%", dict(cm=1.05)), ("Shopper cost 0.95", dict(sc=0.95)), ("Shopper cost 0.75", dict(sc=0.75))]
dv_specs = [("Base", {}), ("Lapse ×0.8", dict(lm=0.8)), ("Lapse ×1.2", dict(lm=1.2)), ("Pent-up 1.15", dict(pa=-0.13)), ("Pent-up 1.40", dict(pa=0.12)),
            ("Trend −1%", dict(ta=-0.01)), ("Trend +1%", dict(ta=0.01)), ("Discount −2%", dict(da=-0.02)), ("Discount +2%", dict(da=0.02)),
            ("Claims −5%", dict(cm=0.95)), ("Claims +5%", dict(cm=1.05))]
md_specs = [("Base", {}), ("Rate update +1%", dict(ra=0.01)), ("Rate update −1%", dict(ra=-0.01)), ("Trend −1%", dict(ta=-0.01)), ("Trend +1%", dict(ta=0.01)),
            ("MCR −2pt", dict(ma=-0.02)), ("MCR +2pt", dict(ma=0.02)), ("Renewal 100%", dict(pa=0.25)), ("Renewal 50%", dict(pa=-0.25)),
            ("Disenroll ×0.8", dict(dm=0.8)), ("Disenroll ×1.2", dict(dm=1.2)), ("Acuity −3%", dict(aa=-0.03)), ("Acuity +3%", dict(aa=0.03))]
ms_ref = {lab: ms_scn(lab, **kw) for lab, kw in ms_specs}
dv_ref = {lab: dv_scn(lab, **kw) for lab, kw in dv_specs}
md_ref = {lab: md_scn(lab, **kw) for lab, kw in md_specs}
wsc.freeze_panes = "F6"

# Tornado table: each row links the base, favorable and adverse NPVs from Sens_Calc; Swing is the
# absolute spread. Rows 6-11 Med Supp, 12-16 D&V, 17-22 Medicaid (the Dashboard relies on these).
wsn = wb.create_sheet("Sensitivity")
title(wsn, "Sensitivity — one assumption at a time",
      "NPV under each favorable and adverse shock. Every figure links to a live monthly re-projection on Sens_Calc.")
wsn["A4"] = "Tornado summary"; wsn["A4"].font = f_sec
header_row(wsn, 5, 1, ["Line", "Driver (favorable / adverse)", "Base NPV", "NPV favorable", "NPV adverse", "Swing"])
for col, w in zip("ABCDEF", [16, 46, 15, 15, 15, 15]):
    wsn.column_dimensions[col].width = w
tor_rows = [("Med Supp", "Lapse ×0.8 / ×1.2", ms_ref, "Lapse ×0.8", "Lapse ×1.2"),
            ("Med Supp", "Trend −1% / +1%", ms_ref, "Trend −1%", "Trend +1%"),
            ("Med Supp", "Rate increase +1% / −1%", ms_ref, "Rate increase +1%", "Rate increase −1%"),
            ("Med Supp", "Discount rate −2% / +2%", ms_ref, "Discount −2%", "Discount +2%"),
            ("Med Supp", "Claim-cost level −5% / +5% (all years)", ms_ref, "Claims −5%", "Claims +5%"),
            ("Med Supp", "Anti-selection: shopper cost 0.95 / 0.75", ms_ref, "Shopper cost 0.95", "Shopper cost 0.75"),
            ("Dental & Vision", "Lapse ×0.8 / ×1.2", dv_ref, "Lapse ×0.8", "Lapse ×1.2"),
            ("Dental & Vision", "Pent-up 1.15 / 1.40 (year 1 only)", dv_ref, "Pent-up 1.15", "Pent-up 1.40"),
            ("Dental & Vision", "Trend −1% / +1%", dv_ref, "Trend −1%", "Trend +1%"),
            ("Dental & Vision", "Discount rate −2% / +2%", dv_ref, "Discount −2%", "Discount +2%"),
            ("Dental & Vision", "Claim-cost level −5% / +5% (all years)", dv_ref, "Claims −5%", "Claims +5%"),
            ("Medicaid", "Rate update +1% / −1%", md_ref, "Rate update +1%", "Rate update −1%"),
            ("Medicaid", "Trend −1% / +1%", md_ref, "Trend −1%", "Trend +1%"),
            ("Medicaid", "Starting medical cost ratio −2pt / +2pt (all years)", md_ref, "MCR −2pt", "MCR +2pt"),
            ("Medicaid", "Renewal prob 100% / 50%", md_ref, "Renewal 100%", "Renewal 50%"),
            ("Medicaid", "Disenrollment ×0.8 / ×1.2", md_ref, "Disenroll ×0.8", "Disenroll ×1.2"),
            ("Medicaid", "Plan acuity −3% / +3% (80% risk-adjusted)", md_ref, "Acuity −3%", "Acuity +3%")]
for k, (line, drv, ref, lo, hi) in enumerate(tor_rows):
    rr = 6 + k
    wsn.cell(rr, 1, line); wsn.cell(rr, 2, drv)
    wsn.cell(rr, 3, f"={ref['Base']}"); wsn.cell(rr, 4, f"={ref[lo]}"); wsn.cell(rr, 5, f"={ref[hi]}")
    wsn.cell(rr, 6, f"=ABS(D{rr}-E{rr})")
    for j in range(1, 7):
        cc = wsn.cell(rr, j); cc.border = box; cc.font = f_base if j < 3 else f_link
        if j >= 3: cc.number_format = FMT_USD
    wsn.cell(rr, 6).font = f_base
TOR_LAST = 6 + len(tor_rows) - 1          # rows 6-11 Med Supp, 12-16 D&V, 17-22 Medicaid
TOR = {"Med Supp": (6, 11), "Dental & Vision": (12, 16), "Medicaid": (17, 22)}
# Control: the compact base block must equal the full engine within one cent. Because Sens_Calc
# ignores the scenario levers, this check also flags a non-neutral lever on the main sheets.
wsn["A25"] = "Base-case check: each base scenario must equal its main sheet"; wsn["A25"].font = f_sec
for k, (lab, ref, sh) in enumerate([("Med Supp", ms_ref["Base"], "MedSupp_NPV"), ("Dental & Vision", dv_ref["Base"], "DentalVision_NPV"), ("Medicaid", md_ref["Base"], "Medicaid_NPV")]):
    rr = 26 + k
    wsn.cell(rr, 1, lab); wsn.cell(rr, 2, f"={ref}-'{sh}'!D5").number_format = FMT_USD2
    wsn.cell(rr, 3, f'=IF(ABS(B{rr})<0.01,"TIES","CHECK")')
wsn.freeze_panes = "A6"

# =============================================================================
# EXPERIENCE (study results from the SQL kit on the synthetic data)
# Documents where assumptions come from and how emerging experience is monitored against them.
# =============================================================================
we = wb.create_sheet("Experience")
title(we, "Experience studies — where assumptions come from and how they are checked",
      "Values below are results of the SQL programs (05, 04, 06) run on the synthetic practice data. Blue = pasted study output; black = formulas.")
# Section 1: completed experience by line and calendar year (claims completed for IBNR in SQL).
# Study output is pasted as blue values; loss ratio and PMPM change are formulas.
yearly = pd.read_csv(SQLR / "05_npv_assumption_inputs_gold.csv")
we["A4"] = "1. Completed experience by year (claims completed for IBNR)"; we["A4"].font = f_sec
header_row(we, 5, 1, ["Line", "Year", "Member months", "Premium PMPM", "Completed claims PMPM",
                      "Loss ratio", "Claims PMPM change", "Note"])
r = 6
prev_lob = None
for _, x in yearly.iterrows():
    we.cell(r, 1, x.lob); we.cell(r, 2, str(int(x.year)))
    for j, v in [(3, x.member_months), (4, x.premium_pmpm), (5, x.completed_claims_pmpm)]:
        cc = we.cell(r, j, float(v)); cc.font = f_input
    we.cell(r, 6, f"=E{r}/D{r}")
    we.cell(r, 7, "" if x.lob != prev_lob else f"=E{r}/E{r-1}-1")
    prev_lob = x.lob
    for j, fm in zip(range(1, 8), ["@", "@", FMT_NUM, FMT_USD2, FMT_USD2, FMT_PCT, FMT_PCT]):
        we.cell(r, j).number_format = fm; we.cell(r, j).border = box
        if we.cell(r, j).font != f_input: we.cell(r, j).font = f_base
    r += 1
# Annotations are placed at fixed row offsets that assume the CSV's current row order (by line, then
# year); revisit them if the SQL output changes shape.
if SYNTH:   # commentary specific to the synthetic book
    we.cell(6 + 10, 8, "Block PMPM growth includes aging + selection wear-off, so it exceeds pure cost trend (see Python 02).").font = Font(name=F, size=9, italic=True)
    md_2025 = 6 + 7
    we.cell(md_2025, 8, "Investigate: 2025 jump = one pooled $250K claim + heavily estimated recent months + noise.").font = Font(name=F, size=9, italic=True, color="B03A2E")
for col, w in zip("ABCDEFGH", [16, 8, 14, 13, 15, 11, 13, 80]):
    we.column_dimensions[col].width = w

# Section 2: lapse study. Observed annual lapse is converted from the monthly rate,
# 1 - (1 - lapses / exposure months)^12, and compared with the model assumption. Med Supp
# durations 2+ are compared with base lapse combined with the anniversary shock, since the
# experience includes both. A/E ratios outside 0.9-1.1 are highlighted.
lap = pd.read_csv(SQLR / "04_lapse_persistency_study.csv")
r += 2
we.cell(r, 1, "2. Lapse study (study window 2022–2025)").font = f_sec; r += 1
header_row(we, r, 1, ["Line", "Duration year", "Exposure months", "Lapses", "Annual lapse (formula)",
                      "Model assumption", "Actual ÷ assumption", "Note"]); r += 1
amap = {("MEDSUPP", "1"): "=MS_Lapse1",
        ("MEDSUPP", "2"): "=1-(1-MS_Lapse2)*(1-MS_Shock)",     # base lapse combined with Jan shock
        ("MEDSUPP", "3+"): "=1-(1-MS_Lapse3)*(1-MS_Shock)",
        ("DENTAL_VISION", "1"): "=DV_Lapse1", ("DENTAL_VISION", "2"): "=DV_Lapse2", ("DENTAL_VISION", "3+"): "=DV_Lapse3",
        ("MEDICAID", "1"): "=MD_Disenroll", ("MEDICAID", "2"): "=MD_Disenroll", ("MEDICAID", "3+"): "=MD_Disenroll"}
lap_first = r
for _, x in lap.iterrows():
    we.cell(r, 1, x.lob); we.cell(r, 2, str(x.duration_year))
    a = we.cell(r, 3, int(x.exposure_months)); a.font = f_input
    b = we.cell(r, 4, int(x.lapses)); b.font = f_input
    we.cell(r, 5, f"=1-(1-D{r}/C{r})^12")
    we.cell(r, 6, amap[(x.lob, str(x.duration_year))])
    we.cell(r, 7, f"=E{r}/F{r}")
    for j, fm in zip(range(1, 8), ["@", "@", FMT_NUM, FMT_NUM, FMT_PCT2, FMT_PCT2, '0.00"x"']):
        we.cell(r, j).number_format = fm; we.cell(r, j).border = box
        if j not in (3, 4): we.cell(r, j).font = f_base
    r += 1
if SYNTH:
    we.cell(lap_first + 3, 8, "Medicaid experience includes the 2023–24 redetermination wave; remove one-offs before setting a forward assumption.").font = Font(name=F, size=9, italic=True)
we.cell(lap_first + 7, 8, "Med Supp yrs 2+ compared with base lapse combined with the January rate-increase shock.").font = Font(name=F, size=9, italic=True)
we.conditional_formatting.add(f"G{lap_first}:G{r-1}", CellIsRule(operator="notBetween", formula=["0.9", "1.1"],
                              font=Font(name=F, color="B03A2E", bold=True)))

# Section 3: quarterly actual-to-expected monitoring with REVIEW flags (claims +/-5%, terminations
# +/-15%).
ae = pd.read_csv(SQLR / "06_actual_vs_expected_monitoring.csv")
r += 2
we.cell(r, 1, "3. Actual-to-expected monitoring by quarter (claims A/E outside ±5% or terminations outside ±15% → REVIEW)").font = f_sec; r += 1
header_row(we, r, 1, ["Line", "Quarter", "Actual claims", "Expected claims", "Claims A/E",
                      "Actual terminations", "Expected terminations", "Termination A/E", "Flag"]); r += 1
ae_first = r
for _, x in ae.iterrows():
    we.cell(r, 1, x.lob); we.cell(r, 2, x.quarter)
    for j, v in [(3, x.actual_claims), (4, x.expected_claims), (6, x.actual_terms), (7, x.expected_terms)]:
        cc = we.cell(r, j, float(v)); cc.font = f_input
    we.cell(r, 5, f"=C{r}/D{r}"); we.cell(r, 8, f"=F{r}/G{r}")
    we.cell(r, 9, f'=IF(OR(ABS(E{r}-1)>0.05,ABS(H{r}-1)>0.15),"REVIEW","OK")')
    for j, fm in zip(range(1, 10), ["@", "@", FMT_USD, FMT_USD, '0.000', FMT_NUM, FMT_NUM, '0.000', "@"]):
        we.cell(r, j).number_format = fm; we.cell(r, j).border = box
        if j not in (3, 4, 6, 7): we.cell(r, j).font = f_base
    r += 1
we.conditional_formatting.add(f"I{ae_first}:I{r-1}", CellIsRule(operator="equal", formula=['"REVIEW"'],
                              font=Font(name=F, color="B03A2E", bold=True)))
if SYNTH:
    we.cell(r + 1, 1, "Reading it: the expected basis is deliberately simple (no seasonality, no duration effect). The REVIEW flags it raises are real signals: "
            "Med Supp terminations spike every Q1 (lapses after the January rate increase) and Medicaid terminations spike in 2023-Q2 to 2024-Q2 "
            "(the post-pandemic redetermination wave). Persistent patterns like these mean the assumption should be refined, not explained away each quarter.").font = Font(name=F, size=9, italic=True)
we.freeze_panes = "A4"


# =============================================================================
# BACKTEST (results pasted from 06_npv_backtest.py; differences are formulas)
# =============================================================================
# Model NPV is linked live from the annual sheets; realized-outcome statistics are pasted from the
# simulation. Section 1 tests bias: z = (realized mean - model) / standard error, |z| <= 2 passes.
# Section 2 shows process risk (P5 / median / P95, probability of loss). Section 3 is the version 1
# bias attribution that motivated the monthly rebuild. Section 4 measures NPV error from estimating
# assumptions on four years of experience; section 5 compares process vs parameter risk widths.
BT = OUTPUT_DIR / "backtest"
HAS_BT = (BT / "test_a_realized_vs_model.csv").exists()
if not HAS_BT:
    # Production cycle: the backtest needs the known data-generating process, so it runs in the
    # synthetic validation run at each model release, not on every production cycle.
    wbt = wb.create_sheet("Backtest")
    title(wbt, "Backtest — not run in this cycle",
          "The backtest validates the model mechanics against a known data-generating process; it runs in synthetic mode at each model release (see docs/MODEL_DOCUMENTATION.md).")
    lob_rows = [("Medicare Supplement", "MEDSUPP", "MedSupp_NPV"), ("Dental & Vision", "DENTAL_VISION", "DentalVision_NPV"), ("Medicaid", "MEDICAID", "Medicaid_NPV")]
else:
    ta = pd.read_csv(BT / "test_a_realized_vs_model.csv").set_index("lob")
    attr = pd.read_csv(BT / "test_a_bias_attribution.csv")
    tb = pd.read_csv(BT / "test_b_estimation_error.csv")
    tc = pd.read_csv(BT / "test_c_process_vs_parameter.csv").set_index("lob")
    wbt = wb.create_sheet("Backtest")
    title(wbt, "Backtest — how good are the NPV predictions?",
          "Blue values are pasted from python/06_npv_backtest.py (4,000 simulated realized cohorts per line, month by month, event level). Black cells are formulas.")
    for col, w in zip("ABCDEFGHI", [44, 16, 16, 14, 14, 12, 16, 16, 14]):
        wbt.column_dimensions[col].width = w
    wbt["A4"] = "1. Current model (version 4) vs realized outcomes"; wbt["A4"].font = f_sec
    header_row(wbt, 5, 1, ["Line", "Model NPV (this workbook)", "Realized mean (simulated)", "Difference", "Difference %",
                           "Std error of mean", "z-score", "Verdict", ""])
    lob_rows = [("Medicare Supplement", "MEDSUPP", "MedSupp_NPV"), ("Dental & Vision", "DENTAL_VISION", "DentalVision_NPV"), ("Medicaid", "MEDICAID", "Medicaid_NPV")]
    for k, (lab, key, sh) in enumerate(lob_rows):
        rr = 6 + k
        wbt.cell(rr, 1, lab).font = f_bold
        c = wbt.cell(rr, 2, f"='{sh}'!D5"); c.font = f_link; c.number_format = FMT_USD
        c = wbt.cell(rr, 3, float(ta.loc[key, "realized_mean"])); c.font = f_input; c.number_format = FMT_USD
        wbt.cell(rr, 4, f"=C{rr}-B{rr}").number_format = FMT_USD
        wbt.cell(rr, 5, f"=D{rr}/B{rr}").number_format = FMT_PCT2
        c = wbt.cell(rr, 6, float(ta.loc[key, "bias_se"])); c.font = f_input; c.number_format = FMT_USD
        wbt.cell(rr, 7, f"=D{rr}/F{rr}").number_format = "0.00"
        wbt.cell(rr, 8, f'=IF(ABS(G{rr})<=2,"Unbiased","Check")')
        for j in range(1, 9): wbt.cell(rr, j).border = box
    wbt.cell(9, 1, "|z| ≤ 2 means the model's NPV sits within simulation noise of the average realized outcome.").font = Font(name=F, size=9, italic=True)

    wbt["A11"] = "2. Range of realized outcomes with the assumptions known (process risk)"; wbt["A11"].font = f_sec
    header_row(wbt, 12, 1, ["Line", "P5", "Median", "P95", "Probability of loss", "", "", "", ""])
    for k, (lab, key, sh) in enumerate(lob_rows):
        rr = 13 + k
        wbt.cell(rr, 1, lab).font = f_bold
        for j, col in [(2, "realized_p5"), (3, "realized_p50"), (4, "realized_p95")]:
            c = wbt.cell(rr, j, float(ta.loc[key, col])); c.font = f_input; c.number_format = FMT_USD
        c = wbt.cell(rr, 5, float(ta.loc[key, "prob_loss"])); c.font = f_input; c.number_format = FMT_PCT
        for j in range(1, 6): wbt.cell(rr, j).border = box
    wbt.cell(16, 1, "Medicaid's range is wide and its median above its mean because 25% of simulated contracts are not renewed after year 5.").font = Font(name=F, size=9, italic=True)

    wbt["A18"] = "3. Model history: version 1 bias, version 2 fix, version 3 mechanics (two-class Med Supp, Medicaid rate cells)"; wbt["A18"].font = f_sec
    header_row(wbt, 19, 1, ["Line / step", "NPV", "Change", "", "", "", "", "", ""])
    rr = 20
    for _, x in attr.iterrows():
        wbt.cell(rr, 1, f"{x.lob}: {x.step}")
        c = wbt.cell(rr, 2, float(x.value)); c.font = f_input; c.number_format = FMT_USD
        if pd.notna(x.change):
            c = wbt.cell(rr, 3, float(x.change)); c.font = f_input; c.number_format = FMT_USD
        for j in range(1, 4): wbt.cell(rr, j).border = box
        rr += 1
    wbt.column_dimensions["A"].width = 95
    rr += 1
    wbt.cell(rr, 1, "4. NPV error from estimating assumptions from four years of experience, measured against the generator's true values (version 4 engine)").font = f_sec; rr += 1
    header_row(wbt, rr, 1, ["Line: assumption", "NPV with true assumption", "NPV with estimate", "Error", "Error %", "", "", "", ""]); rr += 1
    for _, x in tb.iterrows():
        wbt.cell(rr, 1, f"{x.lob}: {x.assumption}")
        for j, v in [(2, x.npv_true), (3, x.npv_with_estimate)]:
            c = wbt.cell(rr, j, float(v)); c.font = f_input; c.number_format = FMT_USD
        wbt.cell(rr, 4, f"=C{rr}-B{rr}").number_format = FMT_USD
        wbt.cell(rr, 5, f"=D{rr}/B{rr}").number_format = FMT_PCT
        for j in range(1, 6): wbt.cell(rr, j).border = box
        rr += 1
    rr += 1
    wbt.cell(rr, 1, "5. Width of the 90% outcome range: randomness vs uncertainty about assumptions").font = f_sec; rr += 1
    header_row(wbt, rr, 1, ["Line", "Process (assumptions known)", "Parameter (Monte Carlo)", "Ratio", "", "", "", "", ""]); rr += 1
    for lab, key, _ in lob_rows:
        wbt.cell(rr, 1, lab)
        for j, v in [(2, tc.loc[key, "process_width"]), (3, tc.loc[key, "parameter_width"])]:
            c = wbt.cell(rr, j, float(v)); c.font = f_input; c.number_format = FMT_USD
        wbt.cell(rr, 4, f"=C{rr}/B{rr}").number_format = '0.0"×"'
        for j in range(1, 5): wbt.cell(rr, j).border = box
        rr += 1

# =============================================================================
# DASHBOARD
# Created at tab position 1 (after README); final tab order is set at the end of the script.
# =============================================================================
# Headline results link to the fixed results cells on each annual sheet. The tie-out column
# compares the workbook NPV with the Python engine NPV (pasted from npv_summary.csv) and shows
# TIES within $1; the cent-level reconciliation of every year and scenario is done by
# 05_reconcile_python_to_excel.py. The lever check confirms the tie-out is being read at the base
# case; the backtest verdict summarizes the Backtest sheet.
wdb = wb.create_sheet("Dashboard", 1)
title(wdb, "Dashboard — NPV results at a glance (version 4.2)", "All figures link to the model sheets. Base case requires scenario levers at neutral. Cash NPV = pre-tax (v2 measure); DE NPV = after tax and cost of capital.")
header_row(wdb, 4, 1, ["Line of business", "Cash NPV (pre-tax)", "DE NPV (after tax)", "Per policy / member (cash)", "PV loss ratio",
                       "Cash NPV margin", "Break-even year", "Python cash NPV", "Python DE NPV", "Max |Excel − Python|", "Tie-out"])
dash = [("Medicare Supplement (per 1,000 policies)", "MedSupp_NPV", "MEDSUPP"),
        ("Dental & Vision (per 1,000 policies)", "DentalVision_NPV", "DENTAL_VISION"),
        ("Medicaid (contract, 10,000 members)", "Medicaid_NPV", "MEDICAID")]
for k, (lab, sh, key) in enumerate(dash):
    rr = 5 + k
    wdb.cell(rr, 1, lab).font = f_bold
    for j, src, fm in [(2, "D5", FMT_USD), (3, "D13", FMT_USD), (4, "D6", FMT_USD2), (5, "D9", FMT_PCT2), (6, "D10", FMT_PCT2), (7, "D11", "0")]:
        cc = wdb.cell(rr, j, f"='{sh}'!{src}"); cc.font = f_link; cc.number_format = fm
    for j, col in [(8, "npv"), (9, "de_npv")]:
        pcell = wdb.cell(rr, j, float(PY.loc[key, col])); pcell.font = f_input; pcell.number_format = FMT_USD
        pcell.comment = Comment("Pasted from output/npv/npv_summary.csv (Python 04_npv_engine.py, same assumption file).", "model")
    wdb.cell(rr, 10, f"=MAX(ABS(B{rr}-H{rr}),ABS(C{rr}-I{rr}))").number_format = FMT_USD2
    wdb.cell(rr, 11, f'=IF(J{rr}<1,"TIES","CHECK")')
    for j in range(1, 12):
        wdb.cell(rr, j).border = box
        if j in (10, 11): wdb.cell(rr, j).font = f_base
lev = wdb.cell(9, 1, "Scenario levers at neutral?"); lev.font = f_bold
wdb.cell(9, 2, '=IF(AND(MS_LapseMult=1,MS_TrendAdd=0,MS_RateAdd=0,DV_LapseMult=1,MD_RateAdd=0,MD_TrendAdd=0),"YES - base case","NO - scenario active")').font = f_bold
wdb.cell(10, 1, "Backtest verdict (model vs 4,000 realized cohorts)").font = f_bold
wdb.cell(10, 2, '=IF(AND(Backtest!H6="Unbiased",Backtest!H7="Unbiased",Backtest!H8="Unbiased"),"Unbiased on all three lines","Check Backtest sheet")'
         if HAS_BT else "Not run in this cycle (runs at model release, synthetic mode)").font = f_bold
wdb.cell(11, 1, "v3 features (anti-selection / dynamic lapse / rate cells)").font = f_bold
wdb.cell(11, 2, '=IF(AND(F_AntiSel=1,F_DynLapse=1,F_RateCells=1,F_MDTerms=1,F_DVCats=1),"All on (version 4.2)",IF(AND(F_AntiSel=0,F_DynLapse=0,F_RateCells=0,F_MDTerms=0,F_DVCats=0),"All off (version 2)","Mixed - check Assumptions"))').font = f_bold
wdb.conditional_formatting.add("K5:K7", CellIsRule(operator="equal", formula=['"CHECK"'], font=Font(name=F, color="B03A2E", bold=True)))
wdb.conditional_formatting.add("K5:K7", CellIsRule(operator="equal", formula=['"TIES"'], font=Font(name=F, color="008000", bold=True)))
for col, w in zip("ABCDEFGHIJK", [44, 27, 16, 14, 12, 18, 16, 16, 16, 16, 14]):
    wdb.column_dimensions[col].width = w

# Largest swing per line: MAX over that line's tornado rows, INDEX/MATCH returns the driver name.
wdb["A13"] = "Biggest swing factor by line (from Sensitivity, cash NPV)"; wdb["A13"].font = f_sec
header_row(wdb, 14, 1, ["Line", "Driver", "Swing in NPV"])
for k, line in enumerate(["Med Supp", "Dental & Vision", "Medicaid"]):
    lo, hi = TOR[line]
    rr = 15 + k
    wdb.cell(rr, 1, line).font = f_base
    wdb.cell(rr, 2, f"=INDEX(Sensitivity!$B${lo}:$B${hi},MATCH(MAX(Sensitivity!$F${lo}:$F${hi}),Sensitivity!$F${lo}:$F${hi},0))").font = f_link
    cc = wdb.cell(rr, 3, f"=MAX(Sensitivity!$F${lo}:$F${hi})"); cc.font = f_link; cc.number_format = FMT_USD
    for j in range(1, 4): wdb.cell(rr, j).border = box

# Expected value block (pasted from the Python Monte Carlo, which has no Excel counterpart). For
# Medicaid the deterministic NPV overstates expected value: the MLR floor gives back favorable
# years and keeps adverse ones, a cost only a stochastic run can see.
MCS = pd.read_csv(OUTPUT_DIR / "npv" / "npv_monte_carlo_stats.csv", index_col=0)
wdb["F13"] = "Expected value (Python Monte Carlo, 10,000 correlated scenarios; pasted)"; wdb["F13"].font = f_sec
header_row(wdb, 14, 6, ["Line", "Deterministic cash NPV", "MC mean cash NPV", "MC mean DE NPV", "P(cash NPV < 0)", "Cost of MLR floor"])
for k, (lab, sh, key) in enumerate(dash):
    rr = 15 + k
    wdb.cell(rr, 6, lab.split(" (")[0]).font = f_base
    c = wdb.cell(rr, 7, f"='{sh}'!D5"); c.font = f_link; c.number_format = FMT_USD
    for j, (row_key, col, fm) in enumerate([(key, "mean", FMT_USD), (key + "_DE", "mean", FMT_USD), (key, "prob_npv_negative", FMT_PCT)]):
        c = wdb.cell(rr, 8 + j, float(MCS.loc[row_key, col])); c.font = f_input; c.number_format = fm
    if key == "MEDICAID":
        _col = "mean_without_mlr_floor" if "mean_without_mlr_floor" in MCS.columns and not pd.isna(MCS.loc["MEDICAID"].get("mean_without_mlr_floor")) else "mean_without_mlr_settlement"
        c = wdb.cell(rr, 11, float(MCS.loc["MEDICAID", _col] - MCS.loc["MEDICAID", "mean"])); c.font = f_input; c.number_format = FMT_USD
        if _col != "mean_without_mlr_floor":
            wdb.cell(14, 11, "Cost of MLR settlement (corridor, both sides)")
    for j in range(6, 12): wdb.cell(rr, j).border = box
wdb.cell(18, 6, "Medicaid: lead with the expected value; the deterministic NPV omits the cost of the MLR floor.").font = Font(name=F, size=9, italic=True)

wdb["A19"] = "Talking points this model supports"; wdb["A19"].font = f_sec
# Talking points: the figures are computed from the Python results at build time, so they cannot go stale.
_ms, _dv, _md = PY.loc["MEDSUPP"], PY.loc["DENTAL_VISION"], PY.loc["MEDICAID"]
_usd = lambda x: ("−$" if x < 0 else "$") + f"{abs(x):,.0f}"
PRICING_TOL = json.loads((ROOT / "config" / "pricing_targets.json").read_text())["risk_tolerance"]
_be = pd.read_csv(OUTPUT_DIR / "npv" / "npv_breakeven.csv") if (OUTPUT_DIR / "npv" / "npv_breakeven.csv").exists() else None
_dvp = None
if _be is not None:
    _r = _be[(_be.lob == "DENTAL_VISION") & (_be.measure == "de") & (_be.driver == "Year-1 premium (multiplier)")]
    _dvp = float(_r.breakeven.iloc[0]) - 1 if len(_r) and pd.notna(_r.breakeven.iloc[0]) else None
_md_txt = (f"Medicaid: cash NPV {_usd(_md.npv)} but DE NPV {_usd(_md.de_npv)}: the contract does not earn the hurdle rate on its required capital"
           if _md.npv > 0 > _md.de_npv else f"Medicaid DE NPV {_usd(_md.de_npv)}")
tps = ["Med Supp value is earned after year 1, so persistency and rate adequacy drive NPV; trend and rate increases are the top swing factors.",
       "Anti-selective shock lapse is the largest assumption with no experience basis: healthier shoppers leave at each rate increase and the block's claim cost drifts up (see MedSupp_NPV columns R–S).",
       (f"D&V is below break-even on market inputs (DE NPV {_usd(_dv.de_npv)}); it needs {_dvp:.1%} more premium to break even (V4_Analytics, reverse stress)."
        if _dv.de_npv < 0 and _dvp is not None else f"D&V DE NPV {_usd(_dv.de_npv)}; the claim-cost level and the year-1 pent-up factor are its largest drivers."),
       f"{_md_txt}. Its value lives in the spread between state rate updates and medical trend; the MLR floor caps the upside.",
       f"DE NPV is the decision measure: claim lag adds value, but investment income below the hurdle rate, required capital, tax and (Med Supp) the IRC §848 DAC tax leave Med Supp DE {1 - _ms.de_npv / _ms.npv:.0%} below its cash NPV.",
       "The backtest found version 1's annual conventions biased (Medicaid −12%); versions 2 to 4.2 are unbiased within simulation noise (Backtest sheet)."]
_pr = pd.read_csv(OUTPUT_DIR / "npv" / "npv_pricing_solve.csv") if (OUTPUT_DIR / "npv" / "npv_pricing_solve.csv").exists() else None
if _pr is not None:
    _p = _pr[_pr.item == "Premium (risk-based target)"].set_index("lob")
    _bid = _pr[(_pr.lob == "MEDICAID") & (_pr.item == "Bid recommendation")].status.iloc[0]
    tps.insert(0, f"v4.2 recommended pricing (P(DE < 0) <= {PRICING_TOL:.0%}): Med Supp {_p.loc['MEDSUPP', 'status'].lower()}; "
                  f"D&V premium {_usd(_p.loc['DENTAL_VISION', 'current'])} -> {_usd(_p.loc['DENTAL_VISION', 'recommended'])} a year "
                  f"({_p.loc['DENTAL_VISION', 'change']:+.1%}), DE NPV {_usd(_p.loc['DENTAL_VISION', 'de_npv_recommended'])}; Medicaid: {_bid.split(':')[0].lower()}.")
for k, t in enumerate(tps):
    wdb.cell(20 + k, 1, "• " + t).font = f_base

# Charts reference live ranges on the annual and Sensitivity sheets.
from openpyxl.chart import Series as _S
ch = LineChart(); ch.title = "Med Supp: profit and PV profit by year"; ch.y_axis.title = "$"; ch.x_axis.title = "Year"
ch.series.append(_S(Reference(wm, min_col=11, min_row=R0, max_row=last), title="Profit (cash)"))
ch.series.append(_S(Reference(wm, min_col=12, min_row=R0, max_row=last), title="PV of profit"))
ch.set_categories(Reference(wm, min_col=1, min_row=R0, max_row=last)); ch.height, ch.width = 7.5, 16
wdb.add_chart(ch, "A28")
ch2 = LineChart(); ch2.title = "Policies in force (start of year)"; ch2.x_axis.title = "Year"
ch2.series.append(_S(Reference(wm, min_col=2, min_row=R0, max_row=R0 + 9), title="Med Supp policies"))
ch2.series.append(_S(Reference(wd, min_col=2, min_row=R0, max_row=R0 + 9), title="D&V policies"))
ch2.set_categories(Reference(wm, min_col=1, min_row=R0, max_row=R0 + 9)); ch2.height, ch2.width = 7.5, 14
wdb.add_chart(ch2, "F28")
ch3 = BarChart(); ch3.type = "bar"; ch3.title = "Medicaid: NPV under favorable vs adverse shocks"
for colno, ttl in [(4, "Favorable"), (5, "Adverse")]:
    ch3.series.append(_S(Reference(wsn, min_col=colno, min_row=TOR["Medicaid"][0], max_row=TOR["Medicaid"][1]), title=ttl))
ch3.set_categories(Reference(wsn, min_col=2, min_row=TOR["Medicaid"][0], max_row=TOR["Medicaid"][1])); ch3.height, ch3.width = 7.5, 16
wdb.add_chart(ch3, "A44")

# =============================================================================
# V4 ANALYTICS (values from the Python pipeline, clearly labelled; not formulas)
# =============================================================================
# The v4 analytics that need root finding, Monte Carlo or data cuts (break-even, planning
# scenarios, Monte Carlo attribution, Med Supp model points, in-force forecast, A/E waterfall,
# forecast accuracy) are computed in Python and shown here as values, so a reader has them next
# to the formula model. The base model point links to the live MedSupp_NPV result as a check.
wv = wb.create_sheet("V4_Analytics")
title(wv, "Version 4.2 analytics — values from the Python pipeline",
      "Not formulas: these tables come from output/npv and output/forecast. The check cell links the base model point to the live MedSupp_NPV result.")
_vr = [4]


def v4_table(heading, df, fmts=None, note=None):
    r0 = _vr[0]
    wv.cell(r0, 1, heading).font = f_sec
    if note:
        wv.cell(r0 + 1, 1, note).font = Font(name=F, size=9, italic=True, color=GREY)
    hr = r0 + (2 if note else 1)
    header_row(wv, hr, 1, list(df.columns))
    wv.row_dimensions[hr].height = 30
    for i, row_ in enumerate(df.itertuples(index=False), start=1):
        for j, v in enumerate(row_, start=1):
            v = None if (isinstance(v, float) and np.isnan(v)) else v
            c_ = wv.cell(hr + i, j, v); c_.font = f_base; c_.border = box
            if fmts and df.columns[j - 1] in fmts and v is not None:
                c_.number_format = fmts[df.columns[j - 1]]
    _vr[0] = hr + len(df) + 3
    return hr


V4D = OUTPUT_DIR / "npv"
if (V4D / "medsupp_model_points.csv").exists():
    mp_ = pd.read_csv(V4D / "medsupp_model_points.csv")
    hr = v4_table("Med Supp new-business portfolio (model points: issue-age band x plan)", mp_,
                  {"mix": FMT_PCT, "cash_npv_per_1000": FMT_USD, "de_npv_per_1000": FMT_USD, "pv_loss_ratio": FMT_PCT, "npv_margin": FMT_PCT2, "issue_age": "0.0"},
                  "Each cell is the base engine at its issue age and plan relativities. PORTFOLIO = mix-weighted. Plan F excluded from new business (closed to people newly eligible since 2020).")
    wv.cell(hr, 10, "Check: base cell = MedSupp_NPV").font = f_bold
    bi = int(mp_.index[(mp_.band == "65") & (mp_.plan == "G")][0]) + hr + 1
    chk = wv.cell(hr + 1, 10, f"=IF(ABS(E{bi}-MedSupp_NPV!$D$5)<0.01,\"ties\",\"DOES NOT TIE\")"); chk.font = f_bold
for f_, h_, fm_, nt_ in [
        ("npv_breakeven.csv", "Reverse stress: driver level at which NPV = 0 (other assumptions at base)",
         {"base_level": FMT_F4, "breakeven": FMT_F4, "headroom": FMT_F4, "base_npv": FMT_USD},
         "headroom = break-even minus base (direction, not cushion). position 'below break-even': the base already fails that measure and |headroom| is the shortfall."),
        ("npv_planning_scenarios.csv", "Planning scenarios (several shocks at once; config/planning_scenarios.json)",
         {"cash_npv": FMT_USD, "de_npv": FMT_USD, "cash_vs_base": FMT_USD, "de_vs_base": FMT_USD}, None),
        ("medsupp_rating_bases.csv", "Med Supp by rating basis (GA, AZ, FL, MO prohibit attained-age rating): issue-age and community premiums solved for equal PV loss ratio",
         {"mix": FMT_PCT, "year1_premium": FMT_USD, "annual_rate_increase": FMT_PCT, "pv_claims": FMT_USD, "pv_premium": FMT_USD, "pv_loss_ratio": FMT_PCT, "cash_npv_per_1000": FMT_USD}, None),
        ("npv_judgment_sensitivity.csv", "Judgment assumptions across their documented ranges (ASOP 56 §3.6.1; config/assumption_basis.json)",
         {"base_cash": FMT_USD, "base_de": FMT_USD, "de_low_setting": FMT_USD, "de_high_setting": FMT_USD, "de_swing": FMT_USD}, None),
        ("npv_pricing_solve.csv", "v4.2 RECOMMENDED PRICING: each line priced so P(DE NPV < 0) <= the risk tolerance in config/pricing_targets.json (Monte Carlo, trend at best estimate), within regulatory constraints",
         {"current": "#,##0.0000", "required_minimum": "#,##0.0000", "recommended": FMT_USD2, "change": FMT_PCT2, "p_de_negative_current": FMT_PCT,
          "p_de_negative_recommended": FMT_PCT, "de_quantile_recommended": FMT_USD, "cash_npv_recommended": FMT_USD, "de_npv_recommended": FMT_USD,
          "de_margin_recommended": FMT_PCT2, "pv_loss_ratio_recommended": FMT_PCT, "market_reference": FMT_USD},
         "Medicaid capitation is state-set, so its rows give the level each lever would need and the bid recommendation. Excel ties to Python at the recommended premiums (tests/e2e_excel_pricing.py)."),
        ("medsupp_cell_pricing.csv", "v4.2 Med Supp model points priced to the base cell's DE margin, capped by the 65% minimum loss ratio",
         {"mix": FMT_PCT, "year1_premium_current": FMT_USD2, "year1_premium_required": FMT_USD2, "change": FMT_PCT2, "plan_relativity_current": FMT_F4,
          "plan_relativity_required": FMT_F4, "de_margin_current": FMT_PCT2, "de_margin_required": FMT_PCT2, "de_npv_per_1000_current": FMT_USD,
          "de_npv_per_1000_required": FMT_USD, "pv_loss_ratio_required": FMT_PCT}, None),
        ("dv_product_options.csv", "D&V product options: waiting periods defer pent-up demand (upper bounds: lapse and anti-selection do not respond)",
         {"cash_npv": FMT_USD, "de_npv": FMT_USD, "de_vs_base": FMT_USD, "major_cost_share": FMT_PCT}, None),
        ("npv_mc_parameter_sensitivity.csv", "Monte Carlo parameters across their documented ranges (judgment until calibrated with estimate_mc_parameters.py)",
         {"mean": FMT_USD, "prob_negative": FMT_PCT}, None),
        ("dac_tax_MEDSUPP.csv", "Med Supp DAC tax (IRC §848): capitalized, amortized, extra tax by tax year (also live formulas on MedSupp_Profit)",
         {"capitalized": FMT_USD, "amortized": FMT_USD, "extra_tax": FMT_USD}, None),
        ("npv_monte_carlo_attribution.csv", "Monte Carlo attribution: v3 basis -> + process risk -> + rate-filing response (same parameter draws)",
         {"mean": FMT_USD, "P5": FMT_USD, "P95": FMT_USD, "prob_negative": FMT_PCT},
         "The filing-response strength (gap closure per filing) is judgment; its sensitivity rows show how much the tail depends on it.")]:
    if (V4D / f_).exists():
        df_ = pd.read_csv(V4D / f_)
        if f_ == "npv_mc_parameter_sensitivity.csv":     # wide: one row per parameter setting, P(NPV < 0) by measure
            df_ = df_.pivot_table(index=["parameter", "setting"], columns="measure", values="prob_negative", sort=False).reset_index()
            df_.columns = [c_ if c_ in ("parameter", "setting") else f"P({c_} < 0)" for c_ in df_.columns]
            fm_ = {c_: FMT_PCT for c_ in df_.columns if c_.startswith("P(")}
        v4_table(h_, df_, fm_, nt_)
FCD = OUTPUT_DIR / "forecast"
for f_, h_, fm_ in [("inforce_forecast_summary_annual.csv", "In-force forecast of the existing book (calendar years, 09_inforce_forecast.py)",
                     {c_: FMT_USD for c_ in ["premium", "incurred_pooled", "pooling_charge", "incurred", "paid", "ibnr_runoff"]} | {"loss_ratio": FMT_PCT, "premium_pmpm": FMT_USD2, "incurred_pmpm": FMT_USD2}),
                    ("ae_waterfall.csv", "Actual vs expected waterfall: 2025 actual vs the forecast made at 2024-12 (10_actual_vs_expected.py)",
                     {"amount": FMT_USD, "share_of_expected": FMT_PCT2}),
                    ("forecast_accuracy_by_horizon.csv", "Forecast accuracy by horizon across vintages (11_forecast_vintages.py)", None)]:
    if (FCD / f_).exists():
        v4_table(h_, pd.read_csv(FCD / f_), fm_)
for col_, w_ in zip("ABCDEFGHIJKLMNOPQR", [22, 22, 30, 16, 16, 16, 16, 16, 16, 22, 14, 14, 14, 14, 14, 14, 14, 14]):
    wv.column_dimensions[col_].width = w_

# Final tab order: README, Dashboard, Assumptions, annual + monthly sheet per line, Sensitivity,
# Sens_Calc, Experience, Backtest (reader-facing summaries first, supporting calculations after).
order = ["README", "Dashboard", "Assumptions", "MedSupp_NPV", "MedSupp_Monthly", "MedSupp_Profit",
         "DentalVision_NPV", "DentalVision_Monthly", "DentalVision_Profit",
         "Medicaid_NPV", "Medicaid_Monthly", "Medicaid_Profit", "Sensitivity", "Sens_Calc", "Experience", "Backtest", "V4_Analytics"]
wb._sheets = [wb[n] for n in order]

# openpyxl stores formulas without cached results; force a full recalculation when Excel opens it.
wb.calculation.fullCalcOnLoad = True
out = OUTPUT_DIR / "Humana_NPV_Forecasting_Model.xlsx"
wb.save(out)
print("saved", out)

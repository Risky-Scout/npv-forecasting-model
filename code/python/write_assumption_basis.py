"""Writes config/assumption_basis.json, the assumption basis register: every assumption's status,
basis, sources (exact figures), ASOP references, sensitivity range and link to the synthetic book.
Run after changing npv_assumptions.json, then render_assumption_basis.py. tests/test_v4_model.py
checks the register against the assumption file. Usage: python write_assumption_basis.py [kit root]"""
import json, sys
from pathlib import Path
ROOT = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2])
A = json.load(open(f"{ROOT}/code/python/npv_assumptions.json"))
def get(path):
    x = A
    for k in path.split("."): x = x[k]
    return x
S = {  # sources
 "TR26": {"title": "2026 Medicare Trustees Report, Table II.D1 (CMS OACT)", "url": "https://www.cms.gov/oact/tr/2026", "date": "2026-06-09",
          "figure": "Part B 2026-35: prices 2.0%, volume and intensity 4.4%, beneficiaries 1.7%, total 8.5%"},
 "CSG": {"title": "CSG Actuarial: Medicare Supplement rate increases continue higher trend into 2026", "url": "https://csgactuarial.com/news/average-medicare-supplement-rate-increases-continue-higher-trend-into-2026", "date": "2026-01",
         "figure": "average Med Supp rate increase 10.6% (2025), 11.7% (2026 YTD)"},
 "KFF": {"title": "KFF: Key Facts About Medigap Enrollment and Premiums", "url": "https://www.kff.org/medicare/issue-brief/key-facts-about-medigap-enrollment-and-premiums-for-medicare-beneficiaries/", "date": "2026-10-13",
         "figure": "2025 average Plan G premium $194/month ($2,323/yr), all ages; 4 states incl. GA prohibit attained-age rating"},
 "NAICLR": {"title": "NAIC 2024 Medicare Supplement Loss Ratio Report", "url": "https://content.naic.org/sites/default/files/publication-med-bb-medicare-loss-report.pdf", "date": "2025",
            "figure": "individual loss ratio 84.66% (earned premium $27.74B, incurred $23.49B)"},
 "GENRE": {"title": "Gen Re U.S. Medicare Supplement Survey (2024 data)", "url": "https://www.genre.com/content/dam/generalreinsuranceprogram/documents/surveylhmedsupp25-en.pdf", "date": "2025",
           "figure": "open-block lapse incl. mortality avg 11% (4-21%); first-year commission to writing agent avg 19% (9-29%); open-block loss ratio 87.1%"},
 "VA140": {"title": "14VAC5-170-140 (NAIC Medicare Supplement Model Regulation, compensation)", "url": "https://law.lis.virginia.gov/admincode/title14/agency5/chapter170/section140/", "date": "current",
           "figure": "first-year compensation no more than 200% of second-year compensation"},
 "MLRREG": {"title": "NAIC Model Regulation minimum loss ratios (e.g. Wyo. Code R. 35-14)", "url": "https://www.law.cornell.edu/regulations/wyoming/044-35-Wyo-Code-R-SS-35-14", "date": "current",
            "figure": "65% individual, 75% group"},
 "MACRA": {"title": "NAIC MACRA producer bulletin (Plans C/F)", "url": "https://content.naic.org/sites/default/files/inline-files/cmte_b_senior_issues_related_macra_producer_bulletin_0.pdf", "date": "2019",
           "figure": "newly eligible on or after 2020-01-01 cannot buy Plans C or F"},
 "SSA": {"title": "SSA period life table 2022 (2025 Trustees Report)", "url": "https://www.ssa.gov/oact/STATS/table4c6_2022_TR2025.html", "date": "2025",
         "figure": "q65 M 0.017897 F 0.011018; q85 M 0.100525 F 0.076841 (unisex q65 0.01446, q85/q65 = 6.13 -> 9.1%/yr)"},
 "SOADUR": {"title": "SOA / Milliman Individual Health Durational Study (Wachenheim)", "url": "https://www.soa.org/Files/Research/Projects/IH-Durational-Study_Final-1006.pdf", "date": "2006-10",
            "figure": "major medical claims relative to year 2: yr1 0.801, yr3 1.044 (proxy; not Med Supp)"},
 "WEI": {"title": "SOA Health Watch, Wei (anti-selective lapse)", "url": "https://www.soa.org/4937bb/globalassets/assets/library/newsletters/health-watch-newsletter/2010/february/hsn-2010-iss63-wei.pdf", "date": "2010-02",
         "figure": "healthy lives lapse at a higher rate than impaired lives (direction only)"},
 "PTAX": {"title": "NAIC premium tax rate by line chart", "url": "https://content.naic.org/sites/default/files/model-law-chart-zz-1-premium-tax-rate-by-line.pdf", "date": "2025-12",
          "figure": "GA 2.25%; LA ~2.25%; TX/FL/NY 1.75%; CA 2.35%"},
 "ADA": {"title": "ADA Health Policy Institute: National Dental Expenditures 2024", "url": "https://ada.org/resources/research/health-policy-institute/dental-care-market/national-dental-expenses", "date": "2026-01",
         "figure": "dental spending +4% real 2024; private dental insurance +2.3% real"},
 "SEGAL": {"title": "Nevada PEBP plan year 2027 trend (Segal)", "url": "https://www.pebp.nv.gov/siteassets/content/meetings/2026/march/5.-plan-year-2027-trend-and-rate-setting.pdf", "date": "2026-03-19",
           "figure": "Segal dental benchmark 4.5%; plan adopted 3.5%"},
 "NADP": {"title": "Milliman for NADP: Minimum Dental Loss Ratios", "url": "https://www.nadp.org/wp-content/uploads/2022/09/massachusetts-dlr-report-final.pdf", "date": "2022-06",
          "figure": "individual/small group dental loss ratio ~68.2%; national group 79.1%; sample carrier 23% admin"},
 "EMI": {"title": "EMI Health 2025 Premier PPO Low (individual dental filing)", "url": "https://emihealth.com/pdf/plans/individual/Ut_Premier_PPO_Low.pdf", "date": "2025",
         "figure": "$35/month individual; waiting 6 months basic, 12 months major"},
 "RAND": {"title": "Manning et al., JADA 1985 (RAND Health Insurance Experiment, dental)", "url": "https://hcp.hms.harvard.edu/publication/demand-dental-care-evidence-randomized-trial-health-insurance", "date": "1985-06",
          "figure": "substantial surge in demand in the first year of coverage (direction)"},
 "NHE": {"title": "CMS OACT National Health Expenditure Projections 2025-34", "url": "https://www.cms.gov/files/document/nhe-projections-forecast-summary.pdf", "date": "2026-06-24",
         "figure": "Medicaid spending +5.0%/yr 2025-34; enrollment 81.1M (2025) to 81.8M (2034)"},
 "AHCCCS": {"title": "AHCCCS CYE 2027 preliminary capitation rate letter", "url": "https://azahcccs.gov/shared/Downloads/Reporting/2026/2026_JLBC_PreliminaryCapRateLetter.pdf", "date": "2026-08-13",
            "figure": "PMPM trend 3.0-4.0% plus acuity adjustment +1.0-1.5%"},
 "LARC": {"title": "Louisiana SFY2026 Healthy Louisiana rate certification (Milliman)", "url": "https://ldh.la.gov/assets/medicaid/Contracts-PC/05_18_26/Item31_Attachment_D11b_SFY_2026_Rate_Certification.pdf", "date": "2025-08-18",
          "figure": "capitation excl. directed payments $510.09 (SFY2025) -> $562.98 PMPM"},
 "MACSTATS": {"title": "MACPAC MACStats Exhibits 15 and 22 (FY2023)", "url": "https://www.macpac.gov/wp-content/uploads/2026/01/EXHIBIT-22.-Medicaid-Benefit-Spending-Per-Full-Year-Equivalent-FYE-Enrollee-by-State-and-Eligibility-Group-FY-2023.pdf", "date": "2026-01",
              "figure": "per FYE enrollee: child $4,040, adult ~$7,070, aged/disabled ~$23,849 -> relativities ~0.51-0.54 / 0.90-0.95 / 3.2-3.5"},
 "MILLMCO": {"title": "Milliman: Medicaid managed care financial results 2024 / 2025", "url": "https://www.milliman.com/en/insight/medicaid-managed-care-financial-results-2025", "date": "2026-06-29",
             "figure": "SAP MLR 90.6% (2024), 87.1% (2023); admin 7.7% net of taxes; taxes/fees 2.3%; underwriting result -0.6% (2024), -0.1% (2025); RBC median 414%"},
 "CFR8": {"title": "42 CFR 438.8 (Medicaid MLR)", "url": "https://www.govinfo.gov/content/pkg/CFR-2016-title42-vol4/pdf/CFR-2016-title42-vol4-sec438-8.pdf", "date": "current",
          "figure": "MLR at least 85%; remittance only if the state requires it"},
 "CFR6": {"title": "42 CFR 438.6 (special contract provisions: withholds, incentives, directed payments)", "url": "https://www.law.cornell.edu/cfr/text/42/438.6", "date": "current",
          "figure": "withhold portion must be reasonably achievable; incentives capped at 105% of capitation; directed payments in contracts"},
 "CHURN": {"title": "MACPAC: An updated look at rates of churn in Medicaid and CHIP", "url": "https://www.macpac.gov/wp-content/uploads/2021/10/An-Updated-Look-at-Rates-of-Churn-and-Continuous-Coverage-in-Medicaid-and-CHIP.pdf", "date": "2021-10",
           "figure": "21% disenrolled within 12 months (2018): children 19%, MAGI adults 28%, disabled 9%"},
 "DAMO": {"title": "Damodaran, cost of capital by industry (Jan 2026)", "url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/wacc.html", "date": "2026-01",
          "figure": "Healthcare Support Services cost of equity 7.84%; total market 8.02%"},
 "UST": {"title": "US 10-year Treasury yield", "url": "https://tradingeconomics.com/united-states/government-bond-yield", "date": "2026-11-04", "figure": "4.90%"},
 "NEAM": {"title": "NEAM: 2025 health industry investment highlights", "url": "https://insuranceaum.com/2025-health-industry-investment-highlights-net-investment-income-fell-despite-higher-book-yields", "date": "2026-08",
          "figure": "health industry fixed-income book yield 4.18% (2025)"},
 "IRC11": {"title": "IRC §11(b)", "url": "https://uscode.house.gov/us/usc/119-136/t26/s11/", "date": "current", "figure": "21 percent of taxable income"},
 "H2": {"title": "NAIC Health RBC H2 risk factor exhibits", "url": "https://content.naic.org/sites/default/files/inline-files/4.2_H2%20Risk%20Factor%20Summary%20Exhibits_Embed%20%28FINAL%20REV%29_0.pdf", "date": "2025",
        "figure": "comprehensive medical factor 0.1493 (first $25M), 0.0893 above; proposed Medicaid 0.083-0.148"},
 "MDGUIDE": {"title": "Maryland Insurance Administration: Medicare Supplement Monthly Premiums as of July 1, 2026", "url": "https://insurance.maryland.gov/consumer/documents/publications/medicare-supplement-rate-guide.pdf", "date": "2026-07-01",
             "figure": "Plan G age 65 / 85 (5 attained-age carriers): $300/$515, $253/$500, $233/$440, $240/$427, $230/$391 -> 2.7-3.5%/yr; Plan N/G at 65: 0.65, 0.73, 0.90, 0.85, 0.72 (mean 0.77); HDG/G 0.18 (one carrier)"},
 "MILLAGE": {"title": "Milliman (Clark): Medicare Supplement: Climbing a slippery slope?", "url": "https://www.milliman.com/en/insight/medicare-supplement-climbing-a-slippery-slope", "date": "2019-07-17",
             "figure": "Plan G claim cost relative to age 65: 1.20 at 70, 1.45 at 75 (~3.8%/yr)"},
 "KFFAGE": {"title": "KFF: The Rising Cost of Living Longer (2015, 2011 data)", "url": "https://www.kff.org/788a7bd", "date": "2015",
            "figure": "Part B per-capita spending $1,368 at 66, $2,670 at 83 (~4.0%/yr)"},
 "DENTMEMO": {"title": "DC DISB public dental actuarial memoranda (Security Life of America; Starmount)", "url": "https://disb.dc.gov/sites/default/files/dc/sites/disb/publication/attachments/SLAI-128954476.pdf", "date": "2013",
              "figure": "lapse rate of 30% per year; trend 3% per year; commission 20.0%"},
 "LAWH": {"title": "Louisiana CY2022 Medicaid rate certification; LDH 2019 Quality Year-End Review", "url": "https://www.ldh.la.gov/assets/medicaid/Contracts-PC/11.24.21/AttachmentD_RateCertificationCY2022.pdf", "date": "2021-11-15",
          "figure": "withhold 2.0% of monthly capitation (1% quality + 1% VBP); ~85% of quality withhold earned (2018)"},
 "CMSWH": {"title": "CMS CY2023 Medicare-Medicaid plan quality withhold results", "url": "https://www.cms.gov/files/document/cy-2023-quality-withhold-comprehensive-analysis-results.pdf", "date": "2024",
           "figure": "28-plan average earned back 83.0%"},
 "IRC848": {"title": "IRC §848 (specified policy acquisition expenses)", "url": "https://legalfix.com/uscode/text/26/848", "date": "current",
            "figure": "9.2% of net premiums for other specified contracts; amortized over 180 months"},
 "CBO": {"title": "CBO Budget and Economic Outlook (Jan 2026)", "url": "https://www.cbo.gov/publication/62005", "date": "2026-01", "figure": "PCE inflation 2.7% (2026) to 2.1% (2028)"},
}
ASOP = {
 "56c": "ASOP 56 §3.1 (model and assumptions consistent with the intended purpose)",
 "25": "ASOP 25 §3.3 (relevant experience must be predictive; otherwise not used)",
 "25j": "ASOP 25 §3.3 / §3.4 (no relevant experience: professional judgment; credibility not always precise)",
 "8": "ASOP 8 §3.4 (trend, morbidity, lapse assumptions for health benefits)",
 "8t": "ASOP 8 §3.4.6 (consider historical trends; they may not be the best predictor)",
 "12": "ASOP 12 (risk classification: age, plan, underwriting class)",
 "49": "ASOP 49 §2.1 (actuarially sound: reasonable, appropriate, attainable costs)",
 "49t": "ASOP 49 §3.2.9 (trend excludes adjustments captured elsewhere)",
 "49m": "ASOP 49 §3.2.12 (non-benefit costs, cost of capital and risk margin)",
 "45": "ASOP 45 (risk adjustment: accuracy and completeness of diagnosis coding)",
 "56": "ASOP 56 §3.6.1 (sensitivity testing of key assumptions)",
 "58": "ASOP 58 §3.8 (ERM stress testing; replaces repealed ASOPs 46/47)",
 "41": "ASOP 41 (disclose judgment assumptions and reliance on others)",
 "23": "ASOP 23 (data quality; disclose limitations of public data)",
}
E = []
def _strip(x):
    """A block's value without its documentation strings (keys starting with '_')."""
    return {k: _strip(v) for k, v in x.items() if not k.startswith("_")} if isinstance(x, dict) else x
def add(key, status, basis, src=(), asop=(), sens=None):
    E.append(dict(key=key, value=get(key) if not key.endswith("*") else _strip(get(key[:-1])), status=status, basis=basis,
                  sources=[S[s] for s in src], asop=[ASOP[a] for a in asop], sensitivity=sens))
# ---- global
add("global.discount_rate", "P", "Hurdle rate = cost of equity for a health insurer (DE NPV is to equity): Damodaran 7.84% at a lower risk-free rate; +0.7 pt for the 10-yr Treasury now 4.90%.", ["DAMO", "UST"], ["56", "41"], "7.0%-10.0%")
add("global.expense_inflation", "P", "Per-policy expense growth: CBO PCE inflation 2.1-2.7%, rounded up because admin cost is mostly wages (ECI not retrieved).", ["CBO"], ["56"], "2.5%-4.0%")
add("global.model_version", "M", "Model version tag (approvals are tied to it).")
add("global.features*", "M", "Model mechanics switches, not assumptions; each documented in MODEL_DOCUMENTATION.md.")
add("global.profit_test.federal_income_tax_rate", "S", "US corporate rate.", ["IRC11"])
add("global.profit_test.dac_tax_rate_by_line*", "S", "IRC §848: Med Supp (guaranteed-renewable A&H, other specified contracts) 9.2% of net premiums, amortized over 180 months; D&V (excepted-benefit dental, treated as cancellable) and Medicaid capitation 0. Classification to be confirmed by Corporate Tax: individual dental is often guaranteed renewable, which would make D&V 9.2% too (swept). Simplifications: the §848(c)(2) general-deductions cap is not applied and the tax year is the policy year.", ["IRC848"], ["41"], "D&V 0% / 9.2% (swept)")
add("global.profit_test.investment_earned_rate", "P", "Health industry fixed-income book yield 4.18%; short-duration claim reserves earn less, so 4.0%.", ["NEAM"], ["56"], "3.5%-4.9%")
# ---- Med Supp
add("MEDSUPP.projection_years", "M", "20 years captures nearly all value of an issue-age-65 cohort (persistency below 10% by year 20).")
add("MEDSUPP.cohort_size", "M", "Results per 1,000 policies.")
add("MEDSUPP.issue_age", "P", "Most Med Supp is sold at 65 in the open-enrollment window; Plan G dominates new sales (Gen Re: 68% of new lives).", ["GENRE", "MACRA"], ["12"])
add("MEDSUPP.annual_premium_year1", "P", "Plan G age-65 underwritten new-issue premium; below the all-ages in-force average $2,323 (KFF), consistent with a young, select entrant.", ["KFF"], ["8", "23"], "$1,750-$2,300")
add("MEDSUPP.premium_age_step", "P", "Attained-age premium step: Maryland 2026 Plan G rates from 65 to 85 rise 2.7-3.5%/yr across five carriers. GA, AZ, FL, MO prohibit attained-age rating: issue-age and community variants in medsupp_rating_bases.csv.", ["MDGUIDE", "KFF"], ["12", "56"], "2%-4%")
add("MEDSUPP.annual_rate_increase", "D", "Derived for internal consistency: (1 + trend)(1 + claim age slope)/(1 + premium age step) - 1 = 1.065 x 1.038 / 1.03 - 1 = 7.3%, which holds the ultimate loss ratio level. Industry increases of 10.6-11.7% include catch-up of realized trend and are the upside test.", ["CSG"], ["8", "56"], "5%-12%")
add("MEDSUPP.annual_claim_cost_year1_ultimate", "P", "Ultimate year-1 loss ratio 77.6% for a new issue; market loss ratio 84.7% (NAIC) includes older, deteriorated blocks; Gen Re open blocks 87.1% (66-102%).", ["NAICLR", "GENRE"], ["8", "25"], "loss ratio 75%-88%")
add("MEDSUPP.claim_age_slope", "P", "Plan G claim cost by age: Milliman 1.45 at 75 vs 65 (3.8%/yr); KFF Part B per-capita by age ~4.0%/yr.", ["MILLAGE", "KFFAGE"], ["12", "25"], "3%-5%")
add("MEDSUPP.claim_trend", "P", "Pure cost trend = Part B per-enrollee prices x volume/intensity: 1.020 x 1.044 - 1 = 6.49%. Synthetic experience is not relevant experience for a Humana block (ASOP 25 §3.3), so credibility on it is 0; its indication is a dispositioned REVIEW item.", ["TR26", "CSG"], ["25", "8t", "56"], "5.0%-8.0% (benchmark SD 1.5 pt)")
add("MEDSUPP.selection_factors", "J", "Underwriting wear-off; no Med Supp public table. Major-medical proxy shows year 1 ~0.80 of year 2 and longer wear-off.", ["SOADUR"], ["12", "25j", "56"], "year 1 0.70-0.85; ultimate by year 3-5")
add("MEDSUPP.lapse_by_duration", "P", "Consistent with Gen Re open-block lapse 11% average incl. mortality (range 4-21%): ultimate 8.5% + ~1.2% mortality ~ 10%. Also the synthetic book's experience (not relied on).", ["GENRE"], ["8", "25"], "x0.8-x1.2 (tornado)")
add("MEDSUPP.shock_lapse_add_from_year2", "J", "Extra lapse at the anniversary rate increase; no public magnitude.", [], ["8", "41", "56"], "2%-6%")
add("MEDSUPP.mortality_q65", "P", "SSA 2022 period unisex q65 0.01446 x 75% underwriting selection (judgment for an underwritten insured population) = 0.0108.", ["SSA"], ["25j", "41"], "60%-90% of SSA")
add("MEDSUPP.mortality_growth", "P", "Gompertz growth fitted to SSA unisex q65-q85: ln(6.13)/20 = 9.1%.", ["SSA"], ["25"])
add("MEDSUPP.first_year_commission", "S", "Compensation rule: first year no more than 200% of renewal; 20% = 2 x 10%. Gen Re writing-agent first-year average 19%.", ["VA140", "GENRE"], ["8"])
add("MEDSUPP.renewal_commission", "P", "Typical renewal 10% for years 2-6 (e.g. BCBSIL schedule); level for at least 5 years per the model regulation.", ["VA140"], ["8"])
add("MEDSUPP.premium_tax", "P", "GA 2.25%, LA ~2.25% (illustrative state footprint); national range 1.75%-2.35%.", ["PTAX"], ["8"], "1.75%-2.35%")
add("MEDSUPP.acquisition_cost_per_policy", "J", "Underwriting, issue and marketing per policy; no public basis.", [], ["41", "56"], "+/-30%")
add("MEDSUPP.maintenance_cost_per_policy", "J", "Per-policy admin; no public basis.", [], ["41", "56"], "+/-30%")
add("MEDSUPP.antiselection.shopper_share_at_issue", "J", "Anti-selective lapse is supported in direction only; no published magnitude.", ["WEI"], ["12", "25j", "41"], "30%-70%")
add("MEDSUPP.antiselection.shopper_relative_claim_cost", "J", "As above.", ["WEI"], ["12", "25j"], "0.75-0.95 (tornado)")
add("MEDSUPP.antiselection.shopper_shock_lapse_propensity", "J", "As above.", ["WEI"], ["12", "25j"], "2x-6x")
add("MEDSUPP.dynamic_shock_lapse.reference_rate_increase", "D", "Equals the filed rate-increase assumption, so the base shock is unchanged at the base increase.", [], ["56"])
add("MEDSUPP.dynamic_shock_lapse.shock_lapse_per_unit_rate_increase", "J", "Shock lapse response to the size of the increase; no public basis.", [], ["41", "56"], "0.25-1.0")
add("MEDSUPP.claim_payment_pattern", "J", "Medical-style lag (84% paid by month 2); public figures are illustrative only (~85/95/98% by 1/2/3 months). Production: Humana completion study.", [], ["23", "41"], "+/-1 month")
add("MEDSUPP.required_capital_pct_of_annual_claims", "J", "NAIC Health RBC underwriting-risk factors at a ~400% RBC target (MCO median 414%); Med Supp factor not retrieved.", ["H2", "MILLMCO"], ["58", "41"], "6%-15%")
add("MEDSUPP.model_points.bands", "J", "Issue-age bands and their mean ages, taken from the synthetic book's 2024-25 new issues (a sales-mix placeholder for the Humana sales plan); Plan F excluded under MACRA. No effect on the headline cell.", ["MACRA"], ["12"], "per-cell results in medsupp_model_points.csv: any mix is a weighted sum")
add("MEDSUPP.model_points.plans*", "P", "Premium relativities from Maryland 2026 rates: Plan N 0.77 (mean of five carriers, 0.65-0.90), HDG 0.18 (one carrier, low confidence). Claim relativities set equal to the premium relativities (judgment: same loss ratio by plan).", ["MDGUIDE"], ["12", "41"], "N 0.65-0.90; HDG 0.15-0.35")
add("MEDSUPP.model_points.mix*", "J", "New-business mix by band x plan taken from the synthetic book (a sales-mix placeholder for the Humana sales plan); its Plan G share, 67%, is consistent with Gen Re (68% of new lives). Plan relativities are public (Maryland 2026). No effect on the headline cell.", ["GENRE"], ["12", "41"], "per-cell results in medsupp_model_points.csv: any mix is a weighted sum")
# ---- D&V
add("DENTAL_VISION.projection_years", "M", "10 years: high lapse leaves little in force after year 10.")
add("DENTAL_VISION.cohort_size", "M", "Results per 1,000 policies.")
add("DENTAL_VISION.annual_premium_year1", "P", "Individual dental ~$35/month (EMI filing); $39 incl. vision.", ["EMI"], ["8"])
add("DENTAL_VISION.annual_rate_increase", "D", "3.5%: set equal to the claim trend (v4.3), so filed increases hold the loss ratio level rather than erode the margin every year. The 2013 SLICA memo filed 3% against its own 3% trend: the source supports increases at trend, not 3% against a 3.5% trend. A 2026 individual dental filing (Anthem CA) took no increase, the downside test.", ["DENTMEMO"], ["8", "56"], "0%-5%")
add("DENTAL_VISION.annual_claim_cost_year1_ultimate", "P", "Ultimate loss ratio 70.5%; individual dental ~68%, group 79% (Milliman for NADP).", ["NADP"], ["8", "25"], "loss ratio 60%-80%")
add("DENTAL_VISION.claim_trend", "P", "Dental benchmark 3.5% (Segal 3.5-4.5%; ADA HPI private dental +2.3% real). Credibility 0 on synthetic experience (ASOP 25 §3.3).", ["ADA", "SEGAL"], ["25", "8t"], "2.5%-4.5%")
add("DENTAL_VISION.first_year_pent_up_factor", "J", "Year-1 demand surge supported in direction (RAND HIE); magnitude judgment.", ["RAND"], ["25j", "56"], "1.10-1.40")
add("DENTAL_VISION.lapse_by_duration", "P", "Flat 30% per year from filed individual dental actuarial memoranda (SLICA 2013: \"lapse rate of 30% per year\"). Low confidence (2013).", ["DENTMEMO"], ["8", "25", "56"], "+/-10 pts")
add("DENTAL_VISION.first_year_commission", "J", "15%; 2013 dental memoranda show 20% level; kept within total admin ~23% (Milliman for NADP).", ["DENTMEMO", "NADP"], ["41", "56"], "10%-20%")
add("DENTAL_VISION.renewal_commission", "J", "No public basis.", [], ["41"], "5%-10%")
add("DENTAL_VISION.premium_tax", "P", "GA/LA ~2.25%.", ["PTAX"], ["8"], "1.75%-2.35%")
add("DENTAL_VISION.acquisition_cost_per_policy", "J", "No public basis.", [], ["41"], "+/-30%")
add("DENTAL_VISION.maintenance_cost_per_policy", "P", "Raised from $30 so renewal expense (commission + tax + maintenance ~21%) approaches the ~23% admin ratio of a sample dental carrier (Milliman for NADP).", ["NADP"], ["8", "56"], "$30-$70")
add("DENTAL_VISION.claim_payment_pattern", "J", "As Med Supp; dental pays faster in practice (conservative).", [], ["23", "41"], "+/-1 month")
add("DENTAL_VISION.required_capital_pct_of_annual_claims", "J", "Dental RBC factor not retrieved; lower than medical.", ["H2"], ["58", "41"], "4%-10%")
add("DENTAL_VISION.benefit_categories*", "J", "Category shares from the (synthetic) service mix; neutral category trend and pent-up; no waiting periods, matching the product the experience reflects. Market norm is 6/12-month waiting periods on basic/major (EMI); the engine defers a waited category's pent-up demand to the first 12 months after the wait ends (v4.1), so a waiting-period design can be tested as a product option. The shares have NO effect on the base results (categories are neutral); they scale only the waiting-period options (dv_product_options.csv, MAJOR share 24.9%).", ["EMI"], ["12", "41"], "no effect at base; waiting-period options reported with the MAJOR share")
# ---- Medicaid
add("MEDICAID.projection_years", "M", "10 years: two procurement cycles.")
add("MEDICAID.contract_term_years", "J", "Typical 5-year managed care contract; state-specific.", [], ["49"], "3-7 years")
add("MEDICAID.renewal_probability", "J", "Procurement outcome; no public basis.", [], ["41", "56"], "50%-90% (tornado; consider 0%)")
add("MEDICAID.starting_members", "J", "Contract size; depends on auto-assignment.", [], ["41"], "+/-30%")
add("MEDICAID.annual_new_members", "J", "Joiner volume; depends on auto-assignment and choice enrollment.", [], ["41"], "+/-30%")
add("MEDICAID.annual_disenrollment", "J", "Judgment anchored on a public figure: MACPAC 21% within 12 months (2018), ~19-21% at this cell mix, raised to 25% for more frequent redeterminations from 2027 (the uplift is judgment, not verified). Swept 20%-30% in the tornado (x0.8 / x1.2).", ["CHURN"], ["25", "41"], "20%-30% (tornado)")
add("MEDICAID.capitation_pmpm_year1", "P", "Matches Louisiana SFY2025 capitation excluding directed payments ($510.09 PMPM).", ["LARC"], ["49"])
add("MEDICAID.annual_rate_update", "D", "Equal to the trend assumption: under ASOP 49 §2.1 rates are certified to cover attainable cost, so a permanent rate-below-trend gap is not a defensible expectation. Rate risk is in the tornado and Monte Carlo.", ["LARC"], ["49", "56"], "3%-6%")
add("MEDICAID.medical_cost_ratio_year1", "P", "88%: Milliman MCO MLR 87.1% (2023) to 90.6% (2024); with admin 7.8% and taxes 2.3% it leaves ~1.9% margin, consistent with ASOP 49 margin and industry results.", ["MILLMCO"], ["49m", "56"], "86%-92%")
add("MEDICAID.new_state_acuity_load_year1", "J", "Year-1 unmanaged need in a new state; no public basis.", [], ["25j", "56"], "0%-6%")
add("MEDICAID.claim_trend", "P", "Pure trend 4.5%: rate-certification PMPM trend 3-4% plus acuity (AHCCCS), NHE Medicaid spending +5.0%/yr incl. mix; excludes adjustments modelled elsewhere (ASOP 49 §3.2.9).", ["NHE", "AHCCCS"], ["49t", "25"], "3.5%-5.5%")
add("MEDICAID.mlr_floor", "S", "Federal 85% minimum MLR; remittance is a state contract term.", ["CFR8"], ["49"])
add("MEDICAID.premium_tax", "P", "Taxes and fees 2.3% of revenue (industry average); use the state's actual rate.", ["MILLMCO"], ["49m"], "1.5%-6%")
add("MEDICAID.admin_pmpm_year1", "P", "$40 PMPM = 7.8% of capitation; industry admin 7.7% net of taxes; Medicaid-focused median $46.41.", ["MILLMCO"], ["49m"], "$34-$46")
add("MEDICAID.startup_cost_year1", "J", "Implementation, network, systems; no public basis.", [], ["41", "56"], "$4M-$10M")
add("MEDICAID.rate_cells.cells", "M", "Child / Adult / ABD rate-cell structure.", [], ["49"])
add("MEDICAID.rate_cells.member_share", "J", "Contract mix taken from the synthetic book (placeholder for the state data book); national FYE mix is 35.7/44.8/19.5 (MACPAC). With relativities normalized and equal churn by cell, the mix has no effect on the results; swept against the MACPAC mix.", ["MACSTATS"], ["49"], "MACPAC national mix (swept: no effect at equal churn)")
add("MEDICAID.rate_cells.capitation_relativity", "P", "Consistent with MACPAC per-FYE spending relativities (0.51-0.54 / 0.90-0.95 / 3.2-3.5; MACPAC includes LTSS often carved out).", ["MACSTATS"], ["49"])
add("MEDICAID.rate_cells.loss_ratio_relativity", "J", "1.0: rates certified per cell (ASOP 49 §3.2.2) target the same loss ratio.", [], ["49"], "0.95-1.05 by cell")
add("MEDICAID.rate_cells.disenrollment_relativity", "P", "MACPAC 12-month churn by eligibility group, children 19%, MAGI adults 28%, disabled 9%, each divided by the all-enrollee 21%: 0.905 / 1.333 / 0.429 (v4.3; was 1.0 for parsimony). The engine normalizes so the blended rate stays 25%. Material: high-capitation ABD members stay longer, so the mix drifts toward them. Replace with the state data book.", ["CHURN"], ["41"], "equal churn by cell (swept)")
add("MEDICAID.risk_adjustment.plan_acuity", "J", "1.0 = state average (budget neutral).", [], ["45"], "0.97-1.03 (tornado)")
add("MEDICAID.risk_adjustment.revenue_capture", "J", "Share of acuity difference recognized; coding completeness.", [], ["45", "41"], "60%-100%")
add("MEDICAID.claim_payment_pattern", "J", "As Med Supp.", [], ["23", "41"], "+/-1 month")
add("MEDICAID.required_capital_pct_of_annual_claims", "J", "NAIC H2 factors 0.083-0.149 at a ~400% RBC target (MCO median 414%) before covariance and managed-care credit.", ["H2", "MILLMCO"], ["58", "41"], "10%-25%")
add("MEDICAID.contract_terms.quality_withhold.withhold_pct", "P", "2.0% of capitation (Louisiana: 1% quality + 1% VBP); NC 1.5%; TX 3% at risk.", ["LAWH", "CFR6"], ["49"], "1%-3%")
add("MEDICAID.contract_terms.quality_withhold.expected_earn_back", "P", "85%: LDH quality withhold ~85% earned (2018); CMS MMP average 83.0%.", ["LAWH", "CMSWH"], ["49", "56"], "60%-100%")
add("MEDICAID.contract_terms*", "J", "Directed payments 0, no acuity path, MLR floor rule (corridor parameters inactive): contract-specific under 42 CFR 438.6; set from the state contract. The acuity path is swept; directed payments are a pass-through; the corridor branches are tied out in Excel at non-neutral settings.", ["CFR6"], ["49", "41"], "acuity path 1.00 / 1.04 / 1.03 with a 1-year RA lag (swept)")
# ---- Monte Carlo
add("monte_carlo.n_sims", "M", "Simulation size (sampling error of the mean < 1% of NPV).")
add("monte_carlo.seed", "M", "Reproducibility.")
add("monte_carlo.medicaid_renewal_prob", "D", "Equals MEDICAID.renewal_probability.", [], ["56"])
add("monte_carlo.trend_shock_sd", "J", "Parameter uncertainty in long-run trend: benchmark SDs 1.0-1.5 pt (trend_benchmarks.json); calibrate with estimate_mc_parameters.py.", [], ["56", "58"], "0.5-1.5 pt (swept in npv_mc_parameter_sensitivity.csv)")
add("monte_carlo.lapse_multiplier_sd", "J", "Gen Re lapse range 4-21% around 11% supports a wide spread.", ["GENRE"], ["56"], "0.10-0.25 (swept in npv_mc_parameter_sensitivity.csv)")
add("monte_carlo.medsupp_rate_increase_deviation_sd", "J", "Approval uncertainty; calibrate from filing history.", [], ["56"], "0.5-1.5 pt (swept in npv_mc_parameter_sensitivity.csv)")
add("monte_carlo.medicaid_rate_update_sd", "J", "Rate-setting uncertainty.", [], ["56"], "0.5-1.5 pt (swept in npv_mc_parameter_sensitivity.csv)")
add("monte_carlo.correlations*", "J", "Judgment correlations; trend / Med Supp rate correlation 0 because the explicit filing response models it.", [], ["56", "58"], "trend / Medicaid update correlation 0-0.8 (swept in npv_mc_parameter_sensitivity.csv)")
add("monte_carlo.process_risk*", "J", "Year-to-year volatility; calibrate with estimate_mc_parameters.py.", [], ["56", "58"], "yearly trend SD 0.5-1.5 pt, AR(1) phi 0.25-0.75 (swept in npv_mc_parameter_sensitivity.csv)")
add("monte_carlo.management_response*", "J", "Rate filings follow experience with a lag of 2 years for both lines (a filing effective at the start of year s+1 is prepared during year s from experience through year s-1, so a 1-year lag would use claims not yet known). Observed Med Supp increases (10.6-11.7%) exceeding projected trend show filings respond to realized claims. Strength is judgment (attribution shows 0-0.75).", ["CSG"], ["56", "41"], "gap closure per filing 0 / 0.25 / 0.75 (npv_monte_carlo_attribution.csv)")
# ---- link to the synthetic book (review finding, v4.1): which shipped values come FROM the synthetic book
# ("derived": distribution inputs read off it) and which EQUAL the synthetic generator's own value
# ("equals_generator": retained because the cited public figure supports it; the public source is then a
# reasonableness check, not the derivation). tests/test_v4_model.py recomputes the second list from
# data/true_assumptions.json, so it cannot go stale.
DERIVED = {"MEDSUPP.model_points.bands", "MEDSUPP.model_points.mix*", "DENTAL_VISION.benefit_categories*", "MEDICAID.rate_cells.member_share"}
T = json.load(open(f"{ROOT}/data/true_assumptions.json"))
GEN = {"MEDSUPP.annual_premium_year1": 12 * T["MEDSUPP"]["base_premium_pm_age65"],
       "MEDSUPP.annual_claim_cost_year1_ultimate": 12 * T["MEDSUPP"]["base_claim_pmpm_age65"],
       "MEDSUPP.premium_age_step": T["MEDSUPP"]["premium_age_slope"], "MEDSUPP.claim_age_slope": T["MEDSUPP"]["claim_age_slope"],
       "MEDSUPP.annual_rate_increase": T["MEDSUPP"]["annual_rate_increase"], "MEDSUPP.claim_trend": T["MEDSUPP"]["annual_claim_trend"],
       "MEDSUPP.selection_factors": T["MEDSUPP"]["selection_factor_by_duration"], "MEDSUPP.lapse_by_duration": T["MEDSUPP"]["annual_lapse_by_duration"],
       "MEDSUPP.shock_lapse_add_from_year2": T["MEDSUPP"]["shock_lapse_add_january"],
       "DENTAL_VISION.annual_premium_year1": 12 * T["DENTAL_VISION"]["premium_pm"], "DENTAL_VISION.annual_rate_increase": T["DENTAL_VISION"]["annual_rate_increase"],
       "DENTAL_VISION.annual_claim_cost_year1_ultimate": 12 * T["DENTAL_VISION"]["base_claim_pmpm"], "DENTAL_VISION.claim_trend": T["DENTAL_VISION"]["annual_claim_trend"],
       "DENTAL_VISION.first_year_pent_up_factor": T["DENTAL_VISION"]["first_year_pent_up_factor"], "DENTAL_VISION.lapse_by_duration": T["DENTAL_VISION"]["annual_lapse_by_duration"],
       "MEDICAID.annual_rate_update": T["MEDICAID"]["annual_rate_update"], "MEDICAID.claim_trend": T["MEDICAID"]["annual_claim_trend"],
       "MEDICAID.annual_disenrollment": round(1 - (1 - T["MEDICAID"]["monthly_disenrollment"]) ** 12, 3),
       "MEDSUPP.claim_payment_pattern": T["claim_payment_lag_probs"], "DENTAL_VISION.claim_payment_pattern": T["claim_payment_lag_probs"],
       "MEDICAID.claim_payment_pattern": T["claim_payment_lag_probs"]}
def _eq(x, y):
    if isinstance(x, list) or isinstance(y, list):
        return isinstance(x, list) and isinstance(y, list) and len(x) == len(y) and all(_eq(a, b) for a, b in zip(x, y))
    return abs(float(x) - float(y)) <= 0.02 * max(abs(float(y)), 1e-9) + 1e-12   # within 2%
for e in E:
    if e["key"] in DERIVED:
        e["synthetic_link"] = "derived"
    elif e["key"] in GEN and _eq(e["value"], GEN[e["key"]]):
        e["synthetic_link"] = "equals_generator"
json.dump({"_readme": ("Assumption basis register (v4.2). One entry per assumption in code/python/npv_assumptions.json (a key ending in * covers the whole block; its value is the block without documentation strings). "
  "status: P = public source, S = statute / regulation, D = derived for internal consistency, J = judgment (needs a sensitivity range), E = company experience (none shipped), M = model setting. "
  "Policy: the synthetic book is not relevant experience for a Humana block (ASOP 25 §3.3), so no credibility is given to its experience. synthetic_link discloses the exceptions: 'derived' = a distribution input read off the synthetic book (sales mix, category shares, Medicaid cell mix); 'equals_generator' = the value equals the synthetic generator's own and is retained because the cited public figure supports it, so the source is a reasonableness check rather than the derivation. tests/test_v4_model.py fails if any assumption is missing here, "
  "if a value differs from the documented one, if a P/S entry has no source, or if a J entry has no sensitivity range. docs/ASSUMPTION_BASIS.md is generated from this file."),
  "entries": E}, open(f"{ROOT}/config/assumption_basis.json", "w"), indent=1)
print(len(E), "entries")

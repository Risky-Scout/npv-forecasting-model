# Data Request — inputs Humana provides for the first production run

The model reads four extracts (data) and a set of external inputs (decisions and parameters).
Column names below are the *canonical* names; Humana's own names and codes are mapped in
`config/source_mapping.json` (template: `config/source_mapping.production_example.json`).
Member-level files are PHI: deliver them to the governed landing Volume only.

## A. Extracts (per line of business: Med Supp, Dental & Vision, Medicaid)

**History:** at least 48 months of incurred experience, plus all payments through the paid-through
month. The extract must stop at the paid-through month set in `config/run_config.json`: the data
contract stops the run if the latest paid month differs. **Format:** CSV or Parquet, one file per feed.

### A1. Members (one row per member)
| Field | Type | Definition |
|---|---|---|
| member_id | integer | Stable member key, the same in every feed |
| lob | code | Line of business (mapped to MEDSUPP / DENTAL_VISION / MEDICAID) |
| state | code | State of residence (rating state) |
| plan | code | Med Supp plan letter (G, N, F, HDG, …); D&V product; Medicaid program |
| rate_cell | code | Medicaid rate cell (blank for other lines) |
| issue_month | YYYY-MM | Coverage effective month (Med Supp policy anniversary basis) |
| issue_age | integer | Age at issue |
| term_month | YYYY-MM | Last covered month (blank if in force) |
| term_reason | code | Voluntary lapse / death / disenrollment |

### A2. Member months (one row per member per covered month)
member_id, lob, month, state, plan, rate_cell, premium (billed premium or capitation for the month).

### A3. Claims (one row per claim or claim line summary)
claim_id, member_id, lob, incurred_month, paid_month, paid_amount (net of reversals and recoveries;
reversals as negative rows), service_category.

### A4. Finance control totals
control (paid_claims / premium), lob, month, amount — from the general ledger for the same period,
plus the list of known reconciling items (manual journal entries, settlements, timing).

## B. External inputs (owner in brackets)

| Input | Used for |
|---|---|
| Hurdle rate by line [Finance] | Discount rate |
| Capital allocation / RBC target by line [Capital management] | Required capital % |
| Portfolio earned rate [Investments] | Investment income in the profit test |
| Tax rate and DAC tax treatment [Corporate Tax] | Profit-test tax |
| Med Supp age / sex / duration / plan claim-cost curves [Med Supp pricing] | Trend normalization (`config/claim_cost_curves.json`) |
| Rate-filing plan and approval history by state [Med Supp pricing] | Rate increase, dynamic lapse slope |
| Post-rate-action claims A/E and shopping data [Med Supp pricing] | Anti-selection calibration |
| Commission schedules, acquisition and maintenance unit costs [Distribution, Finance] | Expense assumptions |
| State rate certification, data book, contract terms (MLR rules, risk adjustment, directed payments) [Medicaid actuarial] | Medicaid assumptions and rate cells |
| Data dictionary and named data owner per feed [Data engineering] | Mapping and contract |
| **v4:** Pricing trend assumptions by line on the normalized basis, with a plausible range [Med Supp, D&V pricing; Medicaid actuarial: state certification trend] | Trend credibility blend: replaces the public benchmarks with Humana pricing trends where they differ (`config/trend_benchmarks.json`, public sources in v4.1) |
| **v4:** Filed rate relativities by issue age and plan, and the morbidity age curve [Med Supp pricing] | Med Supp model points; the sign of the portfolio NPV depends on these |
| **v4:** D&V claim-cost curves by duration and product; waiting periods and benefit design [D&V pricing, Product] | D&V trend normalization; benefit categories |
| **v4:** Contract terms for the new state: quality withhold and earn-back history, directed payments, MLR rule (floor or corridor and its bands), expected acuity path after redeterminations [Medicaid actuarial] | `MEDICAID.contract_terms` |
| **v4:** New-sales plan by line and month [Finance / FP&A] | In-force forecast (`config/forecast_config.json`), replacing the synthetic trailing-12-month-trend default |
| **v4:** Planning scenarios used in the financial plan [Finance / FP&A] | `config/planning_scenarios.json` |
| **v4:** Annual history: realized cost trend by line, Med Supp assumed vs approved increases, Medicaid rate updates (at least 6 years) [Pricing, Medicaid actuarial] | `estimate_mc_parameters.py` → Monte Carlo volatilities, correlations |
| **v4:** Persistent Volume path for the forecast vintage registry [Platform team] | `NPV_VINTAGE_DIR` |

## C. Acceptance checks on delivery
1. `00a_ingest_source_extracts.py` passes (all columns and codes mapped, months parse).
2. `01b_controls_gate.py` passes (contract, data gate, ledger tie-out), or each finding has an
   approved exception or reconciling item.
3. Record counts and dollar totals agree with the data owner's delivery note.

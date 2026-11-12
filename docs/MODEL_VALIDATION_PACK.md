# Model validation pack — NPV Forecasting Model v4.3.0

For the independent reviewer (model risk). It lists what was tested, where the evidence is, and
what remains unvalidated. Every result can be reproduced with `python run_all.py` (synthetic
mode, about 17 minutes) unless marked *offline*.

## 1. Model inventory entry (proposed)

| Field | Entry |
|---|---|
| Name / version | NPV Forecasting Model 4.3.0 |
| Uses | New-business NPV and risk-based price / bid recommendations (Med Supp, D&V, Medicaid); in-force management forecast and A/E explanation for the existing book |
| Not for | Reserving, rate filings, capital planning, financial reporting |
| Implementations | Python (production engine); Excel workbook (independent formula rebuild, ties to the cent); Databricks notebook (Med Supp base cell, ties to the cent) |
| Inputs | Four data extracts (DATA_REQUEST.md §A); external inputs (§B); approved assumption set and governed configs (hash-gated); assumption basis register (`config/assumption_basis.json`) |
| Owner / approver / reviewer | To be named (MODEL_DOCUMENTATION.md §9) |
| Suggested tier | High: material pricing decisions; long-duration projections; assumption-driven |

## 2. Evidence catalogue

| # | Area | Test | Evidence (file) | Result on the shipped synthetic book |
|---|---|---|---|---|
| 1 | Data | Contract, data gate, ledger tie-out stop the run on failure | `01b_controls_gate.py`, `output/controls/*`, `tests/test_phase1_controls.py` | PASS; the 5 planted problems are cleaned or covered by exceptions (DQ-0142 renewed to 2027-06-30; `test_expired_exception_fails` proves expiry still stops the gate) |
| 2 | Data | Strict ingest; mapping changes layout, never values | `tests/test_v3_production.py`, `tests/e2e_production_run.py` | PASS (two phases) |
| 3 | Implementation | Excel rebuild = Python, approved set | `05_reconcile_python_to_excel.py`, `output/controls/reconciliation.json` | Ties to the cent (cash, DE including the DAC tax schedule, years, 17 sensitivities; 66,860 formulas, 0 errors) |
| 4 | Implementation | **Excel rebuild = Python, non-neutral v4 settings**, three variants covering every settlement branch (corridor loss side, corridor gain side, floor binding with earn-back) | `tests/e2e_excel_nonneutral.py` | Ties to the cent in all three |
| 5 | Implementation | **Excel rebuild = Python at the v4.2 recommended premiums**; the DE NPV equals the pricing table | `tests/e2e_excel_pricing.py` | Ties to the cent; DE matches to $0.01 |
| 6 | Implementation | Notebook base cell = engine | `code/databricks/npv_pipeline_databricks.py` | Ties to the cent |
| 7 | Regression | v3 features off = v2; v4 features neutral = v3 | `tests/test_v3_model.py`, `tests/test_v4_model.py` | PASS (Medicaid compared at neutral contract terms; the shipped 2% withhold is tested separately) |
| 8 | Mechanics | Profit-test identities; tax cost = PV of tax; DAC tax weights and PV; withhold timing identity; corridor hand calculation; directed-payment pass-through; lagged risk adjustment; waiting-period deferral; filing-response identity; rating bases at equal PV loss ratio; break-even position | `tests/test_v3_model.py`, `tests/test_v4_model.py` | PASS |
| 9 | Pricing | Risk-based solve: τ-quantile of DE = 0 at the solved minimum; P(DE < 0) ≤ 10% at the recommendation; 65% minimum loss ratio cap; Medicaid bid rule; competitiveness flags | `tests/test_v4_model.py`, `output/npv/npv_pricing_solve.csv`, `medsupp_cell_pricing.csv` | PASS. Med Supp base cell meets the target as priced (portfolio 28.0% → 3.1% at recommended cell prices); D&V +7.1% (P = 9.9%); Medicaid: do not bid at these terms |
| 10 | Mechanics | Event-level backtest (Test A), simulator implements every Medicaid contract term | `output/backtest/test_a_*` | z = +1.11 / +0.75 / −0.49 |
| 11 | Estimation | NPV error from estimating assumptions, against the generator's truth (Test B) | `output/backtest/test_b_estimation_error.csv` | Approved public Med Supp trend −$261,650 vs the generator's 6.0%; D&V $0. Measures only values that differ from the generator's (13 equal it) |
| 12 | Estimation | **Trend estimator study** (30 seeds): bias, SE calibration, blend vs raw | `docs/validation/trend_estimator_study.md` (*offline*) | Normalized fits unbiased; bootstrap SE 0.86–1.09 of true SD, inflated by 1.16 in 03b; blend RMSE below raw on every line |
| 13 | Forecast | BF / chain-ladder identities; roll-forward conservation; waterfall steps against independent calculations (the sum holds by construction); vintage reconstruction | `tests/test_v4_forecast.py`, asserts in 09–11 | PASS |
| 14 | Forecast | Accuracy by horizon and cross-vintage bias | `output/forecast/forecast_accuracy_by_horizon.csv`, `forecast_bias_test.csv` | Incurred PMPM MAPE 2.3–6.7%; no persistent bias on any line or metric (12 tests); on the generator's own curves and payment pattern |
| 15 | Risk | Process vs parameter risk (Test C); Monte Carlo attribution and response sensitivity | `output/backtest/test_c_*`, `output/npv/npv_monte_carlo_attribution.csv` | Ratios 3.1 / 1.0 / 5.5; Med Supp P(cash NPV < 0) 0.15–18.6% across the response strength; P(DE < 0) 0.3–8.6% across the volatilities (`npv_mc_parameter_sensitivity.csv`) |
| 16 | Governance | Unapproved or undispositioned sets refused in production. The engine re-checks the hash AND this run's 03b status. Governed configs are in the approval hash. The manifest withholds results unless the gate approved | `tests/test_v3_production.py`, `tests/test_v3_model.py`, `tests/test_v4_model.py` | PASS |
| 17 | Assumptions | Every NPV assumption documented (public / statute / derived / judgment with range / setting); register matches the file; no pricing credibility to synthetic experience; synthetic-linked values disclosed | `config/assumption_basis.json`, `docs/ASSUMPTION_BASIS.md`, `tests/test_v4_model.py` | PASS: 89 entries, 40 judgment; block values checked; 17 synthetic-linked values flagged and recomputed from the generator's file |
| 18 | Assumptions | Judgment values across their documented ranges (ASOP 56 §3.6.1) | `output/npv/npv_judgment_sensitivity.csv` | 32 sweeps (every judgment value is run here or in the tornado, Monte Carlo parameter table, response attribution or per-cell model points). Med Supp DE positive throughout; D&V negative except at pent-up 1.10 (+$4,865); Medicaid changes sign within 3 ranges (start-up, risk-adjustment capture, equal churn by cell) |
| 19 | Monitoring | Drift alerts | `output/monitoring/drift_alerts.csv` | 4 ALERTs as designed: 3 Medicaid redetermination wave; D&V below DE break-even |
| 20 | Deployment | Databricks job definition validated and its 17 tasks executed locally in dependency order, production mode | `tests/e2e_databricks_job_local.py` | PASS; 6 placeholders for the platform team |

## 3. Findings the reviewer should challenge first

1. **Trend** (largest driver; 0.94 pt of DE break-even headroom on Med Supp). Check:
   - the benchmark sources (public: Trustees 2026, ADA HPI / Segal, CMS NHE / AHCCCS; national,
     not Humana-specific);
   - the normalization curves;
   - the 1.16 bootstrap SE inflation (from the seed study).
2. **D&V is below break-even** on market inputs (needs +4.3% premium). Check the premium source
   (one EMI filing), the 30% lapse (2013 memoranda) and the pent-up judgment. One at a time, DE
   breaks even at a 4.3% higher premium, a 4.9% lower claim level, a pent-up factor of 1.12
   (range 1.10–1.40) or lapse at 62% of the assumption.
3. **Medicaid DE is positive (+$2.71M) only because churn differs by rate cell.** At equal churn
   it is −$1.09M (a $3.80M swing). Check the MACPAC churn relativities against the state data
   book, and the flat admin PMPM across cells that adds to the gain. Then check required capital
   (15%, judgment from NAIC H2 factors), the withhold earn-back and the start-up cost, the
   largest judgment swing ($4.74M). The chance of a loss is still 47.2%.
4. **The filing response** cuts Med Supp tail risk sharply, and its strength is judgment. Check it
   against filing history (requested, approved, indicated).
5. **Anti-selection** is the largest Med Supp assumption with no experience basis. Check that it
   is not double-counted with the select factors: claim relativities average 1.0 at issue, so it
   changes only the mix left after shock lapses (MODEL_DOCUMENTATION.md §4.2).
6. **DAC tax classification** (Med Supp 9.2%; D&V and Medicaid 0) needs Corporate Tax confirmation (D&V at 9.2%: DE −$8,894 more).
7. **Values linked to the synthetic book** (ASSUMPTION_BASIS.md). Check that the 4 placeholders
   don't affect the decision being made, and that each of the 13 generator-equal values is
   supported on its own merits by the cited figure or range.

8. **The pricing recommendation.** Check the 10% risk tolerance against Humana's pricing policy;
   the D&V +7.1% against competitor filings (it is 7.1% above the one public reference); the
   High-Deductible G loss-ratio cap (three of four cells still lose money at the cap); and the
   Medicaid "do not bid" call. That call compares each lever with public ranges one at a time;
   its DE is positive only at MACPAC churn by rate cell, and even there the chance of a loss is
   47.2%. The Med Supp "keep the price" call relies on the filing-response strength (24.7%
   with no response).

## 4. Not validated here (and why)

| Item | Why | What closes it |
|---|---|---|
| Any assumption against Humana experience | No Humana data outside Humana | First production cycle: indications, A/E, vintages |
| Databricks runtime, Unity Catalog and cluster policies | No Humana workspace (the job definition IS validated and executed locally) | Dry run in a non-production schema (RUNBOOK §2, item 9) |
| SAS | Retired; no licence | Port and tie out to the cent, if SAS is required |
| Profit-test tax and reserve simplifications; DAC classification | Need Corporate Tax and valuation input | MODEL_DOCUMENTATION.md §8 |

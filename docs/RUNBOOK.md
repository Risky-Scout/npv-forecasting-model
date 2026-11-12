# Runbook — deploying and operating the NPV model at Humana

## 1. Environments
- **Release validation** (any machine with Python 3.12+, verified on 3.13; LibreOffice; Java 17+): `python run_all.py`
  in synthetic mode, then `python tests/e2e_production_run.py`. Both must pass for every model
  release. This is where the backtest runs. Locally PySpark 4.2.1 warns that pandas 3.x is not
  fully supported; the notebook tie-out still passes to the cent, and on Databricks the Spark
  notebook runs on the runtime's own pandas (its job cluster installs no extra libraries).
- **Production cycle** (Databricks Workflow, `deploy/databricks_job.json`): production mode on Humana
  extracts. Code and configuration in a Databricks Git folder; data and outputs on a Unity Catalog
  Volume through `NPV_DATA_DIR` / `NPV_OUTPUT_DIR`.

## 2. Before the first production run (go-live checklist)

| # | Item | Owner | How to verify |
|---|---|---|---|
| 1 | Deliver extracts per DATA_REQUEST.md to the landing Volume | Data engineering | Delivery note: counts and totals |
| 2 | Write `config/source_mapping.json` from the example: real columns, codes, formats, paths | Model owner + data owner | Ingest passes on a sample; rerun `tests/test_v3_production.py` with the real layout |
| 3 | Update `config/data_contract.json` owners and allowed values; `dq_policy.json` tolerances; exception register | Model owner | Reviewed change |
| 4 | Replace synthetic ledger totals with the GL extract; register known reconciling items | Finance | Tie-out passes |
| 5 | Provide external inputs (DATA_REQUEST.md §B) and replace `config/claim_cost_curves.json` | Owners listed | Values recorded with source in `npv_assumptions.json` |
| 6 | Run the studies, review `assumption_indications.csv`, set the assumptions and update `config/assumption_basis.json` (re-render `docs/ASSUMPTION_BASIS.md`), approve: add the hash from `03b_assumption_governance.py --hash` (it covers the governed configs too) to `config/assumption_approvals.json` | Responsible actuary | Gate prints APPROVED; register test passes |
| 7 | Set `run_config.json`: mode = production, run id, run date, paid-through and experience dates | Model owner | Reviewed change |
| 8 | Create the Volume, set `NPV_DATA_DIR` / `NPV_OUTPUT_DIR` on both job clusters; grant access to the experience-study group only | Platform team | `tests/e2e_production_run.py` passes on the cluster |
| 9 | Import the job template (17 tasks, first `start_run`; validated and executed locally by `tests/e2e_databricks_job_local.py`); set `NPV_VINTAGE_DIR` to a persistent Volume path outside the cycle output; fill the 6 placeholders the local test lists (runtime, node type, policy, catalog, vintage schema, notification list); check `deploy/requirements-databricks.txt` against the runtime (it omits pyspark on purpose); allow the LibreOffice init script, or run the three workbook tasks on the validation machine | Platform team | Dry run in a non-production schema |
| 10 | Independent model review (MODEL_DOCUMENTATION.md, MODEL_VALIDATION_PACK.md, validation evidence, code) | Model risk | Sign-off table §9 |
| 11 | **v4.1:** Review the public trend benchmarks (`config/trend_benchmarks.json`) against Humana pricing trends; load Humana experience so the credibility blend can weight it; rerun 03b and re-approve | Responsible actuary | Indications cite the source; approval notes record it |
| 12 | **v4:** Provide filed rate relativities by issue age and plan; review the Med Supp model-point table before using the base cell as the headline | Med Supp pricing | `output/npv/medsupp_model_points.csv` reviewed |
| 13 | **v4:** Enter the new state's contract terms (withhold, directed payments, MLR rule, acuity path); run `tests/e2e_excel_nonneutral.py`-style tie-out on the real terms | Medicaid actuarial | Reconciliation PASS on the contract's terms |
| 14 | **v4:** Calibrate Monte Carlo process risk and the filing response with `estimate_mc_parameters.py` on history | Forecasting | Proposed values recorded in the approval |
| 15 | **v4:** Load the Finance new-sales plan into `config/forecast_config.json` | Finance / FP&A | Forecast new sales match the plan |
| 16 | **v4.1:** Confirm the IRC §848 classification and rates by line (`global.profit_test.dac_tax_rate_by_line`) | Corporate Tax | Recorded in the assumption basis |
| 17 | **v4.1:** Review the DQ exception register; renew or close DQ-0142 (Kentucky, renewed in the synthetic example to 2027-06-30) | Data owner | Gate PASS on the real data |
| 18 | **v4.2:** Set the pricing risk tolerance (`config/pricing_targets.json`, default 10%) to Humana's corporate pricing policy, replace the public market references with competitor rate surveys, and re-approve (the file is in the approval hash) | Pricing leadership, responsible actuary | Approval notes record the tolerance |
| 19 | **v4.2:** Review `npv_pricing_solve.csv` and `medsupp_cell_pricing.csv` before any rate filing or bid: REVIEW flags, High-Deductible G at the loss-ratio cap, the Medicaid bid recommendation | Med Supp / D&V pricing, Medicaid actuarial | Signed pricing memo |

## 3. Each production cycle
1. Data lands; the scheduler (paused by default) or an operator starts the job with the cycle.
2. Ingest → SQL kit → controls gate. **On FAIL:** read `output/controls/*_report.csv`; fix the feed
   with the data owner, or add an approved exception / reconciling item (never edit the data).
3. Studies → assumption governance. **On NOT APPROVED:** the assumption file changed; review the
   indications and approve, or revert the change.
4. Engine → in-force forecast → A/E waterfall → forecast vintages → workbook → recalculation →
   reconciliation. **On reconciliation FAIL:** do not publish; the Excel and Python
   implementations disagree, which is a model defect.
5. Review, in this order:
   - drift alerts (`output/monitoring/drift_alerts.csv`);
   - the A/E waterfall (`output/forecast/ae_waterfall.csv`): explain each component above
     materiality;
   - forecast accuracy and bias by horizon;
   - the Dashboard and the V4_Analytics sheet;
   - the bridge (`npv_bridge_v2_to_v4.csv`), tornado, break-even headroom, Monte Carlo attribution;
   - the v4.2 pricing recommendation (`npv_pricing_solve.csv`, `medsupp_cell_pricing.csv`).
6. A persistent forecast bias or an ALERT on A/E goes to the assumption review with its cause
   (persistent, credible, explainable: SQL 06 header). Do not change an assumption on a WATCH.
7. Sign-off with `output/run_manifest.json` attached; archive the outputs by cycle **before** the
   next run, because every run starts by clearing the kit's previous artefacts (`00s_start_run.py`).
   The vintage registry (`NPV_VINTAGE_DIR`) is never cleared: back it up, do not archive-and-delete it.

## 4. Changing the model
Code, configuration and assumption changes go through a reviewed pull request. A model release
reruns the release validation (§1); a new model version needs new assumption approvals (approvals
are tied to `global.model_version`). Never edit an approval; add a new one.

## 5. Scope and reliance (known, not hidden)
These items need Humana data, a Humana workspace or another Humana function, and each is on the
go-live checklist (§2):
- The Databricks job definition is validated and executed locally task by task, but has not run
  in a Humana workspace: runtime pins (`deploy/requirements-databricks.txt`), Unity Catalog
  permissions and cluster policies are confirmed by the dry run (item 9).
- Trend benchmarks are public national sources, not Humana pricing trends. Until Humana
  experience is loaded, the trend indications demonstrate the method; they are not a Humana view
  of trend (item 11).
- 40 assumptions are judgment, including anti-selection, the dynamic lapse slope, risk-adjustment
  capture, Medicaid loss-ratio relativities by rate cell, Monte Carlo correlations, process risk and the
  filing-response strength. Each is run across its range; calibrate on Humana data (items 12–14).
- 17 values are linked to the synthetic book (ASSUMPTION_BASIS.md): replace the placeholders with
  the Humana sales plan and state data book, and confirm the generator-equal values against
  Humana studies (items 5, 11–13).
- Profit-test simplifications (MODEL_DOCUMENTATION.md §8) and the DAC tax classification need
  Corporate Tax and valuation review before DE NPV is used beyond pricing (item 16).
- SAS programs are retired (v2 mirror, never executed; `code/sas/README.md`). Python is the
  production engine; the Excel rebuild is the independent check.

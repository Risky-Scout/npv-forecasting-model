# Detailed results and kit reference (version 4.4.0)

Built for the Humana Associate Actuary, Analytics/Forecasting role. It has two parts:

- **New-business NPV models** for Medicare Supplement, Dental & Vision and Medicaid, with an
  after-tax distributable-earnings profit test, Monte Carlo, reverse stress and an Excel rebuild
  that ties to the cent.
- **The monthly forecasting layer** an Analytics/Forecasting team runs on the existing book:
  in-force forecast, actual-vs-expected waterfall, forecast-accuracy tracking by vintage, drift
  alerts.

Start with [docs/EXECUTIVE_SUMMARY.md](EXECUTIVE_SUMMARY.md) (two pages).

> **Status.**
> - **Built and tested end to end**, in synthetic and production modes. The Databricks Workflow
>   definition is validated and executed locally task by task in its declared order. It has not
>   run in a Humana workspace, because there isn't one here.
> - **No Humana data is used anywhere.** The data is a synthetic practice book with planted data
>   problems.
> - **Every NPV assumption has a documented basis** in
>   [docs/ASSUMPTION_BASIS.md](ASSUMPTION_BASIS.md), and a test enforces it. There are 89
>   register entries covering every value:
>   - **public source** (30), e.g. Medicare Trustees, CMS NHE, MACPAC, SSA, NAIC, Milliman,
>     Gen Re, Segal, ADA HPI, state rate guides and rate certifications, Damodaran, NEAM
>   - **statute or regulation** (4): IRC §11 and §848, NAIC Med Supp model regulation, 42 CFR 438.8
>   - **derived for internal consistency** (5)
>   - **model setting** (10)
>   - **judgment** (40), where no public figure exists or a public anchor is adjusted. Each has a stated range and is run
>     across it: 32 sweeps in `npv_judgment_sensitivity.csv`, plus the tornado, 9 Monte Carlo
>     parameters (`npv_mc_parameter_sensitivity.csv`), the filing-response attribution and
>     the per-cell model-point table.
> - **No assumption is set from the synthetic book's experience.** It is not relevant
>   experience for a Humana block (ASOP 25 §3.3), so it gets zero credibility; it exercises and
>   validates the machinery. 17 values are **linked** to it and disclosed in
>   [ASSUMPTION_BASIS.md](ASSUMPTION_BASIS.md#values-linked-to-the-synthetic-book):
>   - 4 distribution placeholders read off it (Med Supp sales mix and issue ages, D&V category
>     shares, Medicaid cell mix). They don't affect the Plan G cell or the D&V and Medicaid base
>     results; they do drive the Med Supp portfolio and rating-basis figures and, because churn
>     differs by cell (v4.3), the Medicaid result.
>   - 13 values equal to the generator's own. They are kept because a public figure (6) or a
>     judgment range (7) supports them, so for those the source is a reasonableness check, not
>     the derivation. A test recomputes this list from the generator's file.

## Results as priced today (synthetic book, approved set AS-SYN-2026-011)

| Line | Cash NPV | DE NPV (after tax and capital) | Monte Carlo mean, cash / DE | P(NPV < 0), cash / DE | PV loss ratio | Break-even year, cash / DE |
|---|---|---|---|---|---|---|
| Med Supp (Plan G at 65, per 1,000) | $692,009 | $411,854 | $682,965 / $403,863 | 1.2% / 3.6% | 75.7% | 2 / 2 |
| Med Supp portfolio (issue-age × plan mix, per 1,000) | $310,389 | $119,341 | — | — | 78.2% | — |
| Dental & Vision (per 1,000) | **−$41,142** | **−$33,612** | −$39,743 / −$32,526 | 98.2% / 98.6% | 76.9% | not reached |
| Medicaid (10,000-member new-state contract) | $5,620,026 | $2,711,443 | $2,414,756 / $115,066 | 43.0% / 47.2% | 90.1% | 6 / 11 |

**What the public basis says about each product:**
- **Med Supp is sound** when the rate increase is consistent with Trustees trend and the age
  curves (7.3%).
  - Break-even DE trend is 7.44% (0.94 pt of headroom). Break-even rate increase is 6.32%.
  - DE stays positive across every judgment range. Shock lapse matters most ($273K swing).
  - The realistic issue-age and plan mix is worth 29% of the headline cell (DE). HDG and issue
    ages 75+ lose money because per-policy costs don't scale with a small premium, and filed age
    steps (3.0%) are flatter than morbidity (3.8%).
  - **Rating basis** (GA, AZ, FL and MO prohibit attained-age rating): with premiums solved for
    the same lifetime loss ratio and the same issue mix, lapse and anti-selection, the portfolio
    cash NPV is $310,389 attained-age, $276,496 issue-age and $277,967 community. This is
    actuarial equivalence **before selection effects**. Community rating charges 65-year-olds
    $2,689 against $1,980 attained-age, so in a market where both are sold its young buyers
    would leave, and the community figure is optimistic.
- **D&V as specified does not cover market expenses.**
  - Inputs are a market premium (EMI filing), a market loss ratio (Milliman for NADP), market
    admin and a 30% flat lapse from filed memoranda. Acquisition strain is not recovered before
    policies lapse. Filed increases equal the 3.5% claim trend (v4.3; at the earlier 3.0% the
    margin eroded every year by construction), so this is a price-level problem, not a rate-path one.
  - **It needs a 4.3% higher premium to break even** on DE ($468 → $488 a year).
  - A 12-month waiting period on MAJOR services turns it positive (cash +$32,662, DE +$24,567;
    `dv_product_options.csv`). That is a product option, not the base. It is an upper bound,
    because lapse and anti-selection don't respond to the waiting period in this model, and it
    scales with the MAJOR cost share (24.9%, read off the synthetic book).
- **Medicaid is thin, as the industry is.**
  - An 88% cost ratio with industry admin, premium tax and a 2% quality withhold leaves about a
    1.6% pre-tax margin before start-up costs. Milliman reports MCO underwriting results of −0.6% (2024) and −0.1%
    (2025).
  - **Churn by rate cell decides the sign.** The base uses MACPAC's churn by eligibility group
    (child 19% : adult 28% : ABD 9%, scaled to the 25% blend, i.e. about 22.6% / 33.3% / 10.7%).
    The mix drifts toward high-capitation ABD members (11.8% of members at the start, 21.7% by
    year 10), and DE NPV is +$2.71M. At equal churn by cell (the v4.2 base) it is −$1.09M. Part
    of the gain is because admin is a flat PMPM and the loss ratio is equal by cell; per-cell admin
    and cost from the state data book would temper it.
  - The point estimate is positive but thin. The DE break-evens sit 0.18–0.68 pt from the
    assumptions (rate update 4.32%, trend 4.70%, starting cost ratio 88.7%).
  - The expected value is much lower: the MLR floor costs $3.43M in the Monte Carlo (mean cash
    $5.85M without the floor, $2.41M with it), and mean DE is $0.12M with a 47.2% chance of a loss.

**Monte Carlo attribution, P(NPV < 0), cash / DE:**

| Layer | Med Supp | D&V | Medicaid |
|---|---|---|---|
| v3 basis (parameter level shifts) | 8.9% / 15.1% | 96.4% / 96.9% | 41.5% / 47.0% |
| + year-to-year process risk | 12.5% / 18.7% | 95.0% / 95.6% | 42.7% / 47.1% |
| + rate-filing response (v4; v4.4: each filing capped at 10%) | 1.2% / 3.6% | 98.2% / 98.6% | 43.0% / 47.2% |

Step 3 adds the filing response **and** removes v3's −0.5 trend / rate-shortfall correlation,
which the response replaces (keeping both would count it twice). The clean no-response comparison
is gap closure 0, where Med Supp P(cash NPV < 0) is 18.6%. Gap closure 0.25 and 0.75 give 4.0%
and 0.56%. The Monte Carlo volatilities are judgment too: across their ranges Med Supp P(DE < 0)
runs from 0.35% to 9.7%, driven by the trend level-shift SD (`npv_mc_parameter_sensitivity.csv`).

## Recommended pricing (v4.2)

The table above values the products **as priced today** on public assumptions. v4.2 adds the
pricing decision itself. Each line is priced so the probability that DE NPV is negative is at most
**10%** in the Monte Carlo (10,000 scenarios, with parameter risk, process risk and the filing
response). Break-even alone, DE NPV = 0, would leave about a one-in-two chance of missing the
hurdle, so each line's margin above break-even is sized by its own risk.

Trend stays at its best estimate: the margin protects against trend coming in worse, it does not
replace the trend assumption. Regulatory constraints are checked and never relaxed, and every
recommended price is compared with public market rates. The 10% tolerance is Humana's corporate
pricing policy to set, in the governed file `config/pricing_targets.json`.

| Line | As priced | Recommended | P(DE < 0) | DE NPV at recommended | Constraint and market check |
|---|---|---|---|---|---|
| Med Supp, Plan G at 65 (base cell) | $1,980 a year | **No change.** The cell already meets the target; the minimum is $1,952 (1.4% headroom) | 3.6% | $411,854 | PV loss ratio 75.7%, above the 65% minimum. Below the KFF all-ages average of $2,323. Relies on the filing response (below) |
| Med Supp portfolio (all model points) | P(DE < 0) **28.4% as priced** | **Plan N +2.0% to +8.1%; older-age Plan G +1.3% to +6.6%,** each to the base cell's 3.17% DE margin | **3.8%** | Portfolio **$382,991** per 1,000 (from $119,341) | N within Maryland's 0.65–0.90 relativity range. REVIEW: the implied Plan G age step is 3.55% against a public maximum of 3.5% |
| Med Supp, High-Deductible G | — | **Capped** at +16.5% to +26.0% by the 65% minimum loss ratio; three of four HDG cells still lose money | — | −$116,625 to +$40,082 per 1,000 | REVIEW: relativity 0.21 against one public 0.18. Per-policy costs must fall, or HDG is a loss leader |
| Dental & Vision | $468 a year ($39.00 a month) | **$501.36 ($41.78 a month), +7.1%** | 9.9% | **$22,332** (cash $29,924) | State rate filing (no federal minimum loss ratio). REVIEW: 7.1% above the one public reference. If lapse rises 10% in response to price, it needs +8.6% |
| Medicaid new-state contract | Capitation set by the state | **Do not bid at these terms** | 47.2% | +$2,711,443 | Meeting the target needs a starting cost ratio of 83.4% (a PV MLR of 83.8%, against public MCO MLRs of 87.1–90.6% and below the 85% floor) or admin of $10.99 PMPM (public $39–$46). Neither is achievable |

Four things to read with the table:
- **The Med Supp call relies on the filing response.** Med Supp meets the target because rate
  filings respond to emerging claims (each filing closes half the visible gap). With no response
  the base cell's P(DE < 0) is 24.7%; at a quarter of the gap it is 9.2%. Calibrate the response
  on Humana's filing history before relying on "keep the price".
- **Excel checks the NPVs at the recommended premiums.** At those premiums the independent Excel
  model reproduces the Python cash and DE NPVs to the cent (`tests/e2e_excel_pricing.py`). The
  solve itself and the probabilities are Python only.
- **The company goals (v4.4) are checked on every line** (`goals` column of `npv_pricing_solve.csv`):
  safety P(DE < 0) ≤ 10%, profit DE ≥ 0 at the 8.5% hurdle, members no in-force increase above 10%
  a year, market at most 5% above a credible reference. The goals are policy: no assumption moves
  to meet them. The member cap is enforced in the Monte Carlo filing response and costs Med Supp
  0.6 pt of P(DE < 0) (3.0% → 3.6%); the D&V reprice reaches existing members over 2 filings.
- **A risk corridor limits how much Medicaid can lose, not how often.** Negotiated with the state,
  it cuts the worst-10% average DE from −$30.3M to −$9.9M, but the chance of a loss only from 47.2%
  to 45.7%, so it does not reverse the call on its own.
- **Medicaid's positive DE doesn't reverse the call.** MACPAC churn by cell (the v4.3 base) is
  worth +$3.8M of deterministic DE against equal churn, but the 10th-percentile DE is −$19.8M and
  the chance of a loss is 47.2%. Confirm churn with the state data book before deciding.
- **These are recommendations on public assumptions.** On the synthetic book they demonstrate the
  method; the synthetic approval's scope excludes pricing use. Run on Humana's approved
  assumptions and pricing policy, the same solve produces Humana's pricing recommendation.

## What v4.1 closed

Every item the v4.0 review left open that can be closed without Humana data or a Humana workspace
is closed in the model or the controls. The rest are listed under **Scope and reliance** below,
each with its mitigation.

| Item left open in v4.0 | Closed by |
|---|---|
| DAC tax (IRC §848) not modelled | 9.2% of net Med Supp premiums capitalized and amortized over 180 months, half-year convention. Costs $101,388 of DE. Live formulas in Excel, tied to the cent |
| GA, AZ, FL, MO prohibit attained-age rating | Issue-age and community premiums solved for equal PV loss ratio (`medsupp_rating_bases.csv`) |
| D&V waiting periods removed pent-up demand | Pent-up is deferred to the first 12 months after the wait (Python and Excel) |
| No IBNR on the large-claim excess | Excess IBNR = (1 − completion) × credibility-weighted pooling charge × a-priori |
| Medicaid forecast incurred bias +2.8% (pooling charge from one claim) | Limited-fluctuation credibility on the pooling charge (full credibility at 1,082 × (1 + CV²) large claims) |
| Forecast volume bias from pricing lapse and default new sales | Book lapse from the lapse study; new sales on trailing 12-month trend, capped at ±30%. All persistent-bias tests pass |
| D&V filing response used next year's experience | 2-year lag, consistent with the filing cycle |
| Bootstrap SE understated Med Supp uncertainty by ~15% | Inflated by the seed-study ratio, 1.16 |
| Medicaid contract terms all neutral; Test A refused non-neutral terms | Public 2% withhold, 85% earn-back (Louisiana, CMS). Test A's simulator implements every contract term |
| Kentucky exception DQ-0142 expired 2026-06-30 | Renewed by the data owner to 2027-06-30 (synthetic example). The production test still proves an expired exception stops the run |
| Databricks job never executed | `tests/e2e_databricks_job_local.py` validates the job definition (dependencies, clusters, files, placeholders) and runs all 17 tasks in order in production mode |
| Approval did not cover the forecast and curve configs | The approval hash covers the assumption set **and** the governed configs. Production also requires this run's governance step to have approved the same hash |
| A line already below break-even was never alerted | Break-even output reports which side of break-even the base is on. Any line with negative base DE is an ALERT |
| Judgment values not run across their ranges | All judgment values (41 in v4.1, 40 in v4.3) run across their ranges: 32 sweeps, the tornado, 9 Monte Carlo parameters, the response attribution, the per-cell model points; age-curve sweeps re-derive the rate increase |
| *Independent review (v4.1):* synthetic-linked values were not disclosed | Values flagged in the register (17 in v4.3), listed in ASSUMPTION_BASIS.md, recomputed by a test from the generator's file |
| *Independent review (v4.1):* pooling-charge credibility used the claim-count standard | Standard scaled by (1 + CV²) of excess severity, because the charge is an aggregate |
| *Independent review (v4.1):* the drift monitor could miss a line with no break-even in its bracket | Alerts built from every DE row before that filter; tested on the function itself |

## Validation evidence (all run here)

- **The workbook ties to Python to the cent** on the approved set: cash, DE (including the DAC
  tax), every year, 17 sensitivities. It has 66,860 live formulas, none in error. It also ties at
  non-neutral contract terms in three variants (corridor loss side, corridor gain side, floor
  binding), and at the v4.2 recommended premiums.
- **Backtest Test A** (event-level simulation including every Medicaid contract term):
  z = +1.11 / +0.75 / −0.49. No line is biased.
- **Test B** (against the generator's truth): the approved public Med Supp trend (6.5%) is
  0.5 pt above the generator's arbitrary 6.0%, costing −$261,650. That is expected, because the
  generator is not the public process. D&V: $0. Test B can only measure error in values that
  differ from the generator's (chiefly trend), because 13 values equal it.
- **Test C:** parameter-to-process risk ratio 3.2 / 1.0 / 5.5.
- **Forecast:** incurred PMPM MAPE 2.3–6.7% by horizon. No persistent bias on any line or metric
  (12 tests, 5 vintages). These use the generator's claim-cost curves and payment pattern, i.e.
  known curves; on Humana data the curves are estimated, so expect wider errors.
- **Assumption register test:** every value is covered and matches the register. Public and
  statutory entries carry sources; judgment entries carry ranges.
- **Trend estimator study** (30 seeds): normalized fits are unbiased and the inflated bootstrap
  SE is calibrated ([docs/validation/trend_estimator_study.md](validation/trend_estimator_study.md)).
- **Production end to end** (two phases), the **Databricks job run locally** (17 tasks), and
  **119 automated tests**, all passing.

## Run it

```bash
python3.13 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python run_all.py                  # synthetic mode, about 17 minutes, stops at the first failure
python tests/e2e_production_run.py # production mode end to end, two phases
python export_results.py           # every quoted figure into output/results.json (also the last step of run_all)
python code/python/write_assumption_basis.py    # rebuild the assumption register after changing an assumption
python code/python/render_assumption_basis.py   # regenerate docs/ASSUMPTION_BASIS.md from the register
```

## Reading the results: what passes and what is flagged on purpose

- **PASS verdicts:**
  - controls gate
  - assumption gate `APPROVED` (AS-SYN-2026-011)
  - both reconciliations
  - backtest z within ±2
  - notebook tie-out
  - the local Databricks job
  - all tests
- **Two trend REVIEW items are intended.** The synthetic book indicates 6.80% (Med Supp) and
  3.85% (D&V) against the public 6.5% and 3.5%. The approval dispositions them because synthetic
  data is not relevant experience. The three D&V lapse REVIEW items (synthetic book vs the 30%
  public basis) are dispositioned the same way.
- **FAIL / REVIEW in the raw data-quality report (SQL 01) is expected.** The problems are planted,
  and the gate cleans them or covers them by exception.
- **The Medicaid holdout `BIAS FLAG` is one noisy year** (−6.9%; −2.4% averaged across rolling
  origins), not a persistent bias.
- **Drift monitoring raises 4 ALERTs, all intended:**
  - 3 Medicaid termination ALERTs: the redetermination wave.
  - 1 below-break-even ALERT: D&V DE NPV is negative at base.
- **Three Medicaid churn-relativity REVIEW items are intended.** The synthetic book shows no
  credible churn difference by cell; the approval keeps the MACPAC relativities because synthetic
  data is not relevant experience.
- **Placeholders in the Databricks job** (runtime, node type, policy, catalog, vintage schema,
  notification list) are for the platform team. The local job test lists them.

## What's in the kit

| Step | File | What it does |
|---|---|---|
| 0s | `code/python/00s_start_run.py` | Clean start: clears the kit's previous artefacts (never the vintage registry) and stamps the run |
| 0 | `00_generate_synthetic_data.py`, `00b_…finance_controls.py` | Synthetic book with planted data problems, and synthetic ledger totals (synthetic mode only) |
| 0a | `00a_ingest_source_extracts.py` | Maps CSV or Parquet extracts onto the canonical layout. Strict, with lineage hashes |
| 1 | `code/sql/01–06_*.sql` via `01_run_sql_kit.py` | DQ checks → silver → PMPM experience → completion factors → lapse study → gold → A/E |
| 1b | `01b_controls_gate.py` | Data contract, enforced data gate and ledger tie-out; stops the run on failure |
| 2 | `02_claims_trend_forecast.py` | Trend: normalized fits, rolling-origin model selection, member-bootstrap SE, bias diagnostics |
| 3 | `03_lapse_glm_study.py` | Poisson GLM lapse |
| 3b | `03b_assumption_governance.py`, `assumption_control.py` | Indications with credibility (trend blend with inflated SE, lapse, model-point mix); approval gate by hash |
| 4 | `04_npv_engine.py` + `npv_assumptions.json` + `config/pricing_targets.json` | v4.2 risk-based pricing solve; monthly cash flows, contract terms, profit test with DAC tax, tornado, Monte Carlo and attribution, break-even with position, planning scenarios, model points, rating bases, judgment sensitivity, bridge |
| 9 | `09_inforce_forecast.py` + `forecast_lib.py` | In-force forecast of the existing book (BF completion with excess IBNR, credibility-weighted pooling, book lapse, trend new sales) |
| 10 | `10_actual_vs_expected.py` | A/E variance waterfall against the prior vintage; steps tested independently |
| 11 | `11_forecast_vintages.py` | Vintage registry keyed on the as-of month, accuracy by horizon, persistent-bias test |
| 5 | `05_reconcile_python_to_excel.py` | Excel vs Python to the cent |
| — | `tests/e2e_excel_nonneutral.py` | Excel vs Python to the cent at non-neutral v4 settings |
| — | `tests/e2e_excel_pricing.py` | Excel vs Python to the cent at the v4.2 recommended premiums |
| 6 | `06_npv_backtest.py` | Tests A (bias), B (estimation error against the generator's truth), C (process vs parameter risk); synthetic only |
| 12 | `12_drift_monitoring.py` | Credibility-scaled A/E bands with persistence, forecast-bias, governance and break-even alerts |
| 8 | `08_run_manifest.py` | Audit record of this run; releases results only if approved **and** reconciled |
| — | `tests/e2e_databricks_job_local.py` | Validates `deploy/databricks_job.json` and runs its 17 tasks in dependency order |
| — | `estimate_mc_parameters.py` | Proposes Monte Carlo volatilities and correlations from annual history |
| — | `write_assumption_basis.py`, `render_assumption_basis.py` | Assumption basis register (sources, ranges, synthetic links) and its rendered document |
| — | `build_workbook.py`, `recalc_workbook.py` | 17-sheet Excel model: live-formula model sheets, the V4_Analytics sheet of Python value tables with tie-out checks, and a README sheet |
| — | `code/databricks/npv_pipeline_databricks.py` | Bronze/Silver/Gold, DQ gate, distributed lapse × trend grid |
| — | `code/sas/` | **Retired** version 2 mirror, never executed (see its README) |
| — | `deploy/` | Databricks Workflow (17 tasks), Spark-free requirements, LibreOffice init script |
| — | `docs/` | Executive summary, model documentation, validation pack, assumption basis, trend study, data request, runbook |

## Things the kit is designed to surface

1. **Trend is the dominant uncertainty.** A ±1-point trend shock swings Med Supp DE NPV by
   $845,542 and takes it negative on the adverse side.
2. **How you estimate matters as much as what you estimate.**
   - Unnormalized D&V experience gave a trend biased by −1.75 pt.
   - Residual-based standard errors understated the real uncertainty by 25–40%.
   - Both were found only by re-running the estimator on 30 generated books.
3. **A headline cell is not a portfolio.** Plan G at 65 is worth $411,854 DE per 1,000; the
   realistic issue-age and plan mix is worth $119,341.
4. **Management actions belong in the risk measure.** With no filing response Med Supp
   P(cash NPV < 0) is 18.6%; with the assumed response, capped at 10% a filing, it is 1.2%. How strongly filings follow
   experience is judgment, so the attribution shows the range.
5. **Cash NPV is not the decision measure.** Medicaid's $5.62M cash NPV is $2.71M after capital
   and tax, and the MLR floor takes the Monte Carlo mean DE to $0.12M. For Med Supp, the DAC tax alone costs $101,388, 20% of DE before it.
6. **A product can be unprofitable on market inputs.** D&V at market premium, loss ratio and
   admin loses money (98.2% probability). The public basis surfaces this, and the reverse stress
   says by how much (4.3% premium).
7. **One feed drop, two symptoms.** Kentucky Medicaid eligibility is missing for July 2025.
   - The DQ gate flags the missing month, and 336 of the 376 orphan claims trace back to it.
   - The A/E waterfall shows it as volume, not morbidity.
8. **A one-year bias is not a persistent bias.** Medicaid's −6.9% holdout error averages −2.4%
   across rolling origins. Only persistence becomes a REVIEW item.

## Scope and reliance (what this kit cannot establish without Humana)

These are boundaries of a model built outside Humana. They are not defects. Each one has a
mitigation in the kit and a named production remedy (MODEL_DOCUMENTATION.md §8).

- **Humana experience.** Every NPV assumption is public, statutory, derived or judgment. The
  credibility blend is built and tested; it weights Humana experience against these complements
  on the first production cycle.
- **National sources.** Public benchmarks are national or industry-level: Trustees and NHE cover
  all of Medicare and Medicaid; Milliman and Gen Re are composites; the Medicaid capitation and
  withhold come from one state (Louisiana). They are documented as such (ASOP 23, ASOP 41).
- **40 judgment values** with no public figure, or with a public anchor adjusted by judgment. They are disclosed with ranges and run across
  them. Medicaid DE changes sign within three swept ranges (start-up cost, risk-adjustment
  capture, equal churn by rate cell). D&V changes sign within one (pent-up 1.10, +$4,865). Med
  Supp stays positive.
- **Values linked to the synthetic book** (17, listed in ASSUMPTION_BASIS.md). The forecast and
  the trend normalization also use the generator's claim-cost curves and payment pattern, so the
  forecast accuracy and estimator results are on known curves.
- **Classification for the DAC tax** (Med Supp 9.2%, D&V and Medicaid 0) is to be confirmed by
  Corporate Tax. If individual dental is guaranteed renewable, D&V is 9.2% too (DE −$8,894,
  swept). The §848(c)(2) general-deductions cap is not applied, and the tax year is taken as the
  policy year.
- **Pricing-grade profit test:**
  - no active-life reserve, so DE is reported for the attained-age basis only (issue-age rating
    needs one);
  - tax reserves equal statutory reserves;
  - tax losses are used in the year they arise;
  - capital is a percentage of annualized paid claims.
- **Synthetic forecast vintages** are rebuilt with the book lapse estimated on the full
  experience period (hindsight) and with the generator's own curves and payment pattern (known
  curves). Production vintages are real and come from the registry.
- **Monte Carlo volatilities, correlations and the filing-response strength** are judgment until
  `estimate_mc_parameters.py` is run on Humana's rate-filing and trend history. Each is run across
  its range, and the tail probabilities move with them.
- **The Databricks workspace.** Runtime pins, Unity Catalog permissions and cluster policies can
  only be confirmed by a dry run in a Humana non-production schema (RUNBOOK §2, item 9).
- **SAS is retired.** Python is the production engine, and the Excel rebuild is the independent
  check.

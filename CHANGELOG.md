# Changelog

All figures are on the synthetic practice book. From 4.1.0 the assumptions rest on public sources, statute and documented judgment; earlier versions used illustrative values.

## 4.4.0 — company goals in the pricing solve (no assumption changed)

Assumption set AS-SYN-2026-011 supersedes AS-SYN-2026-010 (the approval hash covers
`config/pricing_targets.json`). `make run` passes: 119 tests, Excel = Python to $0.00 in every
variant. Goals are POLICY, kept apart from the assumptions: no assumption moves to meet them.

- **Safety:** P(DE < 0) ≤ 10% on every line, with the worst-10% average DE now reported
  (`de_tail_mean_*` columns). Medicaid as bid: −$30,330,317.
- **Profit:** DE ≥ 0 at the 8.5% hurdle. No second margin target (it would double-count the risk margin).
- **Members:** no in-force increase above 10% a year (Med Supp, D&V), enforced in the Monte Carlo
  filing response (`management_rate_path(..., cap=)`): a capped filing carries the rest of the gap
  forward. Med Supp P(DE < 0) 3.0% → 3.6%, P(cash < 0) 0.6% → 1.2%; minimum premium $1,950 →
  $1,952; portfolio 28.0% → 28.4% as priced, 3.1% → 3.8% at the recommended cell prices. The D&V
  reprice reaches existing members over 2 filings (10.9% in one would exceed the cap).
- **Market:** at most 5% above a credible reference (hard limit; Med Supp vs KFF, met). The single
  D&V filing is low confidence, so +7.1% stays a REVIEW flag.
- **New levers:** D&V fallback waiting-period designs, tried only if price inside the limits cannot
  meet the risk target; a Medicaid risk corridor tested as a negotiable term (P(DE < 0) 47.2% →
  45.7%, worst-10% average −$30.3M → −$9.9M: still "do not bid at these terms").
- 6 tests added; `test_zero_strength_response_equals_no_response` now isolates the response
  mechanism from the cap. `show_results.py` prints the member phasing and the Medicaid term.

## 4.3.0 — two assumption consistency corrections

Assumption set AS-SYN-2026-010 supersedes AS-SYN-2026-009. Every changed figure is in
`output/results.json`; `make run` passes (113 tests, Excel = Python to $0.00 in every variant).

- **D&V annual rate increase 3.0% → 3.5%, equal to the claim trend.** At 3.0% against a 3.5% trend
  the margin eroded every year by construction. The 2013 memo it came from filed 3% against its own
  3% trend, so the source supports increases at trend. D&V DE NPV −$40,686 → −$33,612; P(DE < 0)
  99.8% → 98.6%; the recommended reprice is +7.1% ($41.78 a month) instead of +8.0%.
- **Medicaid churn relativity by rate cell 1.0 / 1.0 / 1.0 → MACPAC 12-month churn by eligibility
  group** (children 19%, adults 28%, disabled 9%, over the all-enrollee 21%; normalized to the 25%
  blend). Equal churn was a parsimony judgment against available public data, and it was material.
  Medicaid DE NPV −$1,089,199 → +$2,711,443, but P(DE < 0) is 47.2%, so the call stays "do not bid
  at these terms". The synthetic book's churn by cell is dispositioned like trend and lapse
  (not relevant experience, ASOP 25 §3.3).
- **In-force D&V forecast reads the book's own rate action** (its January premium step, 3.0%), as
  Medicaid already did, so the new-business 3.5% does not leak into the existing book's forecast or
  the A/E vintages (`config/forecast_config.json`).
- **Med Supp premium age step unchanged at 3.0%.** It is the existing book's filed attained-age
  table and inside the public 2.7–3.5% range. Older issue ages being underpriced against the 3.8%
  claim slope is a pricing finding, which the per-cell pricing solve handles; it is not an
  assumption error.
- Test `test_rate_cells_blend_to_contract_level_assumptions` checks month-1 revenue at equal churn:
  with churn differing by cell the month-1 mix already tilts toward the ABD cell, which a second
  assertion now checks. Drift ALERTs 5 → 4 (Medicaid is no longer below DE break-even).
- Docs and README updated to the new figures; stale "Medicaid −12%" docstring in
  `04_npv_engine.py` corrected.

## 4.2.1 — display and documentation (model version 4.2.0 unchanged)

No figure, assumption or approval changed: the model version stays 4.2.0, the assumption set
stays AS-SYN-2026-009 (same hash), and every result reproduces to the dollar.

- `show_results.py`: checks that `make quick` doesn't run now say `not part of make quick: run
  make run (about 15 min)` instead of `SKIPPED`.
- Med Supp cell pricing: a market check against a single public reference now says so
  (`outside the single public reference 0.18`), instead of printing a one-point range as `0.18-0.18`.
- Backtest Test B prints lapse rates to four decimals (the CSV keeps full precision).
- README: notes on the HDG flag, the Spark pandas warning and the `make quick` summary; a new
  **Known limitations of this build** section (trend estimation error, the no-credibility rule
  and the D&V lapse curve, the unexplained part of the 2025 Medicaid A/E).

## 4.2.0 — risk-based pricing solve

**Why this release exists.** v4.1 valued the products as priced. A model Humana deploys must also
say what to charge, or whether to bid, so that each line earns its cost of capital with an
acceptable risk of falling short, inside the regulatory constraints.

- **Target:** P(DE NPV < 0) ≤ 10% in the Monte Carlo, solved as a zero τ-quantile with Brent's
  method. DE = 0 alone is the minimum, not the target, because it fails the hurdle in about half
  of outcomes. Trend stays at its best estimate. The tolerance is Humana's pricing policy:
  `config/pricing_targets.json`, now a governed config in the approval hash.
- **Constraints, checked and never relaxed:**
  - Med Supp 65% minimum loss ratio (model points are capped at it);
  - Medicaid 85% MLR floor (remittance modelled) and state-set capitation;
  - D&V state rate filing.
- **Competitiveness:** every recommended price is compared with public filed rates (KFF,
  Maryland 2026, EMI). Prices outside them are flagged REVIEW. For D&V, a 10%-higher-lapse
  robustness run gives the price-response sensitivity.
- **Results:**
  - **Med Supp base cell** meets the target as priced (P = 3.0%, 1.5% headroom), provided the
    filing response holds (24.7% with no response, 9.2% at a quarter of the gap).
  - **The Med Supp portfolio** fails the target as priced (P = 28.0%) and meets it at the
    recommended cell prices (3.1%): a Monte Carlo per model point with common random numbers.
  - **Med Supp model points:** Plan N +2.0% to +8.1% and older-age Plan G +1.3% to +6.6% to the
    base cell's DE margin. Portfolio DE goes from $119,341 to $382,991 per 1,000.
  - **High-Deductible G** is capped by the 65% minimum loss ratio at +16.5% to +26.0%, and three
    of four cells still lose money.
  - **D&V +8.0%** ($39.00 → $42.11 a month): DE $21,335, P = 9.8%. REVIEW: 8% above the public
    reference.
  - **Medicaid: do not bid at these terms.** It would need a cost ratio of 82.6% (a PV MLR of
    83.1%, against public MCO MLRs of 87.1–90.6%) or admin of $10.07 PMPM (public $39–$46).
- **Verification:**
  - three new tests (quantile at the solve, constraints and cap, bid rule);
  - `tests/e2e_excel_pricing.py`: the workbook rebuilt at the recommended premiums ties to Python
    to the cent and reproduces the pricing table's DE NPV, on every synthetic release run.
- **Final review fixes:**
  - the portfolio risk check;
  - the Medicaid cost ratio compared through the PV MLR (the measure Milliman reports);
  - a lever that already meets the target is reported achievable;
  - a failed robustness solve or a recommendation above the loss-ratio cap stops the run;
  - model-point prices are never cut.
- **Approval:** AS-SYN-2026-008, then AS-SYN-2026-009 after the final review (model 4.2.0). No assumption value changed.
- **Outputs:** `npv_pricing_solve.csv` and `medsupp_cell_pricing.csv`, both also on V4_Analytics
  and in `results.json`. The Dashboard's first talking point is the pricing recommendation.

## 4.1.0 — public-source assumption basis; v4.0 open items closed

**Why this release exists.** There is no Humana data, so each assumption needs an actuarial
basis that doesn't depend on it. The ASOPs prescribe a process, not values (ASOP 25 §3.3,
ASOP 8 §3.4, ASOP 49, ASOP 23, ASOP 56 §3.6.1, ASOP 41, ASOP 58). v4.1 applies that process with
public sources, documents every assumption, and closes every v4.0 open item that can be closed
without Humana data or a Humana workspace.

### Assumption basis
- **Register** (`config/assumption_basis.json` → `docs/ASSUMPTION_BASIS.md`): 89 entries covering
  every NPV value. Public source 30, statute 4, derived for consistency 4, judgment 41 (each with a
  range), model setting 10. `tests/test_v4_model.py` enforces coverage, value match, sources and
  ranges.
- **Policy: the synthetic book is not relevant experience** for a Humana block (ASOP 25 §3.3) and
  gets no credibility. Its indications (Med Supp trend 6.80%, D&V trend 3.85%, three D&V lapse
  durations) are REVIEW items dispositioned in the approval.
- **Changes from the basis review:**
  - Med Supp trend 6.5% (2026 Trustees Report, Part B per-enrollee prices × volume/intensity).
  - Med Supp claim age slope 3.8% (Milliman, KFF) and premium age step 3.0% (Maryland 2026 rate
    guide); rate increase re-derived as 7.3%, which holds the ultimate loss ratio level.
  - Plan relativities N 0.77, HDG 0.18 (Maryland 2026).
  - **Commission 20% / 10%: the old 22% / 10% broke the 200% first-year cap.**
  - Med Supp mortality 75% of SSA 2022 unisex (the old q65 was 59% of SSA); growth fitted at 9.1%.
  - Premium tax 2.25% (GA and LA).
  - D&V trend 3.5% (ADA HPI, Segal); maintenance $50 (Milliman for NADP admin level); lapse 30%
    flat (filed individual dental memoranda).
  - Medicaid trend and rate update both 4.5% (rate certifications; ASOP 49 §2.1); cost ratio
    88%, admin $40, tax 2.3% (Milliman MCO results); capital 15% (NAIC H2); quality withhold 2%
    with 85% earn-back (Louisiana, CMS).
  - Hurdle rate 8.5% (Damodaran + Treasury); earned rate 4.0% (NEAM).
  - Capital and stress assumptions cite ASOP 58 (ASOPs 46 and 47 were repealed).

### v4.0 open items closed
| Item | Change | Effect |
|---|---|---|
| DAC tax not modelled | IRC §848: 9.2% of net Med Supp premiums capitalized, amortized over 180 months (half-year convention); Excel schedule with live formulas | Med Supp DE −$101,388 |
| GA, AZ, FL, MO prohibit attained-age rating | Issue-age and community bases with premiums solved for equal PV loss ratio | Portfolio cash $310,389 / $276,496 / $277,967 |
| D&V waiting periods removed pent-up | Deferred to the first 12 months after the wait (Python and Excel) | 12-month wait on MAJOR: DE +$17,493 (upper bound) |
| No IBNR on the large-claim excess | Excess IBNR = (1 − completion) × pooling charge × a-priori | Immature months carry IBNR on the excess, so the A/E large-claims step is not flattered by unreported large claims |
| Medicaid incurred forecast bias +2.8% | Limited-fluctuation credibility on the pooling charge | Medicaid incurred bias −0.6%; no persistent bias on any line |
| Forecast used pricing lapse and a flat new-sales default | Book lapse from 03b; trailing-12-month trend new sales (±30% cap) | Member-months bias across vintages: D&V −2.4% → −0.8%, Med Supp −0.9% → 0.0% (neither significant) |
| D&V filing response looked one year ahead | 2-year lag, as Med Supp | — |
| Bootstrap SE understated Med Supp by ~15% | ×1.16 in 03b | Z slightly lower |
| Medicaid contract terms all neutral; Test A refused them | 2% withhold; Test A's simulator implements every term | Medicaid cash −$1.90M (bridge step) |
| Exception DQ-0142 expired | Renewed by the data owner to 2027-06-30 (synthetic example), prior expiry kept | Production test still proves expiry stops the gate |
| Databricks job never executed | `tests/e2e_databricks_job_local.py` validates and runs all 17 tasks in order | PASS |
| Approval did not cover forecast and curve configs | Governed configs in the approval hash; production requires this run's 03b approval of the same hash; manifest releases only if approved and reconciled | — |
| Below-break-even line not alerted | Break-even position and base NPV; negative base DE is an ALERT; D&V premium break-even | D&V needs +5.2% premium |
| Judgment values not run across ranges | All 41 run across their ranges: 32 sweeps in `npv_judgment_sensitivity.csv`, the tornado, 9 Monte Carlo parameters in `npv_mc_parameter_sensitivity.csv`, the response attribution, the per-cell model points | Med Supp DE positive throughout; D&V negative throughout; Medicaid changes sign in 4 ranges |

### Results (cash / DE)
- **Med Supp** $692,009 / $411,854 per 1,000; portfolio $310,389 / $119,341.
- **D&V −$50,128 / −$40,686**: market premium, loss ratio, admin and lapse don't recover
  acquisition strain. Break-even needs +5.2% premium.
- **Medicaid $519,615 / −$1,089,199**: positive cash, but it does not earn the hurdle on 15%
  capital. Expected value −$2.07M / −$3.19M after the MLR floor's option cost.

### Corrections made during the release
- `npv_assumptions.json` documentation strings still called the values illustrative, the plan
  relativities synthetic and the Medicaid terms neutral. Corrected; the set was re-approved
  (AS-SYN-2026-005, then AS-SYN-2026-006 for the config documentation below, then
  AS-SYN-2026-007 after the independent review). No NPV assumption value changed.
- `forecast_config.json` said lapse came from the approved file; since v4.1 it is the book's
  experience. `claim_cost_curves.json` now says the forecast uses it too.
- The tax test was conditioned on the sign of DE; the correct condition is the sign of taxable
  income (Medicaid is taxable before the cost of capital even though its DE is negative). The test
  now also asserts that the tax cost equals the PV of tax.
- The Databricks placeholder scan read documentation text and split a nested placeholder; it now
  scans job settings only, and the vintage path placeholder is flat.

### Independent review (before release)
A separate reviewer re-derived the quoted figures from the outputs and checked the mechanics. Every
finding was fixed:
- **Synthetic-linked values were not disclosed.** The docs said nothing relied on the synthetic
  book. In fact four distribution inputs are read off it (Med Supp mix and issue ages, D&V
  category shares, Medicaid cell mix; none affects the Plan G cell or the D&V and Medicaid base
  results, but the Med Supp portfolio and rating-basis figures rest on the mix), and 14 values equal the
  generator's own. All 18 are now flagged in the register, listed in ASSUMPTION_BASIS.md and
  recomputed by a test. The policy wording says what is true: no credibility to synthetic
  *experience*.
- **"Every judgment value is run" was not true** (22 of 40). Now all 41 are (Medicaid churn was
  relabelled judgment: its 25% is a judgment uplift on MACPAC's 21%), with ten new sweeps:
  - payment patterns ±1 month;
  - D&V renewal commission;
  - D&V DAC classification;
  - Medicaid contract term;
  - Medicaid cell mix and ABD loss ratio;
  - Medicaid churn by cell;
  - Medicaid acuity path;
  - the dynamic-lapse slope, at a non-reference increase;
  - a Monte Carlo parameter table (9 parameters).

  The Medicaid cell-mix and ABD loss-ratio sweeps run at MACPAC churn by cell, because at equal
  churn the normalization makes them inert.

  Block entries in the register now store their values, and the test checks them.
- **New finding from the sweeps:** Medicaid DE turns positive (+$2.71M) at MACPAC's churn by rate
  cell, because the mix drifts toward ABD members and admin is a flat PMPM.
- **Pooling-charge credibility** used the claim-count standard. It is now 1,082 × (1 + CV²) of
  excess severity.
- **Drift monitor** could miss a line with no break-even inside its bracket. Fixed, and tested on
  the function rather than on stale output files.
- **Stale or wrong text:**
  - D&V trend indication 3.90% → 3.85%;
  - the forecast-vintage caveat (curves are the generator's own, i.e. known);
  - the Monte Carlo attribution wording: step 3 also removes v3's correlation, so the
    no-response comparison is gap closure 0 (18.6%);
  - the rating-basis comparison is equivalence before selection effects;
  - DAC simplifications disclosed (§848(c)(2) cap, tax year = policy year, D&V if guaranteed
    renewable).
- **Outputs that were quoted but not exported:** D&V waiting-period options
  (`dv_product_options.csv`). `export_results.py` now runs at the end of every synthetic release
  run.

## 4.0.0 — forecasting-team version

Built to close the gaps between v3 (a new-business pricing NPV kit) and what an Analytics /
Forecasting team runs every month: an in-force forecast, an actual-vs-expected explanation,
calibrated assumptions with measured uncertainty, and contract-level economics.

### Findings that changed the headline (read these first)
- **The v3 trend assumptions were not defensible.** They kept Med Supp 6.0% and D&V 3.5% against
  fits of 6.82% and 2.58%, on the basis that four years pin trend only to about ±2 points. A
  30-seed study of the estimator (`docs/validation/trend_estimator_study.md`) measured the true
  sampling SD at 0.50 pt (Med Supp) and 1.05 pt (D&V), against a stated ±2 pt range. That is about 4× tighter for Med Supp and 2× for D&V. The kept values were also
  the synthetic generator's true trends, which no production benchmark can supply.
- **The v3 D&V trend fit was biased by −1.75 pt** (nine standard errors). Raw D&V PMPM mixes
  cost trend with the falling share of year-1 (pent-up) exposure. v4 normalizes D&V for duration
  and product, as v3 did for Med Supp; the bias goes to −0.09 ± 0.19 pt.
- **v4 sets trend by a precision-weighted credibility blend** of the normalized fit and an
  external benchmark: Med Supp 6.75% (Z = 0.86), D&V 3.65% (Z = 0.49). The benchmarks are
  placeholders (X). On the 30 seeds the blend beat the raw fit in RMSE on every line.
- **Headline after the change (cash / DE):** Med Supp **$256,873 / $184,962** per 1,000
  (v3: $704,726 / $538,482); D&V $29,256 / $23,009 (v3: $32,998 / $25,956); Medicaid unchanged
  at $12,977,412 / $10,054,905. The shipped data is a +1.6 SD draw for Med Supp trend, so on this
  book the correct process lands above the true 6.0%. Backtest Test B now measures that against
  the generator's truth: −$447,853.
- **Med Supp break-even trend is 7.16%, only 0.41 pt above the assumption** (DE: 7.12%).
- **The realistic Med Supp new-business mix is worth less than the headline cell, and on these
  assumptions it is negative:** −$204,269 cash / −$178,591 DE per 1,000. Only Plan G at 65, Plan N at
  65 and Plan G at 66–69 are positive. Two causes: the kit's illustrative premium age step (3%) is below its claim
  age slope (4%), and HDG carries fixed per-policy expenses on 35% of the premium. Humana's filed
  age and plan rate relativities (X) decide whether this holds. It is the first table to check
  with real inputs.
- **Medicaid "BIAS FLAG" (−6.9% holdout) is one noisy year, not a persistent bias.** It was −2.4%
  on average across 5 rolling origins (SE 1.4%) and −2.2% across 5 forecast vintages (z −1.5).
  The generator has no acuity shift. 2022–24 ran below trend by chance on a small book and 2025
  caught up.

### Model (each new mechanic is neutral at its neutral setting and tested to reproduce v3)
- **Trend method** (02, 03b): member-level bootstrap SE of the fitted trend (residual HAC SEs
  understate the true SD by 25–40%); D&V normalization (`config/claim_cost_curves.json`);
  credibility blend with `config/trend_benchmarks.json`; rolling-origin model selection (5 origins)
  with a persistent-bias test. A persistent bias is a REVIEW item, a one-year bias is INFO.
- **Medicaid contract terms** (`medicaid_contract_terms`): quality withhold with expected
  earn-back (received with the year's settlement); directed payments as a pass-through (both sides
  of the MLR; premium tax on them); MLR floor **or** a symmetric risk corridor; acuity path by
  contract year with risk adjustment catching up after a lag (post-redetermination acuity shifts).
  Neutral settings in the approved set, because the synthetic contract has none of these terms.
- **D&V benefit categories** (`dv_benefit_categories`): preventive / basic / major / vision with
  their own share (from experience), trend add-on, normalized pent-up relativity and waiting period.
- **Med Supp model points** (`medsupp_portfolio()`): issue-age band × plan, mix from 2024–25 issues,
  Plan F excluded from new business (closed to people newly eligible for Medicare since 2020).
- **Monte Carlo process risk** (`mc_process_risk`): AR(1) year-to-year trend deviations shared
  by all lines, plus yearly noise on Med Supp approvals and Medicaid updates, on a separate random
  stream so the v3 draws are unchanged.
- **Rate-filing response** (`mc_management_response`): each Med Supp / D&V filing closes a share of
  the claim-index gap visible in lagged experience. It replaces v3's −0.5 trend / rate-shortfall
  correlation (now 0; the engine refuses both together). The Monte Carlo attribution
  (`npv_monte_carlo_attribution.csv`) shows each layer and the response-strength sensitivity:
  Med Supp P(cash NPV < 0) is 32.3% on the v3 basis, 34.1% with process risk and 20.6% with the
  response. At response strength 0 / 0.25 / 0.75 it is 37.7% / 27.8% / 16.1%. The strength is
  judgment.
- **Reverse stress** (`breakeven()`): the level of each key driver at which cash and DE NPV are zero.
- **Planning scenarios** (`config/planning_scenarios.json`): named multi-driver scenarios, cash
  and DE.

### Forecasting (new modules 09–11, `code/python/forecast_lib.py`)
- **09 in-force forecast**: calendar-month, 36 months, existing book by line × state ×
  plan/rate cell × duration. Bornhuetter-Ferguson completion for lags 0–2, segment credibility,
  IBNR run-off, new sales from `config/forecast_config.json` (Finance plan in production). Medicaid
  uses book churn, trend and rate update, not the new-state contract.
- **10 A/E waterfall**: 2025 actual against the forecast made at 2024-12, split into volume
  (in-force / new sales), mix, base re-estimate, trend and morbidity, large claims and prior-period
  development. The steps add to the total by construction: trend and morbidity is the residual, and the other steps are tested against independent calculations. The Kentucky July 2025 feed drop is shown as
  volume, not morbidity.
- **11 forecast vintages**: a persistent registry (`NPV_VINTAGE_DIR`, never cleared), accuracy
  by horizon and a cross-vintage bias test.

### Governance, monitoring, production
- **12 drift monitoring**: credibility-scaled A/E bands with a persistence rule, plus forecast
  bias, undispositioned REVIEW items and break-even headroom, in one alert list
  (`config/monitoring_rules.json`). The manifest carries the summary.
- **Approval AS-SYN-2026-002** for model version 4.0.0 (synthetic example), with the reasons for
  the trend change.
- **`estimate_mc_parameters.py`**: proposes Monte Carlo volatilities, AR(1) and correlations from
  annual history (`config/mc_history_template.csv`), and refuses fewer than 6 years.
- **Databricks job template**: v4 tasks; `NPV_VINTAGE_DIR` on a persistent Volume; monthly
  schedule, paused.
- **Workbook**: v4 contract terms and benefit categories as live formulas in the main sheets,
  the profit sheets and the sensitivity blocks (66,544 formulas); a V4_Analytics sheet holds the
  Python-only analytics as labelled values, with a live check cell.
- **SAS retired** (`code/sas/README.md`): never executed, v2 only. Python is the production engine.

### Validation added
- `tests/e2e_excel_nonneutral.py`: Excel ties to Python **to the cent at NON-neutral v4 settings**
  (withhold, directed payments, acuity path, D&V waiting period / trend / pent-up), on cash, DE,
  every year and all 17 sensitivities. Three variants exercise every settlement branch:
  - corridor loss side (state pays the plan);
  - corridor gain side, crossing band 2;
  - MLR floor binding, with the withhold earn-back in the MLR base.

  A tie at neutral settings cannot catch a wrong v4 formula, and a single variant left the
  corridor's gain side untested.
- `tests/test_v4_model.py` (22 tests): neutrality, hand-calculated corridor, exact withhold
  timing identity, directed-payment pass-through, lagged risk adjustment, waiting period,
  break-even roots, portfolio base cell, the response identity with full gap closure, and the
  estimator recovering known parameters.
- `tests/test_v4_forecast.py` (14 tests): BF / chain-ladder identities, roll-forward conservation,
  waterfall steps against independent calculations, vintage reconstruction, and the registry
  (one vintage per as-of, reused run id refused).
- `tests/validation/trend_estimator_study.py`: the 30-seed estimator study (offline).
- Backtest: Test A unbiased (z +0.73 / +0.10 / −0.72). Test B now measured against the generator's
  truth, including the approved v4 trend and the v3 raw D&V fit. Test A stops if the v4 contract
  terms are non-neutral, because its simulator does not implement them.
- Two v3 governance tests rewritten for v4's state (they assumed the first approval was the
  active one, with a Med Supp trend REVIEW item). The new disposition test also covers the
  positive case.

### Fixes from the independent pre-release review
An independent reviewer worked on a separate copy. It found six verified defects, none in a
cash-flow or Excel formula. All are fixed and tested:
- **Production could release results on a set the gate had refused.**
  - The engine's production check now also requires this run's 03b status to approve the same
    hash, since 03b alone checks REVIEW dispositions.
  - The manifest releases results only when the gate approved and the reconciliation passed.
- **The approval did not cover the forecast's inputs.** The approval hash now includes the
  governed configuration files (claim-cost curves, trend benchmarks, forecast settings), and the
  forecast reads age slope and selection from the approved set, as the engine does.
- **The vintage registry was keyed on run id.** A fixed run id overwrote last month's vintage.
  It is now keyed on the as-of month, and a run id reused for a different month is refused.
- **The Monte Carlo estimator double-counted Med Supp approval risk.** It proposed the yearly SD
  as the level-shift SD too. The level SD is now the standard error of the long-run mean.
- **The "without MLR floor" statistic was mislabelled under a corridor.** It is now labelled by rule.
- **The waterfall's reconciliation test could not fail** (residual by construction). It is
  replaced by independent checks of the individual steps.
- **Also:**
  - the "higher rates" scenario now moves the earned rate with the hurdle rate;
  - the estimator correlates the Medicaid update with Medicaid's own trend;
  - the non-neutral Excel tie-out now covers the corridor's gain side and the binding floor;
  - several doc figures are corrected;
  - new limitations: approved lapse and curves equal the generator's values, waiting periods
    remove pent-up instead of deferring it, and large-claim excess carries no IBNR.

### Not done (cannot be done here, stated plainly)
- Kentucky exception DQ-0142 expired on 2026-06-30. Renewing it is a data-owner decision; the
  production end-to-end test still stops on it.
- The Databricks job and SAS have not been executed (no workspace, no licence).
- Every X input (benchmarks, filed age and plan relativities, contract terms, Finance sales plan,
  Monte Carlo history) and every J value needs Humana data.

## 3.0.0 — Humana-ready version

### Model
- **After-tax profit test** (`profit_test()` in `04_npv_engine.py`; `*_Profit` sheets in the workbook).
  Claims are paid on each line's lag pattern; the claim reserve and the Medicaid MLR remittance
  liability are held until paid; required capital is held as a percentage of annualized paid
  claims (so it runs off with the claim reserve); assets earn the investment rate; income is taxed
  at 21%. Distributable-earnings (DE) NPV
  is the new headline; the version 2 cash NPV is kept alongside it.
- **Anti-selective shock lapse** (Med Supp): two health classes. Shoppers (50% at issue, claim
  relativity 0.85) are 4× as likely as non-shoppers to take the shock lapse; the split is solved
  each anniversary so the block's lapse still equals the lapse study. The block's claim relativity
  rises from 1.000 to 1.030 by year 10 and 1.070 by year 20. *Judgment parameters.*
  - First specification sent all shock lapse to shoppers. A regression test (shopper cost = 1.0
    must equal version 2) failed by $749 and exposed that shoppers ran out by year 19, after which
    the block lapsed less than the study. The specification was changed to the propensity form
    before release.
- **Dynamic shock lapse** (Med Supp): shock = max(0, 3.5% + 0.5 × (rate increase − 7%)). Neutral at
  the filed increase; active in the rate-increase sensitivity and the Monte Carlo.
- **Medicaid rate cells** (Child / Adult / ABD) with mix and capitation relativities from the
  experience and normalization so the contract-level levers keep their meaning; loss-ratio and
  churn relativities are 1.0 because the experience shows no difference beyond 2 standard errors.
  **Budget-neutral risk adjustment** (plan acuity, 80% revenue capture).
- **Correlated Monte Carlo**: trend ↔ Med Supp rate shortfall −0.5, trend ↔ Medicaid rate update +0.5.
  With the switch off the draws are bit-identical to version 2.
- **Labels**: "Year-1 claim cost" renamed "Claim-cost level −5% / +5% (all years)" and "Yr-1 medical
  ratio" renamed "Starting medical cost ratio (all years)": both move the starting level, and trend
  carries the change into every year. The D&V pent-up factor is labelled "(year 1 only)".
- New tornado drivers: Med Supp anti-selection strength; Medicaid plan acuity.

### Bridge from version 2 (cash NPV → v3 cash NPV → v3 DE NPV)

| Step | Med Supp | Dental & Vision | Medicaid |
|---|---|---|---|
| Version 2 cash NPV | 924,382 | 32,998 | 12,977,412 |
| + anti-selective shock lapse | −219,656 | — | — |
| + dynamic shock lapse | 0 | — | — |
| + rate cells and risk adjustment | — | — | 0 |
| **= v3 cash NPV** | **704,726** | **32,998** | **12,977,412** |
| + claims paid on lag (reserves earn the hurdle) | +84,285 | +8,721 | +3,238,253 |
| + assets earn 4.5%, not 8% | −53,427 | −5,513 | −1,414,746 |
| + required capital | −33,312 | −2,068 | −1,279,863 |
| + federal income tax | −163,790 | −8,182 | −3,466,150 |
| **= v3 DE NPV** | **538,482** | **25,956** | **10,054,905** |

Expected value (Monte Carlo mean, cash / DE): Med Supp 691,740 / 528,101; D&V 34,141 / 26,857;
Medicaid 11,403,044 / 8,787,768 (the MLR floor costs 1,709,882 of cash expected value).

### Data and governance
- **Production mode** (`config/run_config.json`): source extracts through `config/source_mapping.json`
  (`00a_ingest_source_extracts.py`, strict: missing columns, unmapped codes and unparseable months
  stop the run); synthetic generators and synthetic-only validation skipped.
- **Relocatable data and outputs** (`NPV_DATA_DIR`, `NPV_OUTPUT_DIR`, `code/python/kitpaths.py`) so
  member-level data lives on governed storage, not in the repository. Found by the production
  end-to-end test: a missing `parents=True` on the output folder.
- **Assumption governance** (`03b_assumption_governance.py`): experience indications with
  limited-fluctuation credibility; payment pattern and tail test on pooled claims; rate-cell
  relativities moved off 1.0 only beyond 2 SE; approval gate on the SHA-256 of the assumption set.
- **Run manifest** (`08_run_manifest.py`): code, config and input hashes, environment, approvals,
  step status, results.
- **Claim-cost curves to config** (`config/claim_cost_curves.json`): the Med Supp trend
  normalization no longer reads the synthetic truth file; the trend and lapse studies run without it.

### Engineering
- **Deterministic SQL kit**: DuckDB runs single-threaded. With parallel threads the gold PMPMs
  differed in the 16th digit between rebuilds, which the SARIMA benchmark amplified to the 5th
  decimal. SARIMA holdout scores therefore differ from version 2's quoted values by up to 0.0003;
  model selection and every trend the engine uses are unchanged.
- **Databricks notebook** reads its Med Supp assumptions and paid-through month from the shared
  configuration (they were hard-coded), implements the v3 mechanics, and raises if its base cell
  does not tie to the engine (it previously only printed a label). Spark workers are pinned to the
  running interpreter for local runs.
- **Databricks Workflow template** (`deploy/databricks_job.json`) and LibreOffice init script.
- **Workbook**: 16 sheets, 62,849 formulas; mode-aware (synthetic commentary never appears in a
  production workbook; the Backtest sheet states when the backtest was not part of the cycle).
- **Tests**: 62 (17 Phase 1 controls, 31 model, 14 production path) plus the two-phase
  `tests/e2e_production_run.py`.
- SAS programs labelled as the version 2 mirror (not executed).

### Fixes from the independent pre-release review
An independent review of the release candidate found, among others, these defects. Status after a
second verification pass: fixed unless marked otherwise.
- **Paid-through month hard-coded in SQL 03** (`'2026-02'`) while other steps used the config or
  `MAX(paid_month)`. The SQL kit now renders every parameter from configuration (paid-through
  month, pooling points, large-claim thresholds, A/E expected basis) and stops on any unfilled
  placeholder; the trend study uses the configured month. Outputs on the synthetic book are
  byte-identical, and the data contract still stops any feed whose latest paid month differs.
- **A failed run could leave the previous run's results in its manifest.** Every run now starts by
  clearing the kit's own artefacts (`00s_start_run.py`, also the first Databricks task), and the
  manifest includes only artefacts written after the run started. Tested: phase 1 of the production
  end-to-end test stops at the gate and its manifest shows no results.
- **The approval gate and the engine could read different assumption files.** One resolver
  (`assumption_control.py`) now serves the gate, the engine, the workbook and the notebook, and the
  engine re-checks approval in production (tested).
- **The production test compared only NPVs**, which come from the assumption file. It now compares
  all 12 data-derived outputs as well.
- **Backtest simulator reused engine helper functions.** It now sets up classes, shock lapse, rate
  cells and risk adjustment with its own code from the assumption file.
- **Medicaid headline omitted the MLR-floor cost.** README and Dashboard now show the Monte Carlo
  expected value next to the deterministic NPV.
- **"medical_cost_relativity" was in fact a loss-ratio relativity** (applied on top of the
  capitation relativity); renamed `loss_ratio_relativity`, with the data-book conversion stated.
- **"Pooling charge" was described but not computed.** The governance step now reports it
  (Medicaid 2.5%; Med Supp and D&V 0.0%), and the text says when to add it back. *Reported, not
  applied:* the kit's claim-cost assumptions are all-in, so nothing is added automatically.
- **Parquet ingest failed on nulls**; fixed with Arrow-backed types, and tested.
- **Trend tolerance (1 point) was immaterial-looking but material** (0.8 pt ≈ −70% NPV): now 0.25
  point; the two resulting REVIEW items are dispositioned in the approval record.
- **Required capital was released while claim reserves were still running off**; now held on
  annualized paid claims (a proxy; it tapers with payments rather than matching the reserve exactly).
- **Anti-selection could be double counted on real data** (trend and select factors fitted on block
  experience already contain past deterioration). *Disclosed, with the calibration rule*, in the
  model documentation; there is no data here to do more.
- **The Monte Carlo rate driver is symmetric.** Kept on purpose (the assumption is the expected
  approval) and renamed `medsupp_rate_increase_deviation_sd` so it no longer reads as one-sided.
- **REVIEW items needed a recorded disposition.** The approval record now carries
  `review_dispositions`, and an approval without one for every REVIEW item does not count (tested).
- **Databricks job had no step status for the manifest.** Results are now released in the manifest
  only when this run's Excel reconciliation passed (marker written by step 05), in both runners; the
  Spark notebook runs on its own cluster with no pinned libraries; the notebook judges exception
  expiry like the gate; every step reads the run configuration through one function.
- **Exception expiry was judged on the operator-set run date**; production now uses today's date.
- **Databricks**: the job no longer installs PyPI pyspark over the runtime; DuckDB is built on local
  disk and copied to the output location; the unused job parameter is gone; the notebook raises
  instead of asserting.
- **Wording**: no longer claims the Databricks path was exercised, that the simulator "independently
  finds" anti-selection, or that anti-selection is the largest uncertainty (trend is).

### Validation results
- Backtest z-scores: Med Supp +0.14, D&V +0.11, Medicaid −0.72 (unbiased); version 2 at its own step
  +1.65 / +0.11 / −0.71; version 1 bias +2.8% / +9.8% / −12.3%.
- Test B: estimated trend moves Med Supp NPV by −$490,374. Test C: parameter-to-process ratio
  Med Supp 6.2×, D&V 1.8×, Medicaid 4.4× (version 2 quoted 10.5× for Med Supp under independent
  shocks; correlation narrows the parameter spread).

## 3.0.0-phase1 — real data and enforced gates
Data contract, enforced data gate with exception register, ledger tie-out; 17 tests. Model results
identical to version 2.

## 2 — monthly model
Monthly projection with the real contract mechanics; backtest unbiased on all three lines.

## 1 — annual model (archived)
Annual projection with mid-year conventions; backtest found it biased (Medicaid −12.3%: new members
never churned in their first year).

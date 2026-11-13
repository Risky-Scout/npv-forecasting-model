# NPV Forecasting Model v4.4.0 — Model Documentation

Prepared in the spirit of ASOP No. 56 (Modeling), No. 41 (Actuarial Communications), No. 23 (Data
Quality) and No. 25 (Credibility Procedures). All results quoted are on a synthetic practice book
with assumptions from public sources, statute and documented judgment (§5); they are not Humana
results or Humana assumptions.

## 1. Purpose and intended use

**Purpose.**
1. Estimate the economic value of three things, for pricing, product and bid decisions, together
   with the drivers and the range of that value, and (v4.2) the price or bid decision that meets
   a risk-based return target:
   - a newly issued Medicare Supplement portfolio (model points by issue age and plan)
   - a newly issued individual Dental & Vision cohort
   - a new-state Medicaid managed-care contract
2. (v4) Forecast the existing book by calendar month and explain each period's actual against
   expected. This is the monthly work of an Analytics / Forecasting team.

**Measures.**
- *Cash NPV*: pre-tax cash flows at incurred timing, discounted at the hurdle rate.
- *DE NPV*: after-tax distributable earnings, after claim payment lag, claim reserves, the
  Medicaid MLR settlement liability, required capital and investment income, discounted at the
  hurdle rate. DE NPV is the decision measure.
- *Forecast*: members, premium, incurred and paid claims, and loss ratio, by month and segment.

**Intended users.** Forecasting and pricing actuaries, finance partners, model risk.

**Not intended for.** Statutory or GAAP reserving, rate filings, capital planning or financial
reporting. The profit test is a pricing-grade approximation (§8). The in-force forecast is a
management forecast; it is not an IBNR reserve.

## 2. Model structure

```
00s clean start (clear previous artefacts, never the vintage registry)
source extracts ─► 00a ingest ─► 01 SQL kit (bronze → silver → gold, completion, lapse, A/E)
                 ─► 01b controls gate                                            [stops on failure]
                 ─► 02 trend study (normalized, rolling origins, bootstrap SE), 03 lapse GLM
                 ─► 03b assumption governance (credibility, trend blend, approval) [stops if unapproved in production]
                 ─► 04 NPV engine: cash flows (+ contract terms) ─► profit test ─► tornado, Monte Carlo
                       (level + process risk + filing response) and attribution, break-even, planning
                       scenarios, Med Supp model points and rating bases, judgment sensitivity, bridge,
                       v4.2 risk-based pricing solve
                 ─► 09 in-force forecast ─► 10 A/E waterfall ─► 11 forecast vintages (registry)
                 ─► workbook, recalculation, Excel = Python reconciliation        [stops on any difference > $0.01]
                 ─► [synthetic] Excel = Python at NON-neutral v4 settings and at the recommended prices
                 ─► 12 drift monitoring ─► Databricks notebook ─► run manifest
synthetic mode adds: data generator, archived v1 engine, backtest (Tests A–C), test suite,
                     the Databricks job definition executed locally task by task
```

One assumption file (`code/python/npv_assumptions.json`) drives the Python engine, the Excel
workbook, the Databricks notebook and the forecast. Feature switches (`global.features`) turn each
mechanic on or off:
- all v3 switches off reproduces version 2;
- the v4 switches are each neutral at their neutral settings, so they reproduce v3.

## 3. Data

| Feed | Grain | Used for |
|---|---|---|
| Members | member | issue month and age, plan, state, rate cell, termination month and reason |
| Member months | member × month | exposure and premium |
| Claims | claim | incurred and paid month, paid amount, service category |
| Finance control totals | line × month × control | ledger tie-out of paid claims and premium |

**Contract, data gate, ledger tie-out, lineage and mapping are unchanged from v3:**
- **Contract**: `config/data_contract.json`; a breach stops the run.
- **Data gate**: `dq_policy.json`, `dq_exceptions.json`, with exceptions that expire.
- **Ledger tie-out**: `recon_items.json`.
- **Lineage**: SHA-256 hashes in the manifests.
- **Mapping**: `source_mapping.json`, strict.

The required extracts and external inputs are in [DATA_REQUEST.md](DATA_REQUEST.md).

## 4. Methods

### 4.1 Projection conventions (all lines)
- **Time step:** monthly. Annual rates convert as q_m = 1 − (1 − q)^(1/12).
- **Premium:** collected in advance from start-of-month in force. Commission and premium tax ride
  on premium.
- **Decrements:** a lapse takes effect at the end of the paid month. Deaths occur mid-month, so
  exposure = in force − ½ deaths.
- **Claims and maintenance:** mid-month.
- **Acquisition and start-up:** paid at time 0.

Deterministic growth indices are (1 + rate)^(t−1), bit-identical to v3. Monte Carlo paths replace
them with Π(1 + rate + deviation_s).

### 4.2 Medicare Supplement
- **Base cell:** Plan G issued at 65, attained-age rated.
  - Premium grows with the age step and the filed rate increases.
  - Claims grow with the age slope and trend, and carry select factors 0.78 / 0.90 / 1.00.
- **Lapse:** by duration. At each anniversary an extra shock lapse is added and concentrated in
  month 1, so the year's total equals base + shock. The shock is dynamic:
  3.5% + 0.5 × (increase − 7.3%), floored at 0.
- **Anti-selection:** two classes from issue.
  - Shoppers are 50% of the cohort at claim relativity 0.85; non-shoppers are at 1.15, so the
    issue mix averages 1.00.
  - Both classes take the base lapse. The extra anniversary lapse is split so a shopper is 4× as
    likely to take it, solved so the block total equals the lapse study.

**Model points (v4).**
- **Grid:** issue-age bands 65 / 66–69 / 70–74 / 75+, each at its mean issue age, × plans
  G / N / HDG.
- **Mix:** 2024–25 issues read off the synthetic book (J; a sales-mix placeholder, disclosed in §5).
- **Plan F is excluded from new business.** Since 2020 it cannot be sold to people newly eligible
  for Medicare.
- **Starting point:** each point starts at the attained-age premium and claim cost
  (× (1 + step)^(age − 65) and × (1 + slope)^(age − 65)) times its plan relativities (P: Maryland
  2026 rates, N 0.77, HDG 0.18; claim relativity = premium relativity), and carries every
  base-cell mechanic. The base point must equal the headline cohort (asserted).

**Rating bases (v4.1).** Georgia, Arizona, Florida and Missouri prohibit attained-age rating.
`medsupp_rating_bases()` reprices every model point on three bases with the same claims:
- **attained-age:** as above;
- **issue-age:** a level premium by issue age, with the rate increase equal to trend
  (6.5%) because there is no age step;
- **community:** one premium for all ages, with the rate increase equal to trend.

The issue-age and community starting premiums are solved (Brent) so the mix-weighted PV loss
ratio equals the attained-age portfolio's. The comparison is therefore about timing and cross-
subsidy by age, not price level. It is reported on cash NPV, because an issue-age basis needs an
active-life reserve, which the profit test does not hold (§8).

### 4.3 Dental & Vision
- **Base:** a cohort of individual policies. No mortality, high early lapse, year-1 pent-up
  factor 1.28.
- **Benefit categories (v4)** — preventive, basic, major and vision. For each category:
  - share of ultimate cost (J: read off the synthetic book's paid claims by service category; a placeholder, disclosed in §5; no effect at base)
  - trend add-on (J, 0)
  - year-1 pent-up relativity (J, normalized so the blended factor equals 1.28)
  - waiting period (product design, X; 0 in the experience)
- **Waiting periods (v4.1):** no benefit for a category before its waiting period ends, and its
  pent-up load applies to its first 12 months of eligibility instead of policy year 1. Demand is
  deferred, not removed. Lapse and anti-selection do not respond to the waiting period, so the
  value of a waiting period is an upper bound.
- **Neutral settings** reproduce the single-category model.

### 4.4 Medicaid
- **Contract:** new state, 10,000 starting members, 2,800 joiners a year.
- **Membership:** by rate cell (Child / Adult / ABD), each with its own churn. Joiners arrive in
  the joiner mix and churn from the following month.
- **Revenue and medical:**
  - Revenue = Σ cell member months × capitation relativity × blended capitation × risk-adjustment
    factor.
  - Medical = Σ cell member months × capitation relativity × **loss-ratio relativity** × blended
    medical PMPM × plan acuity.
  - Relativities are normalized so the blended year-1 values equal the contract-level assumptions.
- **Settlement:** paid six months after each contract year. Years after the five-year term are
  weighted by a 75% renewal probability.

**Contract terms (v4; X from the state contract).** The v4.1 public basis carries a 2% quality
withhold with 85% expected earn-back (Louisiana rate certification and quality review; 42 CFR
438.6). Directed payments, an acuity path and a corridor are contract-specific and are neutral.
- **Quality withhold** w with expected earn-back e:
  - capitation received = gross × (1 − w);
  - e × w × gross is received with the year's settlement;
  - premium tax is on gross.
- **Directed payments:** a PMPM pass-through, added to both revenue and medical (both sides of the
  MLR) and taxed.
- **Acuity path f_t:** medical × f_t; revenue × (1 + capture × (f_{t−lag} − 1)). Risk adjustment
  recognizes an acuity shift only after its lag, e.g. after a redetermination wave.
- **MLR rule:** settlement on the MLR base (revenue received + withhold earned), one of:
  - **floor:** max(0, 85% × base − medical);
  - **risk corridor:** symmetric bands b1 < b2 around a target T. The state takes (1 − s2) of the
    gain or loss between b1 and b2 and (1 − s3) beyond b2.
- **Net settlement** = rule amount − withhold earned. It is negative when the state owes the plan.

### 4.5 Profit test
End-of-month accounting, defined in `04_npv_engine.py`, `profit_test()`.
- Paid claims follow the lag pattern.
- The claim reserve and the MLR settlement liability are held until paid. In v4 the liability can
  be negative (a receivable): it then earns no investment income.
- Required capital = capital % × 12 × paid claims.
- Investment income is at the earned rate (4.0%); federal income tax is 21% (IRC §11).
- **DAC tax (v4.1, IRC §848):** each tax year (= policy year) a share of net premiums is
  capitalized (Med Supp 9.2%; D&V and Medicaid 0, see §5) and deducted over 180 months with the
  half-year convention: 1/30 in the year capitalized, 1/15 for the next 14 years, 1/30 in the
  16th. The extra tax τ × (capitalized − amortized) is paid at the end of each tax year, and the
  deductions run on after the cohort has lapsed. It is a timing cost; its PV is deducted from DE
  NPV and shown as its own bridge step. Excel rebuilds the schedule with live formulas.
- **Identity (tested):** no tax, no capital, earned rate = hurdle and immediate payment give
  DE NPV = cash NPV.

### 4.6 Sensitivities, Monte Carlo, break-even, scenarios, bridge
- **Tornado:** 17 drivers, cash and DE.
- **Monte Carlo:** 10,000 scenarios.
  - **Parameter risk (v3):** correlated level shifts of lapse, trend, the Med Supp rate deviation
    and the Medicaid update, plus a 0/1 Medicaid renewal.
  - **Process risk (v4):** an AR(1) yearly trend deviation shared by all lines (SD 1 pt, φ 0.5),
    plus yearly noise on Med Supp approvals and Medicaid updates (SD 1 pt each). It runs on a
    separate random stream.
  - **Rate-filing response (v4)** for Med Supp and D&V, both with a 2-year lag (v4.1: a filing
    effective at the start of year s + 1 is prepared during year s from experience through
    year s − 1, so a 1-year lag would use claims not yet known). Each filing
    closes a share h = 0.5 of the gap between the claim-index deviation visible in lagged
    experience (level plus process, against the priced trend) and the premium deviation already
    in force. This replaces v3's −0.5 trend / rate-shortfall correlation, now 0; the engine
    refuses both together.
  - **Attribution:** the v3 basis, then + process risk, then + response, then h = 0 / 0.25 / 0.75.
  - **MLR option cost:** a no-floor rerun with common random numbers.
- **Break-even:** the level of each key driver at which cash and DE NPV are zero, found with
  Brent's method on full re-projections. Headroom is break-even minus base, a signed direction.
  v4.1 reports the base NPV and its **position** (above or below break-even): below means the
  product already fails the measure and |headroom| is a shortfall. D&V also has a year-1 premium
  multiplier, the premium the product needs.
- **Judgment sensitivity (v4.1, ASOP 56 §3.6.1):** judgment assumptions run at the low and high
  ends of their documented ranges (`npv_judgment_sensitivity.csv`, 32 sweeps), with a flag when
  DE NPV changes sign.
  - The Med Supp age-curve sweeps re-derive the rate increase, so they test the curve and not an
    inconsistency.
  - The dynamic-lapse slope is swept at a filed increase 2 pt above the reference, because at
    the reference it has no effect by construction.
  - Payment patterns are shifted one month faster or slower.
  - The D&V DAC classification is swept at 0% / 9.2%.
  - Medicaid churn by cell is MACPAC's relative churn in the v4.3 base (19 : 28 : 9, scaled to
    the 25% blend: about 22.6% / 33.3% / 10.7%) and is swept against equal churn. This is
    material: at equal churn DE is −$1.09M against +$2.71M at the base, because the mix drifts
    toward ABD members with admin at a flat PMPM. The cell-mix and ABD loss-ratio sweeps run at
    MACPAC churn, because at equal churn the normalization makes them inert.
- **Monte Carlo parameter sensitivity (v4.1):** every volatility (the four level-shift SDs and
  the three process-risk SDs), the AR(1) φ and the trend / Medicaid update correlation, nine
  parameters, at the ends of their ranges
  (`npv_mc_parameter_sensitivity.csv`). Med Supp P(DE < 0) runs from 0.35% to 9.7%, driven by the
  trend level-shift SD.
- **D&V product options (v4.1):** waiting periods on the base assumptions (`dv_product_options.csv`).
  These are upper bounds.
- **Planning scenarios:** named multi-driver shocks (`config/planning_scenarios.json`).
- **Bridge:** v2 → each deterministic feature → v4 cash NPV → profit-test layers → DE NPV.

### 4.7 Trend study and assumption (v4)
- **Fits:** a log-linear fit with month dummies on completed PMPM for each series.
  - Med Supp is normalized for age, selection and plan; D&V (v4) for the year-1 pent-up load and
    product (`config/claim_cost_curves.json`).
- **Model selection** uses the average of PMPM error and absolute bias over 5 rolling origins
  (training lengths 36 down to 24 months, each with a 12-month horizon).
  - **Persistent bias** is flagged when all three hold: the mean bias exceeds 5%, it exceeds
    2 SE, and at least 80% of origins have the same sign. It becomes a REVIEW item.
  - A one-year holdout bias is INFO.
- **Standard error:** a member-level bootstrap (400 resamples of members with their whole claim
  history) of the fitted slope, multiplied by 1.16 (v4.1). That factor is 1 / 0.86, the lowest
  ratio of bootstrap SE to true sampling SD in the seed study, so Z is not overstated.
  Residual-based (HAC) SEs are reported but not used, because they understated the true SD by
  25–40%.
- **Indication:** Z × fit + (1 − Z) × benchmark, with Z = sd_b² / (sd_b² + SE²), rounded to
  0.05%. A gap above 0.25 pt is REVIEW.
- **Validation:** [validation/trend_estimator_study.md](validation/trend_estimator_study.md).

### 4.8 In-force forecast (09; `forecast_lib.py`)
- **Scope:** the existing book, by calendar month for 36 months, by line × state × plan or rate
  cell × duration band.
- **Membership:** rolled forward per segment with mortality and the **book's own** persistency:
  - Med Supp and D&V: lapse by duration and the anniversary shock from the book's
    credibility-weighted lapse indications (03b, v4.1). The approved file is the new-business
    pricing basis, a different population; using it biased D&V member months by −2.4%.
  - Medicaid: the measured non-wave churn, not the new-state contract.
- **New sales:** from `config/forecast_config.json`. The synthetic default (v4.1) is the
  trailing-12-month new issues, grown at the observed year-over-year growth (capped at ±30%) from
  the window's midpoint. The flat average it replaced lagged a growing book. The Finance plan
  replaces it in production.
- **Premium:** in-force PMPM × rate actions on the anniversary or state rate year.
- **Claims:**
  - Base PMPM by segment from the latest 12 completed months. Completion is Bornhuetter-Ferguson
    for lags 0–2 (a-priori from a log-linear fit on mature months) and chain-ladder after.
  - Limited-fluctuation credibility toward the line, with an off-balance correction.
  - Trended with the approved trend (Med Supp: plus aging and selection from the book's curves,
    `config/claim_cost_curves.json`). Medicaid uses the book fit.
  - Plus the pooling charge, credibility-weighted (v4.1) by limited fluctuation:
    Z = min(1, √(n_large / n_full)) on the count of claims above the pooling point, with
    n_full = 1,082 × (1 + CV²). CV is the excess severity's coefficient of variation, 1.0 below
    two large claims; the charge is an aggregate, so the standard scales with severity, as for
    the segment base. The complement is a judgment charge. A Medicaid charge measured from a
    single large claim had biased the incurred forecast by +2.8%.
- **IBNR on the excess (v4.1):** pooled completion leaves the excess over the pooling point
  unreserved. Excess IBNR = (1 − completion factor) × pooling charge × a-priori PMPM × member
  months, allocated to segments by member months.
- **Paid claims** = incurred on the payment pattern, plus run-off of the IBNR held at the as-of
  date.
- **Assertions:** members are conserved, and segment IBNR ties to line IBNR.

### 4.9 Actual vs expected waterfall (10)
The prior vintage's forecast (synthetic: rebuilt as of 2024-12 from the data as it stood then)
is set against actual 2025 incurred claims and premium. The variance is split into:
- volume (in-force retention, new sales)
- mix
- base re-estimate (hindsight completion)
- trend and morbidity
- large claims (excess over the expected pooling charge)
- prior-period development

The components sum to the total by construction, because trend and morbidity is the residual.
The other steps are tested against independent calculations (`tests/test_v4_forecast.py`).

### 4.10 Forecast vintages (11)
- **Registry:** each production cycle appends its forecast to `NPV_VINTAGE_DIR`, keyed on the
  as-of month and never cleared. Re-running a cycle replaces its own vintage; reusing a run id
  for a different as-of month stops the run (v4.1).
- **Scoring:** every stored vintage is scored against completed actuals (at least 3 months of
  run-out) by horizon bucket (1–3, 4–6, 7–12, 13–24 months).
- **Bias test:** mean signed error across vintages with its standard error. Vintages overlap, so
  the stated SE is understated.

### 4.11 Drift monitoring (12)
- **Claims A/E band:** ± k × √((1 + CV²) / N), compound Poisson.
- **Termination band:** ± k / √(expected terminations), Poisson.
- **Levels:** WATCH on a single breach; ALERT after `persistence` consecutive same-side breaches.
- **Also collected into the same list:** persistent forecast bias, undispositioned REVIEW items,
  and break-even headroom below a threshold (WATCH).
- **Below break-even (v4.1):** a line whose base DE NPV is negative is one ALERT row, listing the
  level each driver would need, however far it is from break-even.
- **Rules:** `config/monitoring_rules.json`.

### 4.12 Risk-based pricing solve (v4.2, company goals v4.4; `pricing_solve()`, `config/pricing_targets.json`)
- **Target.** P(DE NPV < 0) ≤ τ in the Monte Carlo, i.e. the τ-quantile of DE NPV = 0, with τ = 10%
  (Humana's pricing policy; the default is judgment). The quantile is solved with Brent's method
  on the full Monte Carlo. Common random numbers make it move smoothly with the lever. DE NPV = 0
  alone is the minimum acceptable price, not the target: priced there, a line fails the hurdle in
  about half of outcomes.
- **Trend** stays at its best estimate. The margin pays for the risk; it does not change the
  assumption.
- **Med Supp:**
  - The lever is the Plan G issue-age-65 year-1 premium. A price that already meets the target is
    never cut; the minimum is reported as headroom.
  - Each model point is priced to the base cell's DE margin (DE NPV / PV premium), subject to the
    65% minimum loss ratio. Where the margin needs a lower loss ratio, the cell is priced at the
    cap and reported as not reaching the target (High-Deductible G).
  - Plan relativities and the implied attained-age step are compared with Maryland's 2026 filed
    rates; values outside them are flagged REVIEW.
  - The risk target is also measured on the whole portfolio: a Monte Carlo per model point with
    common random numbers, mix-weighted. P(DE < 0) is 28.4% as priced and 3.8% at the
    recommended cell prices.
  - The result relies on the filing response: with none, the base cell's P(DE < 0) is 24.7%.
- **D&V:**
  - The lever is the year-1 premium, rounded up to whole cents a month and compared with the
    public reference.
  - Lapse does not respond to price level in the model, so the solve is repeated with lapse 10%
    higher as a robustness check.
- **Medicaid:**
  - Capitation is state-set (42 CFR 438.4, ASOP 49), so price is not a lever.
  - The solve reports, one lever at a time, the starting cost ratio and the admin PMPM that would
    meet the target. Each is compared with its public range: the cost ratio through the PV MLR it
    produces, which is the measure Milliman reports. A level better than the best published is
    not achievable, and a lever that already meets the target is reported achievable.
  - If no lever is achievable, the recommendation is "do not bid at these terms". The 85% MLR
    floor is modelled (remittance), so it caps the upside in every solve.
- **Company goals (v4.4, `goals` in `config/pricing_targets.json`).** Goals are policy, kept
  apart from the assumptions: every assumption stays a best estimate, and the goals decide only
  the levers. No assumption is moved to make a goal come out.
  - *Safety:* P(DE < 0) ≤ τ on every line. The average DE in the worst τ share of scenarios
    (tail mean) is reported beside it, because the probability says how often a line loses and the
    tail mean says how much. Medicaid's worst-10% average is −$30.3M as bid.
  - *Profit:* DE NPV ≥ 0 at the 8.5% hurdle is a return of at least the hurdle on required
    capital. There is no second margin target: the risk target already prices the margin to each
    line's risk, and a second target would count it twice.
  - *Members:* no in-force increase above 10% in one year (Med Supp, D&V). The cap acts inside the
    Monte Carlo filing response: a filing that would exceed it is held at the cap and the rest of
    the gap carries to later filings, again capped. Base filings (7.3%, 3.5%) sit inside it, so the
    deterministic results are unchanged; Med Supp P(DE < 0) moves from 3.0% to 3.6%, the price of
    protecting members in adverse years. A reprice applied to existing members on top of the base
    increase is phased (D&V: 10.9% in one filing, so 2 filings).
  - *Market:* at most 5% above a **credible** reference (a hard limit). Against a single
    low-confidence filing (D&V) the limit is a REVIEW flag only. If the limit binds, D&V benefit
    designs are tried, least restrictive first and capped at the market norm, before the line is
    reported as unable to meet every goal. Price comes first, so members keep immediate access to
    care wherever price alone can meet the target.
  - *Medicaid terms:* each negotiable term (a symmetric risk corridor around an 88% MLR) is tested
    alone. The corridor cuts the worst-10% average DE from −$30.3M to −$9.9M but the chance of a
    loss only from 47.2% to 45.7%: it limits how much the contract can lose, not how often. A term
    that meets the target makes the recommendation "bid only if the state agrees to it".
- **Verification:**
  - `tests/test_v4_model.py` checks that the τ-quantile is zero at the solved minimum, that
    P ≤ τ at the recommendation, the constraints, the cap logic and the bid rule; v4.4 adds the
    goals (policy file, not assumptions), the increase cap (neutral when it cannot bind, held when
    it does, the gap carried), the phasing and tail-mean hand calculations, the per-goal report,
    and a credible-reference run that moves D&V to the least restrictive design that meets every
    goal.
  - `tests/e2e_excel_pricing.py` rebuilds the workbook at the recommended premiums and requires
    the to-the-cent tie and the pricing table's DE NPV.

## 5. Assumptions

**The full basis is in [ASSUMPTION_BASIS.md](ASSUMPTION_BASIS.md),** generated from
`config/assumption_basis.json`. It gives every NPV assumption's value, status, source and exact
figure, ASOP reference and sensitivity range. `tests/test_v4_model.py` fails if any assumption is
missing, differs from the register, lacks a source (public or statutory) or lacks a range
(judgment).

**Standard.** The ASOPs prescribe a process, not values:
- ASOP 25 §3.3 (relevant experience)
- ASOP 8 §3.4 (health assumptions)
- ASOP 49 §2.1 / §3.2.9 / §3.2.12 (Medicaid)
- ASOP 23 (data)
- ASOP 56 §3.6.1 (sensitivity)
- ASOP 41 (disclosure)
- ASOP 58 (ERM; it replaced the repealed ASOPs 46 and 47)

There is no Humana data, so values come from public sources. **The synthetic practice book is
not relevant experience for a Humana block, so its experience is given no credibility.** `03b`
still reports its indications, and the approval records the disposition.

**Values linked to the synthetic book (disclosed).** 18 register entries carry a
`synthetic_link`, listed in ASSUMPTION_BASIS.md:
- **4 are read off the synthetic book** as placeholders for Humana inputs: the Med Supp
  model-point mix and issue ages, the D&V category cost shares and the Medicaid cell mix. None
  affects the headline results. The mix affects only the portfolio, the category shares only the
  waiting-period options, and the Medicaid mix the Medicaid result (churn differs by cell; swept against the MACPAC national mix).
- **13 equal the generator's own values.** They are kept because a public figure (6) or a
  judgment range (7) supports them, so for them the source is a reasonableness check, not the
  derivation.

`tests/test_v4_model.py` recomputes the list from `data/true_assumptions.json`. The trend
normalization and the in-force forecast also use the generator's claim-cost curves
(`config/claim_cost_curves.json`) and payment pattern.

**Status counts (89 register entries, covering `npv_assumptions.json`):** public source 30,
statute / regulation 4, derived for consistency 4, judgment 41 (each with a range, and each run
across it: 32 sweeps in `npv_judgment_sensitivity.csv`, the tornado, 9 parameters in
`npv_mc_parameter_sensitivity.csv`,
the filing-response attribution and the per-cell model-point table), model setting 10.

| Key assumption | Value | Status | Basis |
|---|---|---|---|
| Hurdle rate / earned rate | 8.5% / 4.0% | P | Damodaran Jan 2026 (7.84%) + 10-yr Treasury 4.90%; NEAM health book yield 4.18% |
| Federal tax | 21% | S | IRC §11(b) |
| DAC tax | Med Supp 9.2% of net premiums; D&V, Medicaid 0 | S | IRC §848, 180-month amortization; classification to be confirmed by Corporate Tax |
| Med Supp trend | 6.5% | P | 2026 Trustees Report Table II.D1: Part B prices 2.0% × volume/intensity 4.4% |
| Med Supp claim age slope / premium age step | 3.8% / 3.0% | P | Milliman Plan G cost 1.45 at 75 vs 65, KFF Part B by age; Maryland 2026 Plan G rates, five carriers 2.7–3.5% |
| Med Supp rate increase | 7.3% | D | (1.065 × 1.038 / 1.03) − 1: holds the ultimate loss ratio level |
| Med Supp premium / claim cost | $1,980 / $1,536 | P | KFF Plan G $2,323 all ages; NAIC loss ratio 84.7%, Gen Re 87.1% |
| Med Supp plan relativities | N 0.77, HDG 0.18 | P | Maryland 2026 rate guide (HDG from one carrier, low confidence) |
| Med Supp lapse | 14 / 11 / 8.5% | P | Gen Re open-block 11% incl. mortality |
| Med Supp mortality | q65 0.0108, growth 9.1% | P (+J selection) | SSA 2022 period unisex × 75% |
| Med Supp commission | 20% / 10% | S | Model regulation: first year ≤ 200% of renewal |
| Premium tax (Med Supp, D&V) | 2.25% | P | NAIC chart (GA, LA) |
| Med Supp select factors, shock lapse, anti-selection, expenses per policy | as filed | J | No public basis; ranges in the register |
| D&V trend | 3.5% | P | ADA HPI; Segal 3.5–4.5% |
| D&V premium / claims / maintenance | $468 / $330 / $50 | P | EMI filing; Milliman for NADP loss ratio and admin |
| D&V lapse | 30% flat | P | Filed individual dental actuarial memoranda (low confidence, 2013) |
| D&V rate increase | 3.5% | D | Equal to the claim trend (v4.3): the 2013 memo filed 3% against its own 3% trend |
| D&V pent-up, commission, acquisition | as filed | J | No public basis |
| Medicaid capitation | $510 PMPM | P | Louisiana SFY2025 $510.09 (excl. directed payments) |
| Medicaid trend = rate update | 4.5% | P / D | AHCCCS CYE2027 3–4% + acuity; NHE +5.0%; ASOP 49 §2.1 |
| Medicaid cost ratio / admin / tax | 88% / $40 / 2.3% | P | Milliman MCO results 2024–25 |
| Medicaid quality withhold / earn-back | 2% / 85% | P | Louisiana CY2022 rate certification and quality review; CMS MMP 83% |
| Medicaid MLR floor | 85% | S | 42 CFR 438.8 |
| Medicaid churn | 25% | J (public anchor) | MACPAC 21% (2018), judgment uplift for redeterminations |
| Medicaid cell relativities | 0.575 / 0.946 / 3.042 | P | MACPAC MACStats FY2023 |
| Medicaid churn relativities by cell | 0.905 / 1.333 / 0.429 | P | MACPAC 12-month churn, children 19%, adults 28%, disabled 9%, over the all-enrollee 21% (v4.3) |
| Required capital | 10% / 6% / 15% | J | NAIC H2 factors at a ~400% RBC target |
| Monte Carlo parameters, filing response | — | J | Calibrate with `estimate_mc_parameters.py` |

## 6. Validation

| Test | What it shows | Result |
|---|---|---|
| Excel rebuild, approved set | Formulas and reading of the specification | Ties to the cent: cash, DE including the DAC tax schedule, every year, 17 sensitivities (66,860 formulas, 0 errors) |
| **Excel rebuild, non-neutral v4 settings** | That the v4 formulas (withhold, directed payments, acuity path, corridor, D&V categories and waiting periods) are right, which a tie at neutral settings cannot show; three variants cover the corridor loss side, gain side and floor binding | Ties to the cent in all three |
| Backtest Test A | Mechanics and timing unbiased; the simulator implements every Medicaid contract term (v4.1) | z = +1.11 / +0.75 / −0.49 |
| Backtest Test B (against the generator's truth) | NPV error of the estimation process; it can only measure values that differ from the generator's (13 equal it) | Approved public Med Supp trend (6.5%) vs the generator's arbitrary 6.0%: −$261,650; D&V public 3.5% = generator 3.5%: $0; v3 raw D&V fit +$21,993 |
| Backtest Test C | Parameter vs process risk | 3.2× / 1.0× / 5.5× |
| **Trend estimator study** (30 seeds) | Estimator bias, SE calibration, blend vs raw | Normalized fits unbiased; bootstrap SE 0.86–1.09 of the true SD before the 1.16 inflation; blend RMSE below raw on every line; v3 D&V raw fit biased −1.75 pt |
| **Forecast vintages** (5) | Forecast accuracy by horizon; persistent bias (on the generator's own curves and payment pattern: known curves) | Incurred PMPM MAPE 2.3–6.7%; no persistent bias on any line or metric (12 tests) |
| Regression | Every change attributable | v3 off = v2; v4 neutral = v3 (Medicaid at neutral contract terms) |
| Profit-test and contract identities | Accounting mechanics | DE = cash under the identity; tax cost = PV of tax; exact withhold timing identity; corridor against a hand calculation; DAC schedule weights and PV |
| Reverse stress | NPV is zero at each break-even; base position reported | Pass; D&V and Medicaid DE below break-even at base (ALERTs) |
| Production end to end (two phases) | Gate stops on an expired exception; mapping changes layout, never values | Pass |
| **Databricks job definition, executed locally** | Dependencies, clusters, files, requirements and placeholders valid; 17 tasks run in topological order in production mode against extracts and reproduce the release run | Pass |
| **Excel rebuild at the v4.2 recommended premiums** | The pricing recommendation is reproduced independently | Ties to the cent; DE NPV equals the pricing table |
| Automated tests (119) | Controls fail when they should; every mechanic neutral at neutral settings; assumption register | All pass |

**What validation here cannot show.** Whether the assumptions are right for Humana's business:
- Test A uses the model's own assumptions.
- The synthetic data has no anti-selection and no acuity shift.
- The trend study tests the estimator on the synthetic process only.
- The Databricks job ran on a local Spark session, not on a Humana workspace.

Production covers these with the indications, the bias tests, the A/E monitor, assumption review
and a dry run in a non-production schema.

## 7. Governance and controls
Carried over from v3:
- a fail-fast pipeline with a clean start;
- versioned control configuration and an exception register with expiry;
- ledger tie-out;
- assumption approval by hash and model version, with a disposition for every REVIEW item;
- a run manifest that releases results only after a passing reconciliation.

Added in v4:
- the trend credibility method replaces a judgment override;
- a persistent forecast bias is a REVIEW item;
- the vintage registry is never cleared;
- the drift alert list is recorded in the manifest;
- the non-neutral Excel tie-out runs on every synthetic release run.

Added in v4.2:
- the pricing policy (`config/pricing_targets.json`, risk tolerance and constraints) is a governed
  config inside the approval hash;
- every recommended price is tied out in Excel on each synthetic release run.

Added in v4.1:
- every NPV assumption has a register entry with status, source or range, enforced by a test; block
  entries store their values too, and values linked to the synthetic book are flagged and
  recomputed by a test;
- the approval hash covers the assumption set **and** the governed configs (claim-cost curves,
  trend benchmarks, forecast settings);
- in production the engine also requires this run's governance step to have approved the same
  hash;
- the manifest releases results only if the set is approved **and** reconciled;
- an exception renewal keeps the prior expiry and renewal date in the register;
- the Databricks job definition is validated and executed on every synthetic release run;
- a line below DE break-even is an ALERT.

## 8. Scope, reliance and simplifications

**Closed in v4.1.** DAC tax; non-attained-age rating bases; D&V waiting-period deferral; IBNR on
the large-claim excess; credibility on the pooling charge; book persistency and trend-based new
sales in the forecast; the D&V filing-response lag; bootstrap SE inflation; non-neutral Medicaid
contract terms in the approved set and in Test A; renewal of exception DQ-0142; local execution of
the Databricks job; governed configs in the approval hash; break-even position and shortfall
alert. The CHANGELOG gives the effect of each.

**Scope and reliance.** These need Humana data, a Humana workspace or another Humana function.
They are boundaries of a model built outside Humana, not defects, and each has a mitigation here.

| Item | Direction / materiality | Mitigation in the kit | Production remedy |
|---|---|---|---|
| No Humana experience; public benchmarks are national or industry-level; Medicaid capitation and withhold from one state | Level of every line; sign of Medicaid within judgment ranges | Sources documented with exact figures (ASOP 23, 41); credibility blend built and tested; ranges run | Humana experience through the same blend at the first production cycle |
| 40 judgment values | Medicaid DE changes sign within 3 swept ranges (start-up, risk-adjustment capture, equal churn by cell); D&V within one (pent-up 1.10); Med Supp positive across all | Each disclosed and run across its range | Humana studies (lapse, morbidity, expense, filing history), state data book |
| 17 values linked to the synthetic book (4 placeholders read off it, 13 equal to the generator's); trend normalization and the forecast use the generator's curves and payment pattern | Test B measures only values that differ from the generator's; forecast accuracy is on known curves | Disclosed list, recomputed by a test; the placeholders do not affect the headline results | Humana sales plan, data book, morbidity and completion studies |
| DAC tax classification (Med Supp 9.2%; D&V and Medicaid 0); §848(c)(2) cap not applied; tax year = policy year | Med Supp DE −$101,388; D&V DE −$8,894 more if guaranteed renewable (swept) | Statute cited; separate bridge step | Corporate Tax confirmation |
| Rating-basis comparison holds issue mix, lapse and anti-selection fixed | Actuarial equivalence before selection effects; community rating is optimistic | Stated with the results | Market and lapse response by basis from Med Supp pricing |
| Medicaid admin is a flat PMPM across rate cells | Drives the sign under MACPAC churn by cell | Swept and disclosed | Per-cell admin from the state data |
| No active-life reserve; tax reserves = statutory; tax losses used immediately; capital as % of annualized paid claims | DE modestly overstated; issue-age DE not reported | Pricing-grade use only (§1); rating bases on cash NPV | Valuation, Corporate Tax and the capital model |
| Monte Carlo volatilities, correlations and filing-response strength are judgment | Tail probabilities move materially (Med Supp P(cash < 0) 0.56–18.6% across the response strength; P(DE < 0) 0.35–9.7% across the volatilities) | Attribution and parameter table show the range | `estimate_mc_parameters.py` on Humana history |
| Synthetic forecast vintages use book lapse estimated on the full period and the generator's curves | Hindsight and known-curve advantage | Stated with the accuracy results | Real vintages from the registry |
| Model points are cohorts by band, not seriatim; lapse and anti-selection don't vary by age or plan | Mix within a band averaged | Base point asserted equal to the headline cohort | Policy-level points in Databricks; lapse by age and plan |
| Deterministic base uses the filing plan; the filing response is in the Monte Carlo only | Deterministic NPV ignores management response | Response modelled and attributed | Filing-cycle plan from Med Supp pricing |
| Pricing: lapse does not respond to price level; the risk tolerance is policy; Medicaid levers are solved one at a time; market references are single public sources | A recommended increase could lose more business than modelled (D&V +9.4% instead of +8.0% if lapse rises 10%) | Robustness row; REVIEW flags against public rates | Price-elasticity study, Humana pricing policy, competitor rate surveys |
| Databricks runtime, Unity Catalog permissions and cluster policies | Operational | Job definition validated and executed locally | Dry run in a non-production schema (RUNBOOK §2, item 9) |
| SAS retired | — | Python engine plus the independent Excel rebuild | Port and tie out to the cent, if SAS is required |

## 9. Reliance and responsibility

Results depend on:
- the source data (reliance on the data owners named in `config/data_contract.json`);
- the external inputs listed in DATA_REQUEST.md §B (status X in `config/forecast_config.json`);
- the judgment values (status J in §5 and the assumption register).

On Humana data, the responsible actuary reviews the indications, approves the assumption set, and
signs the cycle's results with the run manifest attached.

| Role | Name | Date |
|---|---|---|
| Model owner | | |
| Responsible actuary (assumptions) | | |
| Independent reviewer (model risk) | | |

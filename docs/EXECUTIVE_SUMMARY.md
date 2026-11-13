# NPV Forecasting Model v4.4 — Executive summary

**What it is.** A working forecasting and valuation model for three Humana lines: Medicare
Supplement, individual Dental & Vision, and Medicaid managed care. It does the two jobs an
Analytics/Forecasting actuary does:
- **Value and price new business** on an after-tax, cost-of-capital basis: Monte Carlo, reverse
  stress, sensitivity, and a risk-based pricing solve within regulatory constraints.
- **Forecast the existing book every month,** explain actual against expected, and track forecast
  accuracy by vintage.

**What it is not.** It uses no Humana data and no Humana assumptions. The data is a synthetic
practice book with planted data problems. Every NPV assumption comes from public sources, statute or
documented judgment, chosen under the ASOPs (25, 8, 49, 23, 56, 41, 58). No pricing assumption
takes credibility from the synthetic experience. The 17 values that are linked to it (sales-mix placeholders, and
values equal to the generator's own that a public figure or judgment range supports) are listed
openly. It is built so that on
day one at Humana the same pipeline runs on Humana extracts and Humana experience takes
credibility against these public complements.

## Results and recommended pricing

The model values each product **as priced today**, then **prices it** so that the chance of
missing the 8.5% hurdle after tax and capital (P(DE < 0)) is at most 10%. The 10% is Humana's
pricing policy to set. Trend stays at its best estimate, and the regulatory limits are never
relaxed.

| Line | As priced today: DE NPV, P(DE < 0) | For Humana | Recommendation | DE NPV at recommendation, P(DE < 0) |
|---|---|---|---|---|
| Med Supp, Plan G at 65 (per 1,000) | $411,854, 3.6% | Good | **Keep the price.** The cell meets the target with 1.4% headroom, provided rate filings keep responding to emerging claims (24.7% with no response) | $411,854, 3.0% |
| Med Supp portfolio (age × plan mix) | $119,341, 28.4% | Good, but fails the risk target as priced | **Reprice Plan N +2% to +8% and older-age Plan G +1% to +7%.** High-Deductible G can't reach the base cell's margin inside the 65% minimum loss ratio | $382,991, 3.1% |
| Dental & Vision (per 1,000) | −$33,612, 98.6% | Bad as priced | **Raise the premium 7.1%**, $39.00 → $41.78 a month (existing members: over 2 filings, the 10% cap) | $22,332, 9.9% |
| Medicaid new-state contract (10,000 members) | +$2,711,443, 47.2% | Thin: positive, but close to a coin flip | **Do not bid at these terms.** Capitation is state-set, and the cost ratio or admin needed to meet the target lies outside every public range. The positive DE rests on MACPAC churn by rate cell; at equal churn it is −$1,089,199, so the state data book comes first | — |

At the recommended premiums, the independent Excel model reproduces the Python NPVs to the cent.
Two prices are flagged for market review: D&V is 7.1% above its one public reference, and the
High-Deductible G relativity is above the one public carrier. On public assumptions these
recommendations demonstrate the method; on Humana's approved assumptions and pricing policy, the
same solve is the pricing recommendation.

Three findings a pricing or forecasting lead would act on:
1. **Trend is the dominant risk**, and how it is estimated matters as much as its level. A ±1 pt
   trend shock swings Med Supp DE by $845,542. On 30 generated books, an unnormalized D&V fit was
   biased by −1.75 pt, and residual-based standard errors understated uncertainty by 25–40%. The
   kit normalizes the experience, uses a member-level bootstrap and blends with a public
   benchmark by credibility.
2. **Cash NPV is not the decision measure.** After claim lag, investment income, capital, tax
   and the IRC §848 DAC tax, Med Supp DE is 40% below cash and Medicaid DE is 52% below cash.
   Medicaid's MLR floor takes the Monte Carlo mean DE down to $0.12M.
3. **Management action belongs in the risk measure.** With no filing response, Med Supp
   P(cash NPV < 0) is 18.6%; with filings that close half the visible gap each year (each at most
   10%) it is 1.2%. The response strength is judgment, so the kit shows the range (0.56% to 18.6%).

## Why the numbers can be trusted

- **Two independent implementations agree to the cent.** A 17-sheet Excel model with 66,860 live
  formulas ties to the Python engine on every year, every sensitivity and the DAC tax schedule.
  It also ties at non-neutral contract terms in three variants.
- **The model is backtested against itself:** event-level simulation (Test A, no bias), the cost
  of estimation error (Test B) and process vs parameter risk (Test C).
- **Controls stop bad runs.** A data contract, a data-quality gate with expiring exceptions and a
  ledger tie-out run first. The assumption set and governed configs are approved by hash. The run
  manifest releases results only when the set is approved **and** Excel reconciles.
- **Every NPV assumption is on the record.** There are 89 register entries with source, exact
  figure, ASOP reference and range ([ASSUMPTION_BASIS.md](ASSUMPTION_BASIS.md)), including the
  values linked to the synthetic book. A test fails if any value drifts from the register or the
  synthetic-link list goes stale.
- **An independent review was run and acted on.** A separate reviewer checked every quoted
  figure against the outputs, and checked the mechanics. Its findings were all fixed: the
  synthetic-link disclosure, judgment sweeps for every judgment value, the pooling-charge
  credibility standard and a drift-alert gap.
- **Production mode is proven.** It runs end to end on source extracts in a separate layout and
  reproduces the synthetic results exactly. The Databricks job definition (17 tasks) is
  validated and executed locally in dependency order. 119 automated tests pass.

## What the forecasting layer does each month

- **In-force forecast** by month and segment: Bornhuetter-Ferguson completion, credibility to
  the line, book persistency, and IBNR on the large-claim excess.
- **A/E waterfall:** volume, mix, base re-estimate, trend and morbidity, large claims, prior
  period. Each step is tested independently.
- **Vintage registry:** accuracy by horizon (incurred PMPM MAPE 2.3–6.7%) and a persistent-bias
  test (none found).
- **Drift monitor:** credibility-scaled A/E bands with persistence, plus break-even alerts.
- **Data issues get traced.** One Kentucky eligibility feed drop is traced through the DQ gate
  (336 of 376 orphan claims) to the A/E waterfall, where it shows as volume, not morbidity.

## What only Humana can close

These boundaries are stated, not hidden. Each has a mitigation and a named owner in the go-live
checklist (RUNBOOK §2):
- Humana experience and pricing trends to replace the public complements, through the same
  credibility blend.
- 40 judgment values (lapse shock, select factors, anti-selection, expenses, Medicaid start-up,
  renewal, Monte Carlo calibration). Each is run across its range. Medicaid DE changes sign
  within three swept ranges (start-up, risk-adjustment capture, equal churn by cell), D&V within
  one (pent-up 1.10); Med Supp stays positive across all of them.
- Corporate Tax confirmation of the §848 classification. Valuation and capital inputs for a
  statutory-grade profit test.
- A dry run of the Databricks job in a Humana non-production workspace.

**Where to look next:** [DETAILED_RESULTS](DETAILED_RESULTS.md) for the full results, the recommended pricing and the list of what
v4.1 closed; [MODEL_DOCUMENTATION](MODEL_DOCUMENTATION.md) for methods;
[MODEL_VALIDATION_PACK](MODEL_VALIDATION_PACK.md) for the evidence; the workbook's Dashboard
sheet for the one-page view.

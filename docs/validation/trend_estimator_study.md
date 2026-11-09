# Trend estimator study (v4 validation)

**Question.** How accurate is the trend the experience study estimates from four years of data,
how well does the kit know its own uncertainty, and does the v4 credibility blend improve on the
raw fit?

**Method.** `tests/validation/trend_estimator_study.py` regenerated the synthetic book under 30
random seeds (201–230). It reran the experience pipeline on each (generator → ingest → SQL kit →
trend study) from a frozen snapshot of the v4 code and configuration. Each fit was then compared
with the generator's true pure cost trend. Raw results are in `trend_estimator_study.csv` and
`trend_estimator_study_by_seed.csv`. All 30 seeds ran without failure.

Note on an earlier attempt: a first 24-seed run copied the code for each seed while the trend
script was being edited, so later seeds mixed two versions and one failed. Its point estimates
are consistent with the results below, but it is not used. The study script now always runs from a
frozen snapshot.

## Results (30 seeds)

| Series | True | Mean fit | Bias (SE) | True SD of fit | Bootstrap SE ÷ true SD | HAC SE ÷ true SD | 95% interval coverage | RMSE raw fit | RMSE v4 blend |
|---|---|---|---|---|---|---|---|---|---|
| Med Supp, raw block | 6.00% | 8.07% | +2.07 pt (0.09) | 0.52 pt | 0.92 | 0.66 | 3% | 2.13 pt | — |
| **Med Supp, normalized** | 6.00% | 5.98% | −0.02 pt (0.09) | 0.50 pt | 0.86 | 0.66 | 90% | 0.49 pt | **0.42 pt** |
| D&V, raw (v3 basis) | 3.50% | 1.75% | **−1.75 pt (0.19)** | 1.06 pt | 0.95 | 0.75 | **50%** | 2.03 pt | — |
| **D&V, normalized (v4)** | 3.50% | 3.41% | −0.09 pt (0.19) | 1.05 pt | 0.94 | 0.75 | 97% | 1.04 pt | **0.59 pt** |
| **Medicaid** | 5.50% | 5.33% | −0.17 pt (0.14) | 0.77 pt | 1.09 | 0.60 | 97% | 0.77 pt | **0.53 pt** |

The blend in this study used the v4.0 placeholder benchmarks (Med Supp 6.5%, D&V 3.0%, Medicaid
5.0%, SD 1.0 pt), each 0.5 pt off the truth. v4.1 replaces them with public sources
(`config/trend_benchmarks.json`), and the approved trends now equal those benchmarks; see
[../ASSUMPTION_BASIS.md](../ASSUMPTION_BASIS.md).

## Findings and the v4 decisions they support

1. **The v3 D&V trend fit was biased by −1.75 points.** That is nine standard errors, and its
   95% interval covered the truth half the time. The raw D&V PMPM mixes cost trend with the
   falling share of year-1 (pent-up) exposure as a block with flat new sales matures.
   **v4 normalizes D&V for duration and product**, as v3 already did for Med Supp. The bias
   disappears (−0.09 ± 0.19).
2. **v3 overstated trend uncertainty.** It kept the 6.0% / 3.5% judgment trends because
   "four years pin trend only to about ±2 points". The measured sampling SD of the normalized fit
   is 0.50 pt for Med Supp and 1.05 pt for D&V. That is about 4× and 2× tighter than the stated
   range (which reads as a ± range, not an SD). That argument for ignoring the data no longer holds.
3. **Residual-based (HAC) standard errors understate the true SD by 25–40%.** The member-level
   bootstrap is close: 0.86–1.09 of the true SD, with 90–97% coverage for a nominal 95%. The
   understatement comes from member-level persistence: the same members appear in every month,
   and a member with persistently high claims moves the whole series. **v4 weights the blend by
   the bootstrap SE.** It may still understate Med Supp uncertainty by about 15%, so **v4.1 multiplies
   it by 1/0.86 = 1.16 in 03b** (`config/trend_benchmarks.json`, `bootstrap_se_inflation`).
   - Inflating the SE by 1/0.86 (16%, the measured shortfall) leaves Med Supp at 6.75% and moves
     D&V to 3.55%.
   - Inflating it by a third leaves Med Supp at 6.75% and moves D&V to 3.45%.
4. **The credibility blend beats the raw fit on every line**, by 15% (Med Supp) to 43% (D&V) in
   RMSE, even with benchmarks 0.5 pt off the truth. The gain is largest where the fit is noisiest
   and the benchmark gets the most weight.
5. **The shipped data set is a +1.6 SD draw for Med Supp.** Its normalized fit is 6.82% against a
   true 6.0%. A correct process therefore lands above the truth here: the approved 6.75% costs
   $447,853 of Med Supp NPV against the true value (backtest Test B). That is estimation risk the
   v3 approach hid by assuming the answer. It is why trend has the largest tornado swing and the
   thinnest break-even headroom. The Med Supp cash NPV break-even trend is 7.16%, only 0.41 pt
   above the approved 6.75% (DE: 7.12%, 0.37 pt). See `output/npv/npv_breakeven.csv`.
   *These are v4.0 figures. v4.1 approves the public 6.5% (2026 Trustees), with zero credibility
   on the synthetic book; Test B prices it at −$261,650 against the generator's 6.0%, and the DE
   break-even trend is 7.44%, 0.94 pt above it.*

## What this study cannot show

It tests the estimator on the synthetic generating process. On Humana data, a misspecified
normalization curve (`config/claim_cost_curves.json`) or a shift in unmodelled mix would bias
the fit in ways this study cannot see. The production controls are the rolling-origin and vintage
bias tests (02, 11), the A/E monitor (12) and the assumption review.

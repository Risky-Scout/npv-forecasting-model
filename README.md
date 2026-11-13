# NPV and forecasting model: Medicare Supplement, Dental & Vision, Medicaid

[![release run](https://github.com/Risky-Scout/npv-forecasting-model/actions/workflows/release-run.yml/badge.svg)](https://github.com/Risky-Scout/npv-forecasting-model/actions/workflows/release-run.yml)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Risky-Scout/npv-forecasting-model)

Joey Shackelford, ASA, MAAA · version 4.4.0

This model values and prices new business, and forecasts the in-force book, for three health
lines: Medicare Supplement, individual Dental & Vision, and Medicaid managed care. I built it for
the Associate Actuary, Analytics/Forecasting role at Humana. It does the two jobs that role does:

1. **Value and price new business** on an after-tax, cost-of-capital basis, then solve for the
   price (or the bid decision) that keeps the chance of missing the hurdle rate under a set
   tolerance, without relaxing any regulatory constraint.
2. **Forecast the existing book every month,** explain actual against expected, and track how
   accurate past forecasts were.

It runs on a synthetic practice book (with planted data problems), and every assumption comes
from public sources, statute or documented judgment. No Humana data is used. The same pipeline
runs in production mode on source extracts, and the credibility blend is built so that Humana
experience takes weight against these public assumptions on the first real cycle.

---

## Running it

### In the browser (nothing to install)

1. Click **Open in GitHub Codespaces** above, then **Create codespace**. The environment (Python
   3.13, Java, LibreOffice and the pinned packages) is prebuilt, so the editor usually opens in a
   minute or two; without the prebuilt image it takes about five. It opens only once everything
   is installed.
2. In the terminal at the bottom of the editor (if none is open: ☰ menu → Terminal → New Terminal):
   ```bash
   make quick    # the model, about 4 minutes
   make run      # full release validation, about 15 minutes
   ```
   `make quick` runs every pipeline step (synthetic book, controls, studies, NPV engine and
   pricing solve, forecast, Excel rebuild) and reconciles Excel to Python to the cent. `make run`
   adds the extended validation: the Excel tie-outs at non-neutral contract terms and at the
   recommended prices, the 119 tests and the local Databricks job. Both check the environment
   first and say what's missing if anything is.
3. Each ends by printing the headline results, the pricing recommendations and the checks.
   `make results` prints them again at any time. For the workbook, right-click
   `output/Humana_NPV_Forecasting_Model.xlsx` in the file explorer and choose **Download**.

### On your own machine

Needs Python 3.12 or newer (3.13 recommended), Java 17 or newer (for the local Spark run of the
Databricks notebook) and LibreOffice (headless, to recalculate the workbook's formulas). These
commands are tested on Linux, in the Codespace and on GitHub's runners. macOS should work the same
way with Homebrew's `python@3.13`, `openjdk` and the LibreOffice app, but it isn't part of the
automated checks. On Windows, use the Codespace.

```bash
git clone https://github.com/Risky-Scout/npv-forecasting-model.git
cd npv-forecasting-model
python3.13 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run_all.py                    # full release run, about 15 minutes
python tests/e2e_production_run.py   # production mode end to end, after a release run (about 3 minutes)
```

| Command | What it does |
|---|---|
| `make run` / `python run_all.py` | Full release run with the extended validation, about 15 minutes. Stops at the first failing step |
| `make quick` / `python run_all.py --core` | The model and the Excel = Python reconciliation, about 4 minutes |
| `make results` / `python show_results.py` | Headline results, pricing recommendations and checks of the last run |
| `make test` / `python tests/run_tests.py` | Tests only (after a run) |
| `make production` / `python tests/e2e_production_run.py` | Production mode on production-layout extracts; must reproduce the release run exactly |

**What you'll see in the log.** Some FAIL, REVIEW, BIAS FLAG and ALERT lines are intended, and
the run still ends with `All steps completed.`:

- The raw data-quality report (SQL 01) shows FAIL and REVIEW because the data problems are
  planted; the controls gate cleans them or covers them by exception.
- Two trend and three D&V lapse REVIEW items: the synthetic book's indications against the public
  basis. The approval dispositions them, because synthetic data isn't relevant experience.
- A Medicaid holdout BIAS FLAG (−6.9%) is one noisy year; across rolling origins it averages
  −2.4%, and only persistent bias becomes a REVIEW item.
- Four drift ALERTs: three Medicaid terminations (the redetermination wave) and D&V, whose base
  DE NPV is below break-even.
- Med Supp High-Deductible G cells flagged `outside the single public reference 0.18`: the only
  public HDG relativity is one carrier's, so any repriced HDG cell is flagged for a market check.
- Spark prints WARN lines and a `FutureWarning` that PySpark doesn't yet fully support pandas 3.
  These are startup messages; the Spark run still ties to the engine to the cent.
- After `make quick`, the summary lists the Excel tie-outs at the contract variants and the
  recommended prices, the test suite and the local Databricks job as `not part of make quick`.
  `make run` runs all of them (about 15 minutes).

The data generator uses a fixed seed, so every run reproduces the figures below to the dollar.

### Changing an assumption

Edit a value in `code/python/npv_assumptions.json`, then run `make quick`. For example, set
`DENTAL_VISION` → `annual_premium_year1` from `468.0` to `501.36` (the model's recommended price):
D&V DE NPV moves from −$33,612 to +$22,332 with P(DE < 0) of 9.9%, and Excel still ties to
Python to the cent on the edited set.

The governance controls treat an edited set the way they would in production review:

- The assumption gate prints `NOT APPROVED` and, in synthetic mode, lets the run continue.
- The run manifest marks the results `WITHHELD` until the set is approved. `make quick` and
  `make results` still show every figure the edited set produces, labelled as not approved.
- `make run` is the release validation of the approved set. Its tests check every value against
  the assumption register, so it fails on an edited set, by design.
- In production mode an unapproved set stops the run at the gate. Approving a set means recording
  its hash in `config/assumption_approvals.json` (`docs/RUNBOOK.md` §2, item 6).

To return to the approved assumptions: `git checkout code/python/npv_assumptions.json`.

---

## Results

Each product is valued as priced today, then priced so that P(DE NPV < 0) is at most 10%. DE NPV
is after-tax distributable earnings discounted at an 8.5% hurdle rate on required capital. Trend
stays at its best estimate, and regulatory limits are never relaxed.

| Line | As priced today: DE NPV, P(DE < 0) | Recommendation | At the recommendation |
|---|---|---|---|
| Med Supp, Plan G at 65 (per 1,000) | $411,854, 3.6% | Keep the price (1.4% headroom). This depends on rate filings continuing to respond to emerging claims, within the 10% member cap; with no response P(DE < 0) is 24.7% | $411,854, 3.6% |
| Med Supp portfolio, issue age × plan (per 1,000) | $119,341, 28.4% | Plan N +2.0% to +8.1%, older-age Plan G +1.3% to +6.6%. High-Deductible G is capped by the 65% minimum loss ratio | $382,991, 3.8% |
| Dental & Vision (per 1,000) | −$33,612, 98.6% | Raise the premium 7.1%, from $39.00 to $41.78 a month; for existing members phase it over 2 filings (the 10% cap). Flagged for market review: 7.1% above the one public reference, which is too weak to enforce the 5% limit | $22,332, 9.9% |
| Medicaid, new-state contract (10,000 members) | +$2,711,443, 47.2% | Do not bid at these terms. DE NPV is positive, but the chance of a loss is 47.2%: meeting the target would need a cost ratio of 83.4% (public MCO MLRs run 87.1–90.6%) or admin of $10.99 PMPM (public $39–$46), and capitation is set by the state. The result rests on MACPAC churn by rate cell; at equal churn DE NPV is −$1,089,199, so the state data book decides | — |

What I'd take from these:

- **Trend is the dominant risk, and how you estimate it matters as much as its level.** A
  1-point trend shock moves Med Supp DE by $845,542. Re-running the estimator on 30 generated
  books showed that an unnormalized D&V fit was biased by −1.75 points and residual-based standard
  errors understated uncertainty by 25–40%. The model normalizes experience, uses a member-level
  bootstrap and credibility-blends with a public benchmark.
- **Cash NPV is the wrong decision measure.** After claim lag, investment income, capital, tax and
  the IRC §848 DAC tax, Med Supp DE is 40% below cash, and Medicaid DE ($2.71M) is 52% below cash ($5.62M).
- **Management action belongs in the risk measure.** With no rate-filing response, Med Supp
  P(cash NPV < 0) is 18.6%; with filings that close half the visible gap each year, capped at 10% a
  year, it is 1.2%.
  The response strength is judgment, so the attribution shows the whole range.
- **A headline cell isn't a portfolio.** Plan G at 65 is the best cell in the book. At realistic
  issue ages and plan mix the same product is worth $119,341, because filed age steps (3.0%)
  are flatter than the claim cost slope (3.8%) and per-policy costs don't scale down with
  small premiums.

The full results, Monte Carlo attribution and break-evens are in
[docs/DETAILED_RESULTS.md](docs/DETAILED_RESULTS.md); the two-page version is
[docs/EXECUTIVE_SUMMARY.md](docs/EXECUTIVE_SUMMARY.md).

---

## How the model works

```
source extracts ─► ingest ─► SQL kit (DQ checks, PMPM experience, completion, lapse, A/E)
                ─► controls gate ......................................... stops the run on failure
                ─► trend study, lapse GLM
                ─► assumption governance (credibility, approval by hash) .. stops if unapproved
                ─► NPV engine: monthly cash flows ─► profit test ─► Monte Carlo ─► pricing solve
                ─► in-force forecast ─► A/E waterfall ─► forecast vintages ─► drift monitoring
                ─► Excel rebuild ─► Excel = Python to the cent .............. stops on any difference
                ─► run manifest (releases results only if approved and reconciled)
```

**New-business NPV.** Monthly cash flows per line, with claim lag, claim reserves, required
capital, investment income and 21% tax layered on to get distributable earnings. Line by line:

- *Medicare Supplement:* selection wear-off, attained-age premiums with a filed rate increase
  derived from trend and aging, anti-selective shock lapse at each rate increase (healthier
  policyholders shop and leave), the 200% commission cap, the 65% minimum loss ratio, the DAC
  tax, and model points by issue-age band and plan (Plan F excluded under MACRA). Issue-age and
  community rating bases for the four states that prohibit attained-age rating.
- *Dental & Vision:* first-year pent-up demand, benefit categories with their own shares and
  trend, waiting periods that defer pent-up demand rather than remove it, and a 30% flat lapse
  from filed memoranda.
- *Medicaid:* Child / Adult / ABD rate cells, risk adjustment with a lag, quality withhold with
  expected earn-back, directed payments, the 85% MLR floor with remittance, risk corridors, an
  acuity path, start-up cost, and contract renewal probability.

**Monte Carlo.** 10,000 scenarios with correlated parameter shocks, year-to-year process risk and
a rate-filing response with a two-year lag. Results are attributed layer by layer, so you can see
how much of the tail is parameter risk, process risk and management action.

**Pricing solve.** For each line the premium is solved (Brent's method) so the 10th percentile
of DE NPV is zero, then checked against the regulatory constraints and against public filed
rates. Where capitation is state-set, the solve reports what cost ratio or admin level would be
needed and whether that is inside public ranges.

**Monthly forecasting.** In-force forecast by month and segment with Bornhuetter-Ferguson
completion, IBNR on the large-claim excess and credibility-weighted pooling. Then an A/E
waterfall against the prior forecast (volume, mix, base, trend and morbidity, large claims, prior
period), a vintage registry that tracks accuracy by horizon, and a drift monitor with
credibility-scaled bands. The synthetic book has a Kentucky eligibility feed drop planted in it.
The data gate catches it (336 of 376 orphan claims trace to it) and the waterfall shows it as
volume, not morbidity.

**Assumptions.** All 89 are in a register ([docs/ASSUMPTION_BASIS.md](docs/ASSUMPTION_BASIS.md))
with source, figure, ASOP reference and range: 30 public sources (Medicare Trustees, CMS, MACPAC,
SSA, NAIC, Milliman, Gen Re and others), 4 statutory, 5 derived, 10 model settings and 40
judgment values. Every judgment value is run across its range. The synthetic book gets no
credibility in pricing (ASOP 25 §3.3), and the 17 values that happen to equal or come from the
synthetic generator are listed. A test fails if any value drifts from the register.

---

## How it's checked

- **Excel rebuilds the NPV independently and ties to Python to the cent:** 17 sheets, 66,860 live
  formulas, every year and sensitivity, at the approved assumptions, at three non-neutral
  Medicaid contract variants and at the recommended prices.
- **Backtests against the generator:** Test A (event-level simulation, no bias on any line),
  Test B (the cost of estimation error) and Test C (process vs parameter risk).
- **Forecast accuracy:** incurred PMPM MAPE 2.3–6.7% by horizon, and no persistent bias on any
  line across five vintages. These use the generator's own claim-cost curves and payment
  pattern; on real data the curves are estimated, so expect wider errors.
- **Controls:** data contract, data-quality gate with expiring exceptions, ledger tie-out,
  assumption approval by hash, and a manifest that refuses to release unreconciled results.
- **Production mode** runs end to end on extracts in a separate layout and reproduces the
  synthetic results exactly. The 17-task Databricks job definition is run locally in dependency
  order.
- **119 automated tests.** The full run is repeated on every push (badge at the top).

---

## What it can't tell you without Humana data

- Humana experience, pricing trends and filed rate relativities, which replace the public values
  through the same credibility blend.
- The 40 judgment values, including the Monte Carlo calibration (`estimate_mc_parameters.py`
  proposes values from rate-filing and trend history). Across the swept ranges, Medicaid DE
  changes sign within three (start-up cost, risk-adjustment capture, equal churn by cell), D&V
  within one (a pent-up factor of 1.10), and Med Supp stays positive across all of them.
- Corporate Tax confirmation of the §848 classification by line, and valuation and capital inputs
  for a statutory-grade profit test.
- A dry run of the Databricks job in a non-production workspace.

The go-live checklist with owners is in [docs/RUNBOOK.md](docs/RUNBOOK.md) §2.

**Known limitations of this build**, stated here rather than left to be found:

- **Four years of one block can't pin trend precisely.** Because the data is synthetic, the
  estimators can be graded against the true trend: normalized Med Supp trend fits 6.82% against
  a true 6.00% (about 2.3 standard errors), D&V 4.35% against 3.50%, Medicaid 4.74% against
  5.50%. Backtest Test B prices it: pricing Med Supp on the fitted trend would understate NPV by
  $434,213. The approved public trend (6.5%) is closer but still conservative, understating NPV
  by $261,650 against the truth. This is why pricing trend comes from public benchmarks with
  credibility, and the estimators are not tuned toward the known answer.
- **One rule, applied consistently: synthetic experience gets no credibility.** So the approved
  set keeps the public flat 30% D&V lapse even though the practice book shows 36% / 22% / 17% by
  duration (flagged REVIEW and dispositioned). On Humana data the duration curve would replace
  it through the credibility blend; it is the first assumption study to run.
- **The 2025 Medicaid actual-to-expected miss is not fully explained.** Claims came in 5.1% over
  the forecast (margin variance −$841,519). The waterfall isolates the Kentucky feed drop as
  volume and large claims as $177,560 below the pooling charge; the remaining $1,059,633 of
  "trend and morbidity" is the residual by construction. Explaining it needs claims detail by
  service category that the practice book doesn't carry.
- **The company goals are policy stand-ins.** The 10% risk tolerance, the 10% member increase
  cap and the 5% market limit are judgment defaults in `config/pricing_targets.json`; Humana's
  pricing policy and risk appetite statement replace them. The D&V market reference is a single
  filing, so its 5% limit is only a flag until competitor rate surveys replace it.
- **Medicaid's sign rests on churn by rate cell.** At MACPAC's churn by cell DE NPV is
  +$2,711,443; at equal churn it is −$1,089,199. MACPAC is national 2018 data, and the flat admin
  PMPM across cells adds to the gain. The state data book replaces both before any bid.

**The in-force forecast and the pricing basis disagree on purpose.** The forecast projects the
existing (synthetic) book on that book's own experience; new-business pricing gives that
experience no credibility and uses the public basis. So:

- The Medicaid book forecasts at 98–101% loss ratio, because its own trend (4.9%) runs ahead of
  the rate action in its capitation history (3.5%). The new-contract NPV sets the rate update
  equal to trend (ASOP 49: rates are certified to cover attainable cost). A book whose rates lag
  trend is what the drift monitor escalates; it isn't a pricing assumption.
- The D&V book runs at about 84%, against a priced ultimate of 70.5% from public individual
  dental results. D&V is unprofitable even on the public figure. The design the market actually
  sells (6-month wait on basic, 12-month on major) turns DE positive, +$57,220 per 1,000
  (`output/npv/dv_product_options.csv`); the 7.1% reprice is the answer if the product keeps no
  waiting periods.
- High-Deductible G rests on one public carrier's relativity (0.18, flagged low confidence) and
  is the first input to replace with Humana's filed relativities.

---

## Repository map

| Path | Contents |
|---|---|
| `run_all.py` | Runs every step in dependency order; fail-fast |
| `show_results.py` | Prints the headline results and checks of the last run |
| `code/python/` | Pipeline steps, numbered in run order (00 generator and ingest through 12 drift monitoring), the NPV engine (`04_npv_engine.py`) and `npv_assumptions.json` |
| `code/sql/` | DuckDB SQL kit: DQ checks, silver/gold layers, completion factors, lapse study, A/E |
| `code/databricks/` | The same pipeline as a Databricks notebook (Bronze/Silver/Gold, Delta) |
| `code/sas/` | Retired version 2 mirror, kept for reference; Python is the production engine |
| `config/` | Run settings, data contract, DQ policy and exceptions, source mapping, trend benchmarks, pricing targets, assumption register and approvals |
| `build_workbook.py`, `recalc_workbook.py` | The 17-sheet Excel model and its LibreOffice recalculation |
| `tests/` | Unit and end-to-end tests, including the production run and the local Databricks job |
| `deploy/` | Databricks Workflow definition (17 tasks) and cluster setup |
| `docs/` | Executive summary, model documentation, validation pack, assumption basis, data request, runbook |
| `data/`, `output/` | Generated by the run; not committed |

**Documentation:** [Executive summary](docs/EXECUTIVE_SUMMARY.md) ·
[Model documentation](docs/MODEL_DOCUMENTATION.md) ·
[Validation pack](docs/MODEL_VALIDATION_PACK.md) ·
[Assumption basis](docs/ASSUMPTION_BASIS.md) ·
[Detailed results](docs/DETAILED_RESULTS.md) ·
[Runbook](docs/RUNBOOK.md) · [Data request](docs/DATA_REQUEST.md) ·
[Changelog](CHANGELOG.md)

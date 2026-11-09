# NPV and forecasting model: Medicare Supplement, Dental & Vision, Medicaid

[![release run](https://github.com/Risky-Scout/npv-forecasting-model/actions/workflows/release-run.yml/badge.svg)](https://github.com/Risky-Scout/npv-forecasting-model/actions/workflows/release-run.yml)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/Risky-Scout/npv-forecasting-model)

Joey Shackelford, ASA, MAAA · version 4.2.0

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

1. Click **Open in GitHub Codespaces** above, then **Create codespace**. The first build takes a
   few minutes; it installs Python 3.13, Java and LibreOffice.
2. In the terminal that opens:
   ```bash
   make run
   ```
3. Results land in `output/`. Open `output/Humana_NPV_Forecasting_Model.xlsx` (download it from
   the file explorer) or read `output/results.json`.

### On your own machine

Needs Python 3.12 or newer (3.13 recommended), Java 17 or newer (for the local Spark run of the
Databricks notebook) and LibreOffice (headless, to recalculate the workbook's formulas).

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
| `make run` / `python run_all.py` | Full release run. Stops at the first failing step |
| `make quick` / `python run_all.py --skip-tests` | Same, without the 113-test suite |
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
- Five drift ALERTs: three Medicaid terminations (the redetermination wave) and two lines whose
  base DE NPV is below break-even (D&V and Medicaid).

The data generator uses a fixed seed, so every run reproduces the figures below to the dollar.
To change an assumption, edit `code/python/npv_assumptions.json` and rerun. The assumption gate
will stop the run until the new set is approved (`docs/RUNBOOK.md` §3 shows how), which is the
point of the gate.

---

## Results

Each product is valued as priced today, then priced so that P(DE NPV < 0) is at most 10%. DE NPV
is after-tax distributable earnings discounted at an 8.5% hurdle rate on required capital. Trend
stays at its best estimate, and regulatory limits are never relaxed.

| Line | As priced today: DE NPV, P(DE < 0) | Recommendation | At the recommendation |
|---|---|---|---|
| Med Supp, Plan G at 65 (per 1,000) | $411,854, 3.0% | Keep the price (1.5% headroom). This depends on rate filings continuing to respond to emerging claims; with no response P(DE < 0) is 24.7% | $411,854, 3.0% |
| Med Supp portfolio, issue age × plan (per 1,000) | $119,341, 28.0% | Plan N +2.0% to +8.1%, older-age Plan G +1.3% to +6.6%. High-Deductible G is capped by the 65% minimum loss ratio | $382,991, 3.1% |
| Dental & Vision (per 1,000) | −$40,686, 99.8% | Raise the premium 8.0%, from $39.00 to $42.11 a month. Flagged for market review: 8% above the public reference | $21,335, 9.8% |
| Medicaid, new-state contract (10,000 members) | −$1,089,199, 57.0% | Do not bid at these terms. It would need a cost ratio of 82.6% (public MCO MLRs run 87.1–90.6%) or admin of $10.07 PMPM (public $39–$46), and capitation is set by the state | — |

What I'd take from these:

- **Trend is the dominant risk, and how you estimate it matters as much as its level.** A
  1-point trend shock moves Med Supp DE by $845,542. Re-running the estimator on 30 generated
  books showed that an unnormalized D&V fit was biased by −1.75 points and residual-based standard
  errors understated uncertainty by 25–40%. The model normalizes experience, uses a member-level
  bootstrap and credibility-blends with a public benchmark.
- **Cash NPV is the wrong decision measure.** After claim lag, investment income, capital, tax and
  the IRC §848 DAC tax, Med Supp DE is 40% below cash, and Medicaid goes from +$0.52M to −$1.09M.
- **Management action belongs in the risk measure.** With no rate-filing response, Med Supp
  P(cash NPV < 0) is 18.6%; with filings that close half the visible gap each year it is 0.6%.
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
SSA, NAIC, Milliman, Gen Re and others), 4 statutory, 4 derived, 10 model settings and 41
judgment values. Every judgment value is run across its range. The synthetic book gets no
credibility in pricing (ASOP 25 §3.3), and the 18 values that happen to equal or come from the
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
- **113 automated tests.** The full run is repeated on every push (badge at the top).

---

## What it can't tell you without Humana data

- Humana experience, pricing trends and filed rate relativities, which replace the public values
  through the same credibility blend.
- The 41 judgment values, including the Monte Carlo calibration (`estimate_mc_parameters.py`
  proposes values from rate-filing and trend history). Medicaid DE changes sign within four of
  their ranges; D&V stays negative and Med Supp positive across all of them.
- Corporate Tax confirmation of the §848 classification by line, and valuation and capital inputs
  for a statutory-grade profit test.
- A dry run of the Databricks job in a non-production workspace.

The go-live checklist with owners is in [docs/RUNBOOK.md](docs/RUNBOOK.md) §2.

---

## Repository map

| Path | Contents |
|---|---|
| `run_all.py` | Runs every step in dependency order; fail-fast |
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

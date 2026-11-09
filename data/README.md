# data/

Empty in the repository on purpose. `python run_all.py` (or `make run`) writes the synthetic
practice book here:

| File | Written by | Contents |
|---|---|---|
| `members.csv`, `member_months.csv`, `claims.csv` | `code/python/00_generate_synthetic_data.py` | Synthetic eligibility and claims for Med Supp, D&V and Medicaid, 2022–2026, with planted data problems |
| `true_assumptions.json` | same | The generator's own parameters. Used only by the backtest (Test B) and the synthetic-link test, never by pricing |
| `finance_controls.csv` | `code/python/00b_generate_finance_controls.py` | Synthetic ledger totals for the reconciliation |

The generator uses a fixed seed, so every run rebuilds the same book and the same results.

In production mode nothing is generated. Source extracts are read through
`config/source_mapping.json`, and `NPV_DATA_DIR` / `NPV_OUTPUT_DIR` point at governed storage
outside this repository (see `code/python/kitpaths.py` and `docs/RUNBOOK.md`).

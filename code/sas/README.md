# SAS programs — retired in v4 (reference only)

**Status: not maintained, never executed.** These programs mirror the *version 2* model. They
were never run (no SAS licence was available to the kit's author), and v3 and v4 mechanics
(profit test, anti-selection, rate cells, contract terms, model points, process-risk Monte
Carlo, forecasting modules) were never ported.

Decision for v4: Python is the production engine. It is the implementation that the Excel
workbook ties to to the cent and that the backtest, the test suite and the production end-to-end
test validate. Keeping an unexecuted SAS copy in step would be an untested third implementation
that looks validated but isn't.

If Humana requires SAS (several Analytics/Forecasting roles list SAS or SQL), port from the v4
Python, then validate the port the same way the workbook is validated: tie every NPV, every
projection year and every sensitivity to the Python engine to the cent, on the approved set and on
the non-neutral set in `tests/e2e_excel_nonneutral.py`.

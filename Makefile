# Shortcuts. Each target is one plain command, so the same steps work without make
# (Windows: run the python lines directly).

PY ?= python

.PHONY: help check setup run quick results test production clean

help:
	@echo "make check       confirm Python packages, Java and LibreOffice are installed"
	@echo "make setup       install the pinned Python packages (Python 3.12+; 3.13 recommended)"
	@echo "make quick       the model: every pipeline step and the Excel = Python reconciliation (about 4 min)"
	@echo "make run         full release run: adds the extended Excel tie-outs, 119 tests, local Databricks job (about 15 min)"
	@echo "make results     print the headline results and checks of the last run"
	@echo "make test        test suite only (needs a completed run)"
	@echo "make production  production-mode end-to-end test, two phases (after make run)"
	@echo "make clean       delete generated data and outputs"

check:
	@$(PY) -c "import numpy, pandas, scipy, statsmodels, duckdb, matplotlib, openpyxl, pyarrow, pyspark, pytest" 2>/dev/null \
	  || { echo "Python packages are not installed. In a Codespace run: bash .devcontainer/setup.sh   Otherwise: make setup"; exit 1; }
	@command -v java >/dev/null || { echo "Java 17+ is not installed (needed for the local Spark run of the Databricks notebook)."; exit 1; }
	@{ command -v soffice || command -v libreoffice || test -x /Applications/LibreOffice.app/Contents/MacOS/soffice; } >/dev/null \
	  || { echo "LibreOffice is not installed (needed to recalculate the workbook's formulas)."; exit 1; }
	@echo "Environment OK: $$($(PY) --version), Java, LibreOffice, all Python packages."

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

run: check
	$(PY) run_all.py
	$(PY) show_results.py

quick: check
	$(PY) run_all.py --core
	$(PY) show_results.py

results:
	$(PY) show_results.py

test: check
	$(PY) tests/run_tests.py

production: check
	$(PY) tests/e2e_production_run.py

clean:
	rm -rf output
	find data -mindepth 1 ! -name README.md -delete

# Shortcuts. Each target is one plain command, so the same steps work without make
# (Windows: run the python lines directly).

PY ?= python

.PHONY: help setup run quick test production clean

help:
	@echo "make setup       install the pinned Python packages (Python 3.12+; 3.13 recommended)"
	@echo "make run         full release run: synthetic book -> NPV -> forecast -> Excel tie-out -> tests (about 15 min)"
	@echo "make quick       same run without the test suite"
	@echo "make test        test suite only (needs a completed run)"
	@echo "make production  production-mode end-to-end test, two phases (after make run)"
	@echo "make clean       delete generated data and outputs"

setup:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

run:
	$(PY) run_all.py

quick:
	$(PY) run_all.py --skip-tests

test:
	$(PY) tests/run_tests.py

production:
	$(PY) tests/e2e_production_run.py

clean:
	rm -rf output
	find data -mindepth 1 ! -name README.md -delete

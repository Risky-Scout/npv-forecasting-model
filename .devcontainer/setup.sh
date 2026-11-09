#!/usr/bin/env bash
# Runs once when the Codespace (or local dev container) is created.
# Installs headless LibreOffice Calc, which recalc_workbook.py uses to compute every workbook
# formula for the Excel-to-Python tie-out, then the pinned Python packages.
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends libreoffice-calc
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo
echo "Ready. Run:  make run    (full release run, about 15 minutes)"

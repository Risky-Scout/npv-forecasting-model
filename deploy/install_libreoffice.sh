#!/bin/bash
# Cluster init script (TEMPLATE): installs headless LibreOffice Calc so recalc_workbook.py can compute
# every workbook formula for the Excel-to-Python reconciliation. Requires the cluster policy to allow
# init scripts and outbound access to the OS package mirror; if it does not, run the three workbook
# tasks on the release-validation machine instead (docs/RUNBOOK.md).
set -euo pipefail
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends libreoffice-calc

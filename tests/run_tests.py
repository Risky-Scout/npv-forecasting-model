"""Runs the test suite (used as the last step of run_all.py). Exit code 0 only if every test passes."""
import sys
from pathlib import Path

import pytest

sys.exit(pytest.main(["-q", str(Path(__file__).resolve().parent)]))

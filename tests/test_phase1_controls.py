"""
Tests for the Phase 1 controls. Two kinds:
  * unit tests on controls.py (pure rules, no data), and
  * end-to-end tests that run 01b_controls_gate.py against deliberately broken
    copies of the configuration and confirm the pipeline STOPS (exit code 1).
A control that cannot fail is not a control, so every rule has a failing case.
Run: python -m pytest -q tests   (after run_all.py, or after steps 00, 00b and 01)
"""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code" / "python"))
import controls  # noqa: E402
from kitpaths import OUTPUT_DIR  # noqa: E402

POLICY = json.loads((ROOT / "config" / "dq_policy.json").read_text())
EXC = json.loads((ROOT / "config" / "dq_exceptions.json").read_text())["exceptions"]


def f(check, lob="MEDICAID", count=1, share=None, keys=()):
    return {"check": check, "lob": lob, "count": count, "share": share, "keys": list(keys)}


# ---------------------------------------------------------------- data gate
def test_blocker_without_rule_fails():
    r = controls.evaluate_gate([f("paid_before_incurred", count=3)], POLICY, EXC, "2026-03-05")
    assert r[0]["status"] == "FAIL"


def test_cleaned_blocker_within_tolerance_passes():
    r = controls.evaluate_gate([f("claims_without_eligibility", count=354, share=0.0046)], POLICY, EXC, "2026-03-05")
    assert r[0]["status"] == "PASS-CLEANED"


def test_cleaned_blocker_over_tolerance_fails():
    r = controls.evaluate_gate([f("claims_without_eligibility", count=9000, share=0.05)], POLICY, EXC, "2026-03-05")
    assert r[0]["status"] == "FAIL"


def test_exception_must_match_the_specific_key():
    r = controls.evaluate_gate([f("missing_eligibility_month", keys=["OH|2025-07"])], POLICY, EXC, "2026-03-05")
    assert r[0]["status"] == "FAIL"


def test_exception_covers_its_key_while_valid():
    r = controls.evaluate_gate([f("missing_eligibility_month", keys=["KY|2025-07"])], POLICY, EXC, "2026-03-05")
    assert r[0]["status"] == "PASS-EXCEPTION"


def test_expired_exception_fails():
    # DQ-0142 was renewed to 2027-06-30 (config/dq_exceptions.json): the day after it expires the
    # finding is a FAIL again, and under the prior expiry (2026-06-30) it would already have failed.
    exp = EXC[[e["id"] for e in EXC].index("DQ-0142")]["expires"]
    assert exp == "2027-06-30"
    r = controls.evaluate_gate([f("missing_eligibility_month", keys=["KY|2025-07"])], POLICY, EXC, "2027-07-01")
    assert r[0]["status"] == "FAIL"
    old = copy.deepcopy(EXC)
    for e in old:
        if e["id"] == "DQ-0142":
            e["expires"] = "2026-06-30"
    r = controls.evaluate_gate([f("missing_eligibility_month", keys=["KY|2025-07"])], POLICY, old, "2026-07-01")
    assert r[0]["status"] == "FAIL"


def test_unknown_check_fails():
    r = controls.evaluate_gate([f("brand_new_check")], POLICY, EXC, "2026-03-05")
    assert r[0]["status"] == "FAIL"


def test_warnings_never_stop_the_run():
    r = controls.evaluate_gate([f("large_claims", count=5)], POLICY, EXC, "2026-03-05")
    assert controls.overall(r)


# ---------------------------------------------------------------- ledger
ITEM = [{"id": "RI-1", "control": "paid_claims", "lob": "MEDSUPP", "month": "2025-06", "amount": 100.0}]


def test_ledger_difference_within_tolerance_passes():
    r = controls.evaluate_ledger({("paid_claims", "MEDSUPP", "2025-05"): 10.0},
                                 {("paid_claims", "MEDSUPP", "2025-05"): 10.5}, [], 1.0)
    assert r[0]["status"] == "PASS"


def test_ledger_difference_explained_passes():
    r = controls.evaluate_ledger({("paid_claims", "MEDSUPP", "2025-06"): 10.0},
                                 {("paid_claims", "MEDSUPP", "2025-06"): 110.0}, ITEM, 1.0)
    assert r[0]["status"] == "PASS-EXPLAINED"


def test_ledger_unexplained_difference_fails():
    r = controls.evaluate_ledger({("paid_claims", "MEDSUPP", "2025-06"): 10.0},
                                 {("paid_claims", "MEDSUPP", "2025-06"): 160.0}, ITEM, 1.0)
    assert r[0]["status"] == "FAIL"


def test_stale_reconciling_item_fails():
    r = controls.evaluate_ledger({}, {}, ITEM, 1.0)
    assert r[0]["status"] == "FAIL"


# ---------------------------------------------------------------- data contract
def test_contract_flags_missing_column_and_stale_feed():
    spec = {"columns": {"a": {"type": "BIGINT", "nullable": False}}, "freshness": {"column": "a", "must_equal": "paid_through"}}
    obs = {"columns": [], "type_failures": {}, "nulls": {}, "disallowed": {}, "max_value": {"a": "2026-01"}}
    r = controls.evaluate_contract("x", spec, obs, {"paid_through": "2026-02"})
    assert [x["status"] for x in r] == ["FAIL", "FAIL"]


# ---------------------------------------------------------------- end to end
def run_gate(tmp_path, mutate):
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    mutate(cfg)
    env = dict(os.environ, CONTROLS_CONFIG_DIR=str(cfg))
    return subprocess.run([sys.executable, str(ROOT / "code" / "python" / "01b_controls_gate.py")],
                          env=env, capture_output=True, text=True)


def edit(cfg, name, fn):
    p = cfg / name
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d))


@pytest.mark.skipif(not (OUTPUT_DIR / "practice.duckdb").exists(), reason="run steps 00, 00b and 01 first")
def test_gate_passes_as_configured(tmp_path):
    assert run_gate(tmp_path, lambda cfg: None).returncode == 0


@pytest.mark.skipif(not (OUTPUT_DIR / "practice.duckdb").exists(), reason="run steps 00, 00b and 01 first")
def test_gate_stops_without_the_exception(tmp_path):
    r = run_gate(tmp_path, lambda cfg: edit(cfg, "dq_exceptions.json", lambda d: d.update(exceptions=[])))
    assert r.returncode == 1 and "missing_eligibility_month" in r.stdout


@pytest.mark.skipif(not (OUTPUT_DIR / "practice.duckdb").exists(), reason="run steps 00, 00b and 01 first")
def test_gate_stops_on_unexplained_ledger_break(tmp_path):
    r = run_gate(tmp_path, lambda cfg: edit(cfg, "recon_items.json", lambda d: d.update(items=d["items"][1:])))
    assert r.returncode == 1 and "unexplained difference" in r.stdout


@pytest.mark.skipif(not (OUTPUT_DIR / "practice.duckdb").exists(), reason="run steps 00, 00b and 01 first")
def test_gate_stops_on_stale_feed(tmp_path):
    r = run_gate(tmp_path, lambda cfg: edit(cfg, "run_config.json", lambda d: d.update(paid_through="2026-03")))
    assert r.returncode == 1 and "freshness" in r.stdout

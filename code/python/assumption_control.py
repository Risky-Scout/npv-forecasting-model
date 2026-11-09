"""
One place that decides WHICH assumption set a run uses and WHETHER it is approved.

Every component that reads assumptions (the governance gate 03b, the NPV engine 04, the
workbook builder, the Databricks notebook) resolves the file through assumption_file(), so the
gate can never approve one file while the engine runs another. The engine also re-checks the
approval itself in production mode, so running 04 on its own cannot bypass the gate.

Resolution order for the assumption file:
    1. NPV_ASSUMPTIONS environment variable (absolute path), if set;
    2. run_config.json -> "assumption_file" (relative to the repository);
    3. code/python/npv_assumptions.json.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_config() -> dict:
    return json.loads(Path(os.environ.get("RUN_CONFIG", ROOT / "config" / "run_config.json")).read_text())


def assumption_file() -> Path:
    env = os.environ.get("NPV_ASSUMPTIONS")
    if env:
        return Path(env)
    rel = run_config().get("assumption_file", "code/python/npv_assumptions.json")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def approvals_file() -> Path:
    return Path(os.environ.get("ASSUMPTION_APPROVALS", ROOT / "config" / "assumption_approvals.json"))


def load_assumptions() -> dict:
    return json.loads(assumption_file().read_text())


def canonical_hash(obj: dict) -> str:
    """SHA-256 of the assumption set as canonical JSON (key order and whitespace do not matter)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# v4: configuration files that change results without being in the assumption file. They are
# governed with it: the approval covers the assumption set AND these files, so editing a curve,
# a trend benchmark or a forecast setting needs a new approval just like editing an assumption.
GOVERNED_CONFIGS = ["config/claim_cost_curves.json", "config/trend_benchmarks.json", "config/forecast_config.json",
                    "config/pricing_targets.json"]       # v4.2: the risk tolerance is pricing policy


def governed_configs() -> dict:
    return {rel: json.loads((ROOT / rel).read_text()) for rel in GOVERNED_CONFIGS if (ROOT / rel).exists()}


def approval_hash(A: dict) -> str:
    """SHA-256 the approval refers to: the assumption set plus the governed configuration files."""
    return canonical_hash({"assumptions": A, "governed_config": governed_configs()})


def approval_status(A: dict, approvals: list[dict] | None = None) -> dict:
    if approvals is None:
        f = approvals_file()
        approvals = json.loads(f.read_text())["approvals"] if f.exists() else []
    h = approval_hash(A)
    ver = A["global"].get("model_version")
    match = [a for a in approvals if a.get("sha256") == h]
    if not match:
        return dict(sha256=h, model_version=ver, approved=False, reason="no approval record for this exact assumption set")
    a = match[0]
    if a.get("model_version") != ver:
        return dict(sha256=h, model_version=ver, approved=False, approval_id=a.get("id"),
                    reason=f"approval {a.get('id')} is for model version {a.get('model_version')}, not {ver}")
    return dict(sha256=h, model_version=ver, approved=True, approval_id=a["id"], approved_by=a.get("approved_by"),
                approved_on=a.get("approved_on"), scope=a.get("scope"))


def require_approved_in_production(A: dict, who: str) -> dict:
    """Raise SystemExit(1) if this is a production run on an unapproved set; return the status otherwise.

    v4: in production the hash match is not enough. An approval only covers the REVIEW items it
    dispositions, and only 03b evaluates those (it needs the experience studies). So the engine also
    requires THIS run's 03b status file (output/assumptions/assumption_status.json) to exist, to be
    for the same hash, and to say approved. Running the engine without 03b in production is refused.
    """
    st = approval_status(A)
    if run_config().get("mode") != "production":
        return st
    if not st["approved"]:
        raise SystemExit(f"{who}: production run refused - assumption set {st['sha256'][:16]}... is not approved ({st['reason']}).")
    from kitpaths import OUTPUT_DIR
    sf = Path(os.environ.get("ASSUMPTION_OUT", OUTPUT_DIR / "assumptions")) / "assumption_status.json"
    if not sf.exists():
        raise SystemExit(f"{who}: production run refused - no assumption_status.json from 03b in this run (run 03b first).")
    g = json.loads(sf.read_text())
    if g.get("sha256") != st["sha256"] or not g.get("approved"):
        raise SystemExit(f"{who}: production run refused - 03b did not approve this assumption set "
                         f"({g.get('reason') or 'status is for a different set'}).")
    return st

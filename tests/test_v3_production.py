"""
Tests for the production path: source mapping / ingest and assumption governance.

  * Ingest round trip: the synthetic book is rewritten in a "production" layout
    (different column names, coded lines of business, YYYYMM months) using the
    example mapping, and ingesting it reproduces the canonical files exactly. This
    is the evidence that the mapping layer changes layout only, never values.
  * Ingest is strict: a missing column, an unmapped code or an unparseable month
    stops the run.
  * Governance: the assumption hash ignores formatting but changes with any value;
    the shipped assumption set is approved; an unapproved set stops a production
    run and only warns in synthetic mode; an approval for another model version
    does not count.
Run: python -m pytest -q tests   (after run_all.py, or after steps 00-03)
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "code" / "python"
sys.path.insert(0, str(PY))
from kitpaths import DATA_DIR  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


ing = _load("ingest", PY / "00a_ingest_source_extracts.py")
gov = _load("governance", PY / "03b_assumption_governance.py")
EXAMPLE = json.loads((ROOT / "config" / "source_mapping.production_example.json").read_text())
CANON = {"members": "members.csv", "member_months": "member_months.csv", "claims": "claims.csv", "finance_controls": "finance_controls.csv"}


def make_production_extracts(dest: Path, mapping: dict, rows: int | None = None) -> None:
    """Write the synthetic book in the example production layout (inverse of the mapping)."""
    dest.mkdir(parents=True, exist_ok=True)
    for feed, spec in mapping["feeds"].items():
        df = pd.read_csv(DATA_DIR / CANON[feed], dtype=str, keep_default_na=False, nrows=rows)
        for col, vmap in spec.get("value_maps", {}).items():
            inv = {v: k for k, v in vmap.items()}
            df[col] = df[col].map(inv)
        for col, fmt in spec.get("month_columns", {}).items():
            if fmt == "%Y%m":
                df[col] = df[col].str.replace("-", "", regex=False)
        df = df.rename(columns=spec["columns"])
        df.to_csv(dest / spec["source"], index=False)


@pytest.fixture(scope="module")
def roundtrip(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("prod")
    make_production_extracts(tmp / "landing", EXAMPLE)
    manifest = ing.ingest(EXAMPLE, tmp / "out", tmp / "landing")       # canonical files land in tmp/out
    return tmp, manifest


@pytest.mark.parametrize("feed", list(CANON))
def test_ingest_round_trip_reproduces_canonical_files(roundtrip, feed):
    tmp, manifest = roundtrip
    a = pd.read_csv(DATA_DIR / CANON[feed], dtype=str, keep_default_na=False)
    b = pd.read_csv(tmp / "out" / CANON[feed], dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(a, b)
    assert manifest["feeds"][feed]["rows"] == len(a)
    assert len(manifest["feeds"][feed]["source_sha256"]) == 64


def _one_feed(spec_feed="claims"):
    m = copy.deepcopy(EXAMPLE)
    m["feeds"] = {spec_feed: m["feeds"][spec_feed]}
    return m


def test_ingest_fails_on_missing_column(tmp_path):
    m = _one_feed()
    make_production_extracts(tmp_path / "landing", m, rows=200)
    f = tmp_path / "landing" / m["feeds"]["claims"]["source"]
    pd.read_csv(f, dtype=str).drop(columns=["PAID_AMT"]).to_csv(f, index=False)
    with pytest.raises(ValueError, match="missing"):
        ing.ingest(m, tmp_path / "out", tmp_path / "landing")


def test_ingest_fails_on_unmapped_code(tmp_path):
    m = _one_feed()
    make_production_extracts(tmp_path / "landing", m, rows=200)
    f = tmp_path / "landing" / m["feeds"]["claims"]["source"]
    d = pd.read_csv(f, dtype=str); d.loc[0, "PROD_LINE_CD"] = "MA"; d.to_csv(f, index=False)
    with pytest.raises(ValueError, match="no mapping"):
        ing.ingest(m, tmp_path / "out", tmp_path / "landing")


def test_ingest_fails_on_bad_month(tmp_path):
    m = _one_feed()
    make_production_extracts(tmp_path / "landing", m, rows=200)
    f = tmp_path / "landing" / m["feeds"]["claims"]["source"]
    d = pd.read_csv(f, dtype=str); d.loc[0, "PAID_YYYYMM"] = "202613"; d.to_csv(f, index=False)
    with pytest.raises(ValueError, match="do not parse"):
        ing.ingest(m, tmp_path / "out", tmp_path / "landing")


def test_ingest_parquet_with_nulls_matches_csv(tmp_path):
    """A Parquet delivery (typed columns, real nulls) must ingest to the same canonical text as CSV."""
    m = _one_feed("members")
    make_production_extracts(tmp_path / "landing", m, rows=500)
    f = tmp_path / "landing" / m["feeds"]["members"]["source"]
    d = pd.read_csv(f)                                      # typed: ints, NaN for blank months / reasons
    assert d["TERM_YYYYMM"].isna().any()
    pq = f.with_suffix(".parquet")
    d.astype({"TERM_YYYYMM": "Int64", "EFF_YYYYMM": "Int64"}).to_parquet(pq, index=False)
    m["feeds"]["members"]["source"] = pq.name; m["feeds"]["members"]["format"] = "parquet"
    ing.ingest(m, tmp_path / "out", tmp_path / "landing")
    a = pd.read_csv(DATA_DIR / "members.csv", dtype=str, keep_default_na=False, nrows=500)
    b = pd.read_csv(tmp_path / "out" / "members.csv", dtype=str, keep_default_na=False)
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------- governance
ASSUMPTIONS = json.loads((PY / "npv_assumptions.json").read_text())
APPROVALS = json.loads((ROOT / "config" / "assumption_approvals.json").read_text())["approvals"]


def test_hash_ignores_key_order_but_not_values():
    shuffled = json.loads(json.dumps(ASSUMPTIONS, sort_keys=True)[::1])
    assert gov.canonical_hash(shuffled) == gov.canonical_hash(ASSUMPTIONS)
    changed = copy.deepcopy(ASSUMPTIONS); changed["MEDSUPP"]["claim_trend"] += 1e-9
    assert gov.canonical_hash(changed) != gov.canonical_hash(ASSUMPTIONS)


def test_shipped_assumption_set_is_approved():
    st = gov.approval_status(ASSUMPTIONS, APPROVALS)
    assert st["approved"], st


def test_approval_for_other_model_version_does_not_count():
    # v4: edit the approval that actually matches the shipped set (not simply the first entry).
    a = copy.deepcopy(APPROVALS)
    h = gov.approval_hash(ASSUMPTIONS)
    match = [x for x in a if x["sha256"] == h]
    assert match, "shipped set has no approval"
    match[0]["model_version"] = "2.0.0"
    assert not gov.approval_status(ASSUMPTIONS, a)["approved"]


def _run_gate(tmp_path, mode):
    """Run 03b against an edited (unapproved) assumption set in the given mode; return exit code.

    Configuration and outputs are redirected to tmp_path, so the run's own files are untouched."""
    edited = copy.deepcopy(ASSUMPTIONS); edited["MEDSUPP"]["claim_trend"] = 0.061
    af = tmp_path / "edited_assumptions.json"; af.write_text(json.dumps(edited))
    cfg = json.loads((ROOT / "config" / "run_config.json").read_text()); cfg["mode"] = mode
    cf = tmp_path / "run_config.json"; cf.write_text(json.dumps(cfg))
    env = dict(os.environ, NPV_ASSUMPTIONS=str(af), RUN_CONFIG=str(cf), ASSUMPTION_OUT=str(tmp_path / "out"))
    r = subprocess.run([sys.executable, str(PY / "03b_assumption_governance.py")], cwd=PY, env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_unapproved_set_stops_production_run(tmp_path):
    code, out = _run_gate(tmp_path, "production")
    assert code == 1 and "NOT APPROVED" in out, out[-800:]


def test_unapproved_set_only_warns_in_synthetic_mode(tmp_path):
    code, out = _run_gate(tmp_path, "synthetic")
    assert code == 0 and "WARNING" in out, out[-800:]


def test_review_item_without_disposition_is_not_approved(tmp_path):
    """An approval must record a disposition for every REVIEW item, or production refuses the set.

    v4: the shipped set has no REVIEW items, so the test builds one. It moves the Med Supp trend
    off the credibility-weighted indication by more than the tolerance (a REVIEW item), approves
    that exact set WITHOUT a disposition, and requires production to refuse it; then it adds the
    disposition and requires the same set to pass."""
    edited = copy.deepcopy(ASSUMPTIONS); edited["MEDSUPP"]["claim_trend"] = 0.060
    af = tmp_path / "edited.json"; af.write_text(json.dumps(edited))
    entry = {"id": "TEST-REVIEW", "sha256": gov.approval_hash(edited), "model_version": edited["global"]["model_version"],
             "approved_by": "test", "approved_on": "2026-01-01", "scope": "test", "review_dispositions": {}}
    cfg = json.loads((ROOT / "config" / "run_config.json").read_text()); cfg["mode"] = "production"
    (tmp_path / "run_config.json").write_text(json.dumps(cfg))

    def run(ap):
        (tmp_path / "approvals.json").write_text(json.dumps({"approvals": ap}))
        env = dict(os.environ, NPV_ASSUMPTIONS=str(af), ASSUMPTION_APPROVALS=str(tmp_path / "approvals.json"),
                   RUN_CONFIG=str(tmp_path / "run_config.json"), ASSUMPTION_OUT=str(tmp_path / "out"))
        return subprocess.run([sys.executable, str(PY / "03b_assumption_governance.py")], cwd=PY, env=env, capture_output=True, text=True)

    r = run([entry])
    assert r.returncode == 1 and "no disposition" in r.stdout, r.stdout[-600:]
    st = json.loads((tmp_path / "out" / "assumption_status.json").read_text())
    assert "MEDSUPP.claim_trend" in st["review_items"]
    entry["review_dispositions"] = {k: "test disposition" for k in st["review_items"]}
    r = run([entry])
    assert r.returncode == 0 and "APPROVED" in r.stdout, r.stdout[-600:]

"""
controls.py
-------------------------------------------------------------------------------
Shared control logic for the data gate (Phase 1 of the roadmap to production).
Pure Python with no I/O, so the same rules run in the local pipeline
(01b_controls_gate.py), in the Databricks notebook and in the unit tests.

Three controls:
  1. Data contract  : each feed has the agreed columns, types, nullability,
                      allowed values, unique keys, freshness and volume.
  2. Data gate      : every BLOCKER finding is either repaired by an approved
                      cleaning rule within its tolerance, or covered by an
                      unexpired, specifically matching exception; otherwise
                      the run fails. WARN / INFO findings are logged only.
  3. Ledger tie-out : paid claims and premium by line and month agree with the
                      finance control totals within tolerance; any difference
                      must match a registered, explained reconciling item.

A control that fails returns status "FAIL"; the caller stops the run.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

from datetime import date


# ----------------------------------------------------------------------------
# 1. Data contract
# ----------------------------------------------------------------------------
def evaluate_contract(source: str, spec: dict, observed: dict, run: dict) -> list[dict]:
    """Compare one feed's observed profile with its contract.

    observed = {"columns": [...], "type_failures": {col: n}, "nulls": {col: n},
                "disallowed": {col: n}, "duplicate_keys": n,
                "max_value": {col: value}, "volume_breaks": [(group, prior, current, drop)]}
    Returns one row per check with status PASS / FAIL (structural) or WARN (volume).
    """
    rows = []
    expected = list(spec["columns"])
    missing = [c for c in expected if c not in observed["columns"]]
    extra = [c for c in observed["columns"] if c not in expected]
    rows.append(_row(source, "columns present", not missing,
                     f"missing {missing}" if missing else "all present"))
    if extra:  # new columns are tolerated but surfaced, so the contract is updated deliberately
        rows.append(_row(source, "unexpected columns", True, f"{extra} (update the contract)", warn=True))
    for col, c in spec["columns"].items():
        if col in missing:
            continue
        n_bad = observed["type_failures"].get(col, 0)
        rows.append(_row(source, f"type {col} {c['type']}", n_bad == 0, f"{n_bad} values fail"))
        if not c["nullable"]:
            n_null = observed["nulls"].get(col, 0)
            rows.append(_row(source, f"not null {col}", n_null == 0, f"{n_null} nulls"))
        if "allowed" in c:
            n_out = observed["disallowed"].get(col, 0)
            rows.append(_row(source, f"allowed values {col}", n_out == 0, f"{n_out} outside {c['allowed']}"))
    if "primary_key" in spec:
        d = observed.get("duplicate_keys", 0)
        rows.append(_row(source, f"unique key {spec['primary_key']}", d == 0, f"{d} duplicate keys"))
    if "freshness" in spec:
        f = spec["freshness"]
        want, got = run[f["must_equal"]], observed["max_value"].get(f["column"])
        rows.append(_row(source, f"freshness max({f['column']}) = {f['must_equal']}", got == want,
                         f"found {got}, expected {want}"))
    if "volume" in spec:
        breaks = observed.get("volume_breaks", [])
        rows.append(_row(source, f"volume drop vs prior month <= {spec['volume']['max_drop_vs_prior']:.0%}",
                         not breaks, f"{len(breaks)} breaks: {breaks[:3]}", warn=True))
    return rows


def _row(source, check, ok, detail, warn=False):
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    return {"control": "data contract", "source": source, "check": check, "status": status, "detail": detail}


# ----------------------------------------------------------------------------
# 2. Data gate
# ----------------------------------------------------------------------------
def evaluate_gate(findings: list[dict], policy: dict, exceptions: list[dict], run_date: str) -> list[dict]:
    """findings: one dict per check x lob:
         {"check", "lob", "count", "share" (metric value or None), "keys": [detail keys]}
    Returns one row per finding with a status and the reason it passed or failed."""
    today = date.fromisoformat(run_date)
    out = []
    for f in findings:
        rule = policy["checks"].get(f["check"])
        if rule is None:  # an unknown check is itself a control failure: the policy must cover it
            out.append(_gate(f, "FAIL", "check not in dq_policy.json"))
            continue
        sev = rule["severity"]
        if f["count"] == 0:
            out.append(_gate(f, "PASS", "none found", sev))
            continue
        if sev != "BLOCKER":
            out.append(_gate(f, "REVIEW" if sev == "WARN" else "NOTE",
                             rule.get("cleaning_rule") or "logged for review", sev))
            continue
        # BLOCKER with findings: cleaned within tolerance, or covered key by key by valid exceptions
        if rule.get("cleaning_rule"):
            tol = rule.get("tolerance")
            if tol is None or (f.get("share") is not None and f["share"] <= tol["max"]):
                out.append(_gate(f, "PASS-CLEANED",
                                 f"{rule['cleaning_rule']} ({tol['metric']} {f['share']:.4%} <= {tol['max']:.2%})"
                                 if tol else rule["cleaning_rule"], sev))
            else:
                out.append(_gate(f, "FAIL", f"{tol['metric']} {f['share']:.4%} exceeds tolerance {tol['max']:.2%}", sev))
            continue
        valid = {e["key"]: e for e in exceptions
                 if e["check"] == f["check"] and e["lob"] == f["lob"]
                 and date.fromisoformat(e["approved_on"]) <= today <= date.fromisoformat(e["expires"])}
        uncovered = [k for k in f.get("keys", []) if k not in valid]
        if f.get("keys") and not uncovered:
            ids = ", ".join(valid[k]["id"] for k in f["keys"])
            out.append(_gate(f, "PASS-EXCEPTION", f"approved exception {ids}", sev))
        else:
            reason = "no cleaning rule and no valid exception"
            if uncovered:
                reason += f" for {uncovered}"
            out.append(_gate(f, "FAIL", reason, sev))
    return out


def _gate(f, status, reason, sev=None):
    return {"control": "data gate", "check": f["check"], "lob": f["lob"], "severity": sev,
            "count": f["count"], "status": status, "reason": reason}


# ----------------------------------------------------------------------------
# 3. Ledger tie-out
# ----------------------------------------------------------------------------
def evaluate_ledger(feed: dict, ledger: dict, items: list[dict], tolerance: float) -> list[dict]:
    """feed, ledger: {(control, lob, month): amount}. A difference within tolerance passes; a larger
    difference passes only if registered reconciling items for that cell explain it to within tolerance."""
    out = []
    for key in sorted(set(feed) | set(ledger)):
        control, lob, month = key
        diff = ledger.get(key, 0.0) - feed.get(key, 0.0)
        explained = sum(i["amount"] for i in items
                        if (i["control"], i["lob"], i["month"]) == key)
        residual = diff - explained
        if abs(diff) <= tolerance:
            status, note = "PASS", "agrees"
        elif abs(residual) <= tolerance:
            ids = ", ".join(i["id"] for i in items if (i["control"], i["lob"], i["month"]) == key)
            status, note = "PASS-EXPLAINED", f"explained by {ids}"
        else:
            status, note = "FAIL", f"unexplained difference {residual:,.2f}"
        out.append({"control": "ledger tie-out", "measure": control, "lob": lob, "month": month,
                    "feed": round(feed.get(key, 0.0), 2), "ledger": round(ledger.get(key, 0.0), 2),
                    "difference": round(diff, 2), "explained": round(explained, 2),
                    "status": status, "note": note})
    # A registered item that matches no difference is stale and is flagged so the register stays clean
    for i in items:
        key = (i["control"], i["lob"], i["month"])
        if key not in feed and key not in ledger:
            out.append({"control": "ledger tie-out", "measure": i["control"], "lob": i["lob"], "month": i["month"],
                        "feed": 0.0, "ledger": 0.0, "difference": 0.0, "explained": i["amount"],
                        "status": "FAIL", "note": f"reconciling item {i['id']} matches no ledger cell"})
    return out


def overall(rows: list[dict]) -> bool:
    """True when no control row failed."""
    return all(r["status"] != "FAIL" for r in rows)

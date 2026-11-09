"""
08_run_manifest.py
-------------------------------------------------------------------------------
Writes output/run_manifest.json: the audit record of one run. It answers, for
any figure the run produced, "what exactly produced this?":

  * run settings (mode, run id, dates) and the environment (Python and package
    versions),
  * SHA-256 of every code file, SQL program and configuration file,
  * the ingest manifest (SHA-256 of every source extract and canonical file),
  * the assumption set's hash and approval record,
  * the headline results (cash and DE NPV by line) and the status of each
    control step recorded by run_all.py.

Rerunning with the same manifest hashes reproduces the results exactly (the SQL
kit is single-threaded and every random draw is seeded), which is the
reproducibility requirement for a model of record. In Databricks the same
record is written next to the Delta table versions the run read.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = OUTPUT_DIR


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    import assumption_control as _ac
    run = _ac.run_config()
    start = json.loads((OUT / "run_start.json").read_text()) if (OUT / "run_start.json").exists() else None
    t0 = datetime.strptime(start["started_at_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp() if start else None

    def fresh(p: Path):
        """Read an artefact only if this run wrote it (after run_start); otherwise None."""
        if not p.exists() or t0 is None or p.stat().st_mtime < t0:
            return None
        return json.loads(p.read_text()) if p.suffix == ".json" else p

    files = sorted([*ROOT.glob("code/**/*.py"), *ROOT.glob("code/**/*.sql"), *ROOT.glob("code/**/*.sas"),
                    *ROOT.glob("code/**/*.json"), *ROOT.glob("config/*.json"), *ROOT.glob("*.py"), ROOT / "requirements.txt"])
    pkgs = {}
    for p in ["numpy", "pandas", "scipy", "statsmodels", "duckdb", "openpyxl", "matplotlib", "pyarrow", "pyspark"]:
        try:
            pkgs[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pkgs[p] = None
    steps = fresh(OUT / "run_steps.json")
    recon = fresh(OUT / "controls" / "reconciliation.json")
    # Results are released only if THIS run's Excel-to-Python reconciliation passed. Step status is
    # recorded by run_all.py; a Databricks job has no run_steps.json, so the reconciliation marker
    # (written by 05 after the engine and workbook succeeded) is the condition in both cases.
    reconciled = bool(recon and recon.get("result") == "PASS")
    summ = fresh(OUT / "npv" / "npv_summary.csv")
    # v4: results also require THIS run's assumption gate to have approved the set (including a
    # disposition for every REVIEW item). A passing reconciliation alone is not enough.
    ast = fresh(OUT / "assumptions" / "assumption_status.json")
    approved = bool(ast and ast.get("approved"))
    results = None
    if summ is not None and reconciled and approved:
        s = pd.read_csv(summ)
        results = {r.lob: {"cash_npv": round(float(r.npv), 2), "de_npv": round(float(r.de_npv), 2),
                           "pv_loss_ratio": round(float(r.pv_loss_ratio), 6)} for r in s.itertuples()}
    v4 = {}
    pf = fresh(OUT / "npv" / "medsupp_model_points.csv")
    if pf is not None:
        r = pd.read_csv(pf).set_index("band").loc["PORTFOLIO"]
        v4["medsupp_portfolio_per_1000"] = {"cash_npv": round(float(r.cash_npv_per_1000), 2), "de_npv": round(float(r.de_npv_per_1000), 2)}
    mcs = fresh(OUT / "npv" / "npv_monte_carlo_stats.csv")
    if mcs is not None:
        m = pd.read_csv(mcs, index_col=0)
        v4["monte_carlo_prob_negative"] = {k: round(float(m.loc[k, "prob_npv_negative"]), 4) for k in m.index}
    manifest = {
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_start": start,
        "run": {k: v for k, v in run.items() if not k.startswith("_")},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "packages": pkgs},
        "code_and_config_sha256": {str(f.relative_to(ROOT)): sha(f) for f in files if f.exists()},
        "ingest": fresh(OUT / "controls" / "ingest_manifest.json"),
        "assumptions": fresh(OUT / "assumptions" / "assumption_status.json"),
        "steps": steps if steps is not None else "not recorded by the runner (Databricks job: see the job run's task statuses)",
        "reconciliation": recon,
        "results_status": "released (assumptions approved and reconciliation passed in this run)" if results else
                          ("WITHHELD: assumption set not approved in this run - do not use outputs" if not approved else
                           "WITHHELD: no passing reconciliation in this run - do not use outputs"),
        "results": results,
        # v4: the Med Supp portfolio (model points) is reported next to the base cell, and the
        # monitoring summary (alerts) is part of the cycle record. Both only when reconciled.
        "v4_results": v4 if results else None,
        "monitoring": fresh(OUT / "monitoring" / "monitoring_summary.json"),
        "note": "Sections are null when this run did not produce them (failed or skipped step); nothing is carried over from earlier runs.",
    }
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    a = manifest["assumptions"] or {}
    print(f"Run manifest written: {OUT / 'run_manifest.json'}")
    print(f"  mode={run.get('mode')}  run_id={run.get('run_id')}  files hashed={len(manifest['code_and_config_sha256'])}")
    print(f"  assumptions sha256={str(a.get('sha256'))[:16]}...  approved={a.get('approved')}  results={'present' if results else 'NOT PRODUCED IN THIS RUN'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

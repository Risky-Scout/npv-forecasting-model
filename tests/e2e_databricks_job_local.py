"""
e2e_databricks_job_local.py  (v4.1)
-------------------------------------------------------------------------------
Executes the Databricks Workflow definition (deploy/databricks_job.json) locally, task by task,
in the dependency order the job declares, in PRODUCTION mode on production-layout extracts.

What it proves (and what it cannot):
  * The job definition is well formed for Jobs API 2.1 in the ways the kit relies on:
    unique task keys, every depends_on points at a defined task, the graph is acyclic, every
    task names a defined job cluster, every python_file / notebook_path resolves to a file
    in the kit, and every libraries requirements file exists.
  * Running the tasks in the job's own topological order completes and gives the same headline
    results and the same data-derived outputs as run_all.py (the release run). A missing or
    mis-ordered dependency would fail here.
  * It cannot prove Databricks-runtime behaviour: library pins against the approved runtime,
    Unity Catalog permissions and Volume I/O need a dry run in the Humana workspace
    (RUNBOOK §2, item 9). Placeholders in <angle brackets> are reported, not filled.

Each task runs with the cluster's spark_env_vars, where NPV_DATA_DIR / NPV_OUTPUT_DIR /
NPV_VINTAGE_DIR are mapped to a local scratch "Volume". /Workspace/Repos/actuarial/npv_kit maps to
a scratch copy of the kit. Exit 0 = PASS.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import importlib.util
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code" / "python"))
from kitpaths import OUTPUT_DIR  # noqa: E402

REPO = "/Workspace/Repos/actuarial/npv_kit"
spec = importlib.util.spec_from_file_location("t", ROOT / "tests" / "test_v3_production.py")
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)


def validate(job: dict, kit: Path) -> tuple[list[str], list[str], list[str]]:
    """(errors, placeholders, topological order of task keys)."""
    errors, placeholders = [], []
    tasks = job["tasks"]
    keys = [x["task_key"] for x in tasks]
    if len(keys) != len(set(keys)):
        errors.append("duplicate task_key")
    clusters = {c["job_cluster_key"] for c in job.get("job_clusters", [])}
    by = {x["task_key"]: x for x in tasks}
    for x in tasks:
        for d in x.get("depends_on", []):
            if d["task_key"] not in by:
                errors.append(f"{x['task_key']}: depends on undefined task {d['task_key']}")
        if x.get("job_cluster_key") not in clusters:
            errors.append(f"{x['task_key']}: undefined job cluster {x.get('job_cluster_key')}")
        if "spark_python_task" in x:
            f = x["spark_python_task"]["python_file"]
        elif "notebook_task" in x:
            f = x["notebook_task"]["notebook_path"] + ".py"
        else:
            errors.append(f"{x['task_key']}: no spark_python_task or notebook_task"); continue
        if not f.startswith(REPO) or not (kit / f[len(REPO) + 1:]).exists():
            errors.append(f"{x['task_key']}: file not in the kit: {f}")
        for lib in x.get("libraries", []):
            r = lib.get("requirements")
            if r and not (kit / r[len(REPO) + 1:]).exists():
                errors.append(f"{x['task_key']}: requirements file missing: {r}")
    # Placeholders are scanned in the job settings only, not in the documentation strings.
    strip = lambda o: ({k: strip(v) for k, v in o.items() if not k.startswith("_")} if isinstance(o, dict)
                       else [strip(v) for v in o] if isinstance(o, list) else o)
    placeholders += re.findall(r"<[^<>]+>", json.dumps(strip(job)))
    # Kahn's algorithm, ties broken by the order tasks appear in the file (deterministic).
    indeg = {k: len(by[k].get("depends_on", [])) for k in keys}
    order, ready = [], [k for k in keys if indeg[k] == 0]
    while ready:
        k = ready.pop(0); order.append(k)
        for x in keys:
            if any(d["task_key"] == k for d in by[x].get("depends_on", [])):
                indeg[x] -= 1
                if indeg[x] == 0:
                    ready.append(x)
    if len(order) != len(keys):
        errors.append("dependency cycle")
    return errors, sorted(set(placeholders)), order


def main() -> int:
    job = json.loads((ROOT / "deploy" / "databricks_job.json").read_text())
    ref = pd.read_csv(OUTPUT_DIR / "npv" / "npv_summary.csv").set_index("lob")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        kit = tmp / "kit"
        shutil.copytree(ROOT, kit, ignore=shutil.ignore_patterns("output", ".pytest_cache", "__pycache__", "*.duckdb"))
        errors, placeholders, order = validate(job, kit)
        print(f"Job '{job['name']}': {len(job['tasks'])} tasks, {len(job['job_clusters'])} job clusters")
        print("Validation:", "PASS" if not errors else "FAIL"); [print("  -", e) for e in errors]
        print(f"Placeholders for the platform team ({len(placeholders)}):", ", ".join(placeholders))
        if errors:
            return 1
        # Production-layout extracts in a landing folder; synthetic data removed from the kit.
        landing = tmp / "landing"
        mapping = json.loads((ROOT / "config" / "source_mapping.production_example.json").read_text())
        t.make_production_extracts(landing, mapping)
        for f in ["members.csv", "member_months.csv", "claims.csv", "finance_controls.csv", "true_assumptions.json"]:
            (kit / "data" / f).unlink()
        mapping["source_root"] = str(landing)
        (kit / "config" / "source_mapping.e2e.json").write_text(json.dumps(mapping, indent=2))
        cfg = json.loads((kit / "config" / "run_config.json").read_text())
        cfg.update(mode="production", run_id="DBX-LOCAL-TEST", source_mapping="config/source_mapping.e2e.json")
        (kit / "config" / "run_config.json").write_text(json.dumps(cfg, indent=2))
        vol = tmp / "volume"
        local = {"NPV_DATA_DIR": str(vol / "data"), "NPV_OUTPUT_DIR": str(vol / "output"), "NPV_VINTAGE_DIR": str(vol / "vintage_registry")}
        clusters = {c["job_cluster_key"]: c["new_cluster"] for c in job["job_clusters"]}
        by = {x["task_key"]: x for x in job["tasks"]}
        print("Execution order:", " -> ".join(order))
        for k in order:
            x = by[k]
            env = dict(os.environ)
            for name, val in clusters[x["job_cluster_key"]].get("spark_env_vars", {}).items():
                env[name] = local.get(name, val)
            f = x["spark_python_task"]["python_file"] if "spark_python_task" in x else x["notebook_task"]["notebook_path"] + ".py"
            script = kit / f[len(REPO) + 1:]
            r = subprocess.run([sys.executable, str(script)], cwd=script.parent, env=env, capture_output=True, text=True)
            print(f"  {k:28s} {'PASS' if r.returncode == 0 else 'FAIL'}")
            if r.returncode != 0:
                print((r.stdout + r.stderr)[-2500:]); print("DATABRICKS JOB (LOCAL) FAIL"); return 1
        out = vol / "output"
        m = json.loads((out / "run_manifest.json").read_text())
        got = pd.read_csv(out / "npv" / "npv_summary.csv").set_index("lob")
        ok = m.get("results") is not None
        for lob in ref.index:
            for col in ("npv", "de_npv"):
                ok &= abs(float(got.loc[lob, col]) - float(ref.loc[lob, col])) < 0.01
        for rel in ["sql_results/05_npv_assumption_inputs_gold.csv", "trend/fitted_trends.csv", "assumptions/assumption_indications.csv",
                    "forecast/inforce_forecast_summary_annual.csv"]:
            a = pd.read_csv(OUTPUT_DIR / rel); b = pd.read_csv(out / rel)
            truth = [c for c in a.columns if c.startswith("true_")]
            try:
                pd.testing.assert_frame_equal(a.drop(columns=truth), b.drop(columns=truth), check_exact=False, rtol=1e-12, atol=1e-9)
            except AssertionError as e:
                ok = False; print(f"DIFFERS: {rel}: {str(e)[:200]}")
        print("Manifest results released:", m.get("results_status"))
        print("RESULT: " + ("PASS - the job definition runs end to end in its declared order and reproduces the release run"
                            if ok else "FAIL"))
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

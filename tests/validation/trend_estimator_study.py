"""
trend_estimator_study.py  (v4 validation, offline: about 1-2 minutes per seed)
-------------------------------------------------------------------------------
How good is the trend-setting method? On one data set nobody can tell: the fit is one draw.
This study regenerates the synthetic book under many random seeds, reruns the experience
pipeline (generator -> ingest -> SQL kit -> trend study) on each, and measures, against the
generator's known true trend:

  * bias and sampling SD of each fitted series (raw and normalized),
  * whether the member-level bootstrap standard error matches the true sampling SD
    (ratio, and coverage of the 95% interval fit +/- 1.96 SE),
  * the error of the v4 credibility blend (Z fit + (1 - Z) benchmark, benchmarks from
    config/trend_benchmarks.json) against the raw fit.

It is the evidence behind three v4 decisions: D&V must be normalized (the raw fit is biased),
the v3 "+/-2 points" uncertainty was too wide by about 3-4x, and the bootstrap SE is a usable
blend weight. Results: docs/validation/trend_estimator_study.md (written by --summarize).

Each seed runs on a frozen copy of code/ and config/ in a scratch directory, so edits to the kit
while it runs cannot leak into it (the first version of this study was contaminated that way:
it copied code per seed while the trend script was being edited).

Usage:  python tests/validation/trend_estimator_study.py --seeds 201-230 --work /tmp/tes
        python tests/validation/trend_estimator_study.py --summarize /tmp/tes
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STEPS = ["00_generate_synthetic_data.py", "00b_generate_finance_controls.py", "00a_ingest_source_extracts.py",
         "01_run_sql_kit.py", "02_claims_trend_forecast.py"]


def run(seeds: list[int], work: Path) -> None:
    snap = work / "snapshot"
    if not snap.exists():
        snap.mkdir(parents=True)
        shutil.copytree(ROOT / "code", snap / "code"); shutil.copytree(ROOT / "config", snap / "config")
    for sd in seeds:
        d = work / f"s{sd}"
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(snap / "code", d / "code"); shutil.copytree(snap / "config", d / "config")
        (d / "data").mkdir(); (d / "output").mkdir()
        g = d / "code" / "python" / "00_generate_synthetic_data.py"
        g.write_text(g.read_text().replace("default_rng(20261028)", f"default_rng({sd})"))
        ok = True
        for s in STEPS:
            r = subprocess.run([sys.executable, s], cwd=d / "code" / "python", capture_output=True, text=True)
            if r.returncode:
                ok = False; (d / "error.log").write_text(r.stdout[-4000:] + r.stderr[-4000:]); break
        if ok:
            shutil.copy(d / "output" / "trend" / "fitted_trends.csv", work / f"fit_{sd}.csv")
        shutil.rmtree(d / "data", ignore_errors=True); (d / "output" / "practice.duckdb").unlink(missing_ok=True)
        print(f"seed {sd}: {'done' if ok else 'FAILED (see error.log)'}", flush=True)


def summarize(work: Path) -> pd.DataFrame:
    tb = json.loads((ROOT / "config" / "trend_benchmarks.json").read_text())
    rows = []
    for f in sorted(work.glob("fit_*.csv")):
        d = pd.read_csv(f).set_index("lob")
        for lob in d.index:
            rows.append(dict(seed=int(f.stem.split("_")[1]), series=lob, fit=d.loc[lob, "fitted_annual_trend"],
                             se_boot=d.loc[lob, "trend_se_bootstrap"], se_hac=d.loc[lob, "trend_se_hac"],
                             true=d.loc[lob, "true_cost_trend"]))
    r = pd.DataFrame(rows)
    r["covered_95"] = (r.fit - r.true).abs() <= 1.96 * r.se_boot
    # Credibility blend per seed, for the series each line's assumption is set from.
    r["blend"] = np.nan
    for lob, b in tb.items():
        if not isinstance(b, dict) or "series" not in b:
            continue
        m = r.series == b["series"]
        z = b["benchmark_sd"] ** 2 / (b["benchmark_sd"] ** 2 + r.loc[m, "se_boot"] ** 2)
        r.loc[m, "blend"] = z * r.loc[m, "fit"] + (1 - z) * b["benchmark"]
    g = r.groupby("series").agg(seeds=("fit", "size"), true=("true", "first"), mean_fit=("fit", "mean"), sd_fit=("fit", "std"),
                                mean_se_boot=("se_boot", "mean"), mean_se_hac=("se_hac", "mean"),
                                coverage_95=("covered_95", "mean"))
    g["bias"] = g.mean_fit - g.true
    g["bias_se"] = g.sd_fit / np.sqrt(g.seeds)
    g["boot_se_over_true_sd"] = g.mean_se_boot / g.sd_fit
    g["hac_se_over_true_sd"] = g.mean_se_hac / g.sd_fit
    g["rmse_fit"] = r.groupby("series").apply(lambda x: float(np.sqrt(((x.fit - x.true) ** 2).mean())))
    g["rmse_blend"] = r.groupby("series").apply(lambda x: float(np.sqrt(((x.blend - x.true) ** 2).mean())) if x.blend.notna().any() else np.nan)
    return g.reset_index(), r


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", help="range a-b")
    ap.add_argument("--work", type=Path)
    ap.add_argument("--summarize", type=Path)
    a = ap.parse_args()
    if a.summarize:
        g, r = summarize(a.summarize)
        out = ROOT / "docs" / "validation"; out.mkdir(parents=True, exist_ok=True)
        g.to_csv(out / "trend_estimator_study.csv", index=False)
        r.to_csv(out / "trend_estimator_study_by_seed.csv", index=False)
        pd.set_option("display.width", 250)
        print(g.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    else:
        lo, hi = map(int, a.seeds.split("-"))
        run(list(range(lo, hi + 1)), a.work)

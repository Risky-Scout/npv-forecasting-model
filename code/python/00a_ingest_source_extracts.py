"""
00a_ingest_source_extracts.py
-------------------------------------------------------------------------------
Production entry point for data: maps each source extract onto the canonical
layout the rest of the pipeline reads (config/data_contract.json), and records
what was loaded.

  * Which file, which columns, which codes: config/source_mapping.json (the
    active mapping named in config/run_config.json). For the synthetic book the
    mapping is the identity; config/source_mapping.production_example.json shows
    the shape a Humana extract mapping takes.
  * Strict by design. A mapped source column that is missing, a code with no
    entry in the value map, or a month that does not parse in the stated format
    stops the run (exit 1). Nothing is silently dropped, coerced or guessed:
    a silent drop in claims is a mispriced product.
  * Values are read and written as text, so amounts and identifiers pass through
    exactly as extracted; typing and validation are the data contract's job
    (01b_controls_gate.py), which runs after the SQL kit.
  * Lineage: output/controls/ingest_manifest.json records, for every feed, the
    source path, its SHA-256, row count and the canonical file written. The run
    manifest (08_run_manifest.py) carries these hashes, so any result can be
    traced to the exact extracts that produced it.

Usage:
    python 00a_ingest_source_extracts.py                       # paths from run_config.json
    python 00a_ingest_source_extracts.py --mapping M --data-dir D --source-root S
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file, display  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_source(path: Path, fmt: str) -> pd.DataFrame:
    """Read an extract with every column as text (empty fields stay empty strings)."""
    if fmt == "csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if fmt == "parquet":
        # Arrow-backed types keep nullable integers as integers (65, not 65.0) and nulls as nulls;
        # every column is then rendered as text with nulls as empty strings, exactly like a CSV.
        df = pd.read_parquet(path, dtype_backend="pyarrow")
        return pd.DataFrame({c: df[c].astype("string").fillna("").astype(object) for c in df.columns})
    raise ValueError(f"unsupported format {fmt!r}")


def map_feed(name: str, spec: dict, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Apply one feed's column, code and month mapping. Returns (canonical frame, errors)."""
    errors = []
    cols = spec["columns"]                                   # canonical -> source column
    missing = [src for src in cols.values() if src not in df.columns]
    if missing:
        return df, [f"{name}: source columns missing from extract: {missing}"]
    out = pd.DataFrame({canon: df[src] for canon, src in cols.items()})
    for col, vmap in spec.get("value_maps", {}).items():
        unmapped = sorted(set(out[col]) - set(vmap))
        if unmapped:
            errors.append(f"{name}.{col}: codes with no mapping: {unmapped[:10]}{' ...' if len(unmapped) > 10 else ''}")
        out[col] = out[col].map(vmap)
    for col, fmt in spec.get("month_columns", {}).items():
        if fmt == "%Y-%m":
            ok = out[col].eq("") | out[col].str.fullmatch(r"\d{4}-(0[1-9]|1[0-2])")
            bad = out.loc[~ok, col]
        else:
            parsed = pd.to_datetime(out[col].where(out[col] != ""), format=fmt, errors="coerce")
            bad = out.loc[(out[col] != "") & parsed.isna(), col]
            out[col] = parsed.dt.strftime("%Y-%m").fillna("")
        if len(bad):
            errors.append(f"{name}.{col}: {len(bad)} values do not parse as {fmt} (e.g. {bad.iloc[0]!r})")
    return out, errors


def ingest(mapping: dict, data_dir: Path, source_root: Path | None = None) -> dict:
    """Map every feed; write canonical files into data_dir; return the manifest (raises on error).

    A mapping target such as 'data/members.csv' names the canonical file; it is written to
    data_dir / 'members.csv' (data_dir defaults to DATA_DIR, see kitpaths.py)."""
    src_root = Path(source_root) if source_root else Path(mapping["source_root"])
    if not src_root.is_absolute():
        src_root = ROOT / src_root
    manifest = {"ingested_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source_root": display(src_root), "feeds": {}}
    errors = []
    for name, spec in mapping["feeds"].items():
        src = src_root / spec["source"]
        tp = Path(spec["target"])
        target = Path(data_dir) / (tp.relative_to("data") if tp.parts[0] == "data" else tp)
        if not src.exists():
            errors.append(f"{name}: source file not found: {src}"); continue
        df = read_source(src, spec.get("format", "csv"))
        out, errs = map_feed(name, spec, df)
        errors += errs
        if errs:
            continue
        identity = src.resolve() == target.resolve()
        if not identity:
            target.parent.mkdir(parents=True, exist_ok=True)
            out.to_csv(target, index=False)
        manifest["feeds"][name] = {"source": display(src), "source_sha256": sha256(src), "rows": int(len(df)),
                                   "target": display(target),
                                   "target_sha256": sha256(target), "identity_mapping": identity}
    if errors:
        raise ValueError("\n".join(errors))
    return manifest


def main() -> int:
    import assumption_control as _ac
    run = _ac.run_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", default=str(ROOT / run.get("source_mapping", "config/source_mapping.json")))
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--source-root", default=None)
    args = ap.parse_args()
    mapping = json.loads(Path(args.mapping).read_text())
    try:
        manifest = ingest(mapping, Path(args.data_dir), args.source_root)
    except ValueError as e:
        print("INGEST FAILED - the run stops here:\n" + str(e))
        return 1
    manifest["mode"] = run.get("mode", "synthetic"); manifest["mapping_file"] = display(args.mapping)
    out = OUTPUT_DIR / "controls"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ingest_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name, f in manifest["feeds"].items():
        print(f"{name:16s} rows={f['rows']:>9,}  sha256={f['source_sha256'][:12]}  "
              f"{'identity (already canonical)' if f['identity_mapping'] else '-> ' + f['target']}")
    print("INGEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

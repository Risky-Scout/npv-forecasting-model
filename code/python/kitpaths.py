"""
Where the kit reads data and writes outputs.

Code and configuration always live in the repository. Data and outputs default to
<repo>/data and <repo>/output, and can be relocated with two environment variables:

    NPV_DATA_DIR    canonical input files (written by 00a ingest, read by every step)
    NPV_OUTPUT_DIR  every output: study results, NPV, workbook, controls, manifests

In production both point at governed storage (for example a Unity Catalog Volume,
/Volumes/<catalog>/<schema>/<volume>/...), because member-level eligibility and claims
are PHI and production volumes do not belong in a code repository. Set them on the job
cluster (deploy/databricks_job.json, spark_env_vars) so every task sees the same paths.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("NPV_DATA_DIR", ROOT / "data"))
OUTPUT_DIR = Path(os.environ.get("NPV_OUTPUT_DIR", ROOT / "output"))


def display(path) -> str:
    """Path for logs and manifests: relative to the repository when inside it, else absolute."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def data_file(rel: str) -> Path:
    """Resolve a config path such as 'data/members.csv' against DATA_DIR."""
    p = Path(rel)
    return DATA_DIR / p.relative_to("data") if p.parts and p.parts[0] == "data" else ROOT / p

"""
00b_generate_finance_controls.py
-------------------------------------------------------------------------------
SYNTHETIC STAND-IN for the finance control totals that a production run would
read from the general ledger (paid claims and earned premium by line of
business and month). It exists so the ledger tie-out in 01b_controls_gate.py
can be built and tested before real ledger access is granted.

How the stand-in is built, and the two reconciling items planted in it:
  * Paid claims by line and paid month = every claim in the raw feed (orphans
    and reversals included: they were paid), plus one planted manual journal
    entry of 12,500.00 in MEDSUPP 2025-06 (a provider settlement booked in the
    ledger but absent from the claims feed).
  * Earned premium by line and coverage month = de-duplicated feed premium
    (finance bills each member once), plus Kentucky Medicaid capitation for
    July 2025, which finance received even though the eligibility file was
    never delivered. Its amount is proxied by the average of the June and
    August 2025 Kentucky premium.

Both items are registered with explanations in config/recon_items.json; any
other difference fails the tie-out. Replace this script with a ledger extract
in production; nothing downstream changes.

Output: data/finance_controls.csv (control, lob, month, amount)
-------------------------------------------------------------------------------
"""
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = DATA_DIR
JOURNAL_ENTRY = ("paid_claims", "MEDSUPP", "2025-06", 12500.00)   # provider settlement (planted)

con = duckdb.connect()
claims = (DATA / "claims.csv").as_posix()
mm = (DATA / "member_months.csv").as_posix()
types_c = "{'incurred_month':'VARCHAR','paid_month':'VARCHAR'}"
types_m = "{'month':'VARCHAR','rate_cell':'VARCHAR'}"

paid = con.execute(f"""
    SELECT 'paid_claims' AS control, lob, paid_month AS month, ROUND(SUM(paid_amount), 2) AS amount
    FROM read_csv_auto('{claims}', header=true, types={types_c})
    GROUP BY lob, paid_month""").fetchdf()

prem = con.execute(f"""
    WITH dedup AS (SELECT DISTINCT * FROM read_csv_auto('{mm}', header=true, types={types_m}))
    SELECT 'premium' AS control, lob, month, ROUND(SUM(premium), 2) AS amount
    FROM dedup GROUP BY lob, month""").fetchdf()

ky = con.execute(f"""
    WITH dedup AS (SELECT DISTINCT * FROM read_csv_auto('{mm}', header=true, types={types_m}))
    SELECT ROUND(AVG(p), 2) FROM (
        SELECT month, SUM(premium) AS p FROM dedup
        WHERE lob = 'MEDICAID' AND state = 'KY' AND month IN ('2025-06', '2025-08') GROUP BY month)""").fetchone()[0]

c, lob, month, amt = JOURNAL_ENTRY
paid.loc[(paid.lob == lob) & (paid.month == month), "amount"] += amt
prem.loc[(prem.lob == "MEDICAID") & (prem.month == "2025-07"), "amount"] += ky

out = pd.concat([paid, prem], ignore_index=True).sort_values(["control", "lob", "month"])
out["amount"] = out["amount"].round(2)
out.to_csv(DATA / "finance_controls.csv", index=False)
print(f"finance controls written: {len(out)} rows -> data/finance_controls.csv")
print(f"planted items: journal entry {amt:,.2f} ({lob} {month}); KY Medicaid July 2025 premium {ky:,.2f}")

/* ============================================================================
   01_data_quality_checks.sql
   Purpose : Data-integrity gate that runs BEFORE any experience study or NPV
             run. Each check returns a count of flagged rows by line of
             business. Severity drives the action:
               BLOCKER - a non-zero count stops the pipeline. The defect would
                         bias exposure or claims directly (PMPM, lapse rates).
               WARN    - reviewed and documented, but the run may proceed.
               INFO    - volume tracked for reasonableness only.
   Standard: ASOP No. 23 (Data Quality). The actuary must review data for
             reasonableness and consistency, disclose material limitations,
             and be able to show what was checked. A repeatable, versioned
             check script is that evidence.
   Tables  : members, member_months, claims (raw / "bronze")
   Dialect : ANSI SQL. Runs as-is in DuckDB and Databricks SQL (Spark SQL).
             Months are stored as 'YYYY-MM' text and converted with
             CAST(month || '-01' AS DATE) where date comparison is needed.
             Text comparison of 'YYYY-MM' also sorts correctly.
   Reading : Checks interact. One root cause can produce two symptoms, e.g. a
             missing eligibility file for one state and month shows up both as
             a missing month (6b) and as claims with no eligibility (2).
             Fix the root cause once, not each symptom separately.
   ============================================================================ */

WITH
-- 1. Exact duplicate eligibility rows: the same member counted twice in a month.
--    Duplicates overstate exposure (member months), which understates PMPM and
--    lapse rates. Total rows minus distinct member-month keys = excess rows.
dup_mm AS (
    SELECT lob, COUNT(*) - COUNT(DISTINCT member_id || '|' || month) AS n_flagged
    FROM member_months
    GROUP BY lob
),

-- 2. Claims for a member who had no eligibility in the incurred month.
--    Orphans inflate PMPM because the claim has no member-month exposure in the
--    denominator. Implemented as an anti-join: LEFT JOIN to eligibility and keep
--    rows where the eligibility side is NULL. Matching is on incurred month,
--    because experience is studied by incurred month, not paid month.
orphan_claims AS (
    SELECT c.lob, COUNT(*) AS n_flagged
    FROM claims c
    LEFT JOIN (SELECT DISTINCT member_id, month FROM member_months) e
           ON e.member_id = c.member_id AND e.month = c.incurred_month
    WHERE e.member_id IS NULL
    GROUP BY c.lob
),

-- 3. Paid date before incurred date: impossible, indicates a date-field error.
--    Left unfixed, it creates negative payment lags in the completion triangle.
paid_before_incurred AS (
    SELECT lob, COUNT(*) AS n_flagged
    FROM claims
    WHERE CAST(paid_month || '-01' AS DATE) < CAST(incurred_month || '-01' AS DATE)
    GROUP BY lob
),

-- 4. Reversals (negative paid). Legitimate adjustments, but they must net
--    against the original claim. Volume is tracked because a spike usually
--    means a reprocessing project that will distort recent paid patterns.
reversals AS (
    SELECT lob, COUNT(*) AS n_flagged
    FROM claims
    WHERE paid_amount < 0
    GROUP BY lob
),

-- 5. Large / outlier claims. Threshold is set by line: a $1.8M Med Supp claim
--    is implausible (Medicare pays first and the supplement covers cost
--    sharing). A $1.8M Medicaid claim is possible but needs review, and is
--    capped at a pooling point in experience studies (see 02).
outliers AS (
    SELECT lob, COUNT(*) AS n_flagged
    FROM claims
    WHERE paid_amount > CASE lob {{LARGE_CLAIM_WHEN}} END      -- review thresholds, config/dq_policy.json
    GROUP BY lob
),

-- 6. Month-over-month membership drop > 40% by line and state: the classic
--    signature of a partial or failed eligibility feed rather than real
--    disenrollment. Real lapse and churn do not move 40% in one month.
monthly_counts AS (
    SELECT lob, state, month, COUNT(DISTINCT member_id) AS members
    FROM member_months
    GROUP BY lob, state, month
),
mom AS (
    SELECT lob, state, month, members,
           LAG(members) OVER (PARTITION BY lob, state ORDER BY month) AS prior_members
    FROM monthly_counts
),
feed_drops AS (
    SELECT lob, COUNT(*) AS n_flagged
    FROM mom
    WHERE prior_members IS NOT NULL AND members < 0.60 * prior_members
    GROUP BY lob
),
-- 6b. Missing months: a line/state with no rows at all in a month its
--     neighbours have. Check 6 cannot see this: LAG only compares rows that
--     exist, so with a month absent it compares the month after to the month
--     before and reports nothing. Detecting an absence needs the full expected
--     grid (every line/state CROSS JOIN every calendar month), then an
--     anti-join to the actual counts. Months before a line/state first
--     appears are excluded so a new state launch is not flagged.
calendar AS (SELECT DISTINCT month FROM member_months),
lob_state AS (SELECT DISTINCT lob, state FROM member_months),
missing_months AS (
    SELECT ls.lob, COUNT(*) AS n_flagged
    FROM lob_state ls
    CROSS JOIN calendar cal
    LEFT JOIN monthly_counts mc
           ON mc.lob = ls.lob AND mc.state = ls.state AND mc.month = cal.month
    WHERE mc.members IS NULL
      AND cal.month >= (SELECT MIN(month) FROM member_months m2
                        WHERE m2.lob = ls.lob AND m2.state = ls.state)
    GROUP BY ls.lob
),

-- 7. Eligibility outside the member's enrollment span (before issue or after
--    termination). Exposure after termination understates the lapse rate,
--    because the member keeps contributing months after leaving.
outside_span AS (
    SELECT mm.lob, COUNT(*) AS n_flagged
    FROM member_months mm
    JOIN members m ON m.member_id = mm.member_id
    WHERE mm.month < m.issue_month
       OR (m.term_month IS NOT NULL AND mm.month > m.term_month)
    GROUP BY mm.lob
),

-- 8. Null keys: a claim that cannot be tied to a member, a month or an amount
--    cannot be placed in any experience cell.
null_keys AS (
    SELECT lob, COUNT(*) AS n_flagged
    FROM claims
    WHERE member_id IS NULL OR incurred_month IS NULL OR paid_amount IS NULL
    GROUP BY lob
),

-- Collect every check's counts. Each check only returns a row for a line where
-- something was flagged: GROUP BY over zero matching rows returns no row at all.
results AS (
    SELECT 'Duplicate member-months'         AS check_name, lob, n_flagged FROM dup_mm
    UNION ALL SELECT 'Claims with no eligibility',     lob, n_flagged FROM orphan_claims
    UNION ALL SELECT 'Paid before incurred',           lob, n_flagged FROM paid_before_incurred
    UNION ALL SELECT 'Missing eligibility month',      lob, n_flagged FROM missing_months
    UNION ALL SELECT 'Membership drop > 40% MoM',      lob, n_flagged FROM feed_drops
    UNION ALL SELECT 'Eligibility outside enrollment', lob, n_flagged FROM outside_span
    UNION ALL SELECT 'Large / outlier claims',         lob, n_flagged FROM outliers
    UNION ALL SELECT 'Claim reversals (negative)',     lob, n_flagged FROM reversals
    UNION ALL SELECT 'Null key fields',                lob, n_flagged FROM null_keys
),
-- The full list of checks x lines, so a check that finds nothing still shows
-- up with 0 and PASS. A check that silently disappears is indistinguishable
-- from a check that never ran, and the output is the audit evidence that every
-- check was performed. Severity is assigned here, in one place.
check_list AS (
    SELECT * FROM (VALUES
        ('Duplicate member-months',        'BLOCKER'),
        ('Claims with no eligibility',     'BLOCKER'),
        ('Paid before incurred',           'BLOCKER'),
        ('Missing eligibility month',      'BLOCKER'),
        ('Null key fields',                'BLOCKER'),
        ('Membership drop > 40% MoM',      'WARN'),
        ('Eligibility outside enrollment', 'WARN'),
        ('Large / outlier claims',         'WARN'),
        ('Claim reversals (negative)',     'INFO')
    ) AS t(check_name, severity)
),
lobs AS (SELECT DISTINCT lob FROM member_months)

-- Final report: every check x every line, LEFT JOINed to the counts so that a
-- missing count becomes 0 / PASS. Sorted blockers first.
SELECT c.check_name,
       c.severity,
       l.lob,
       COALESCE(r.n_flagged, 0) AS n_flagged,
       CASE WHEN COALESCE(r.n_flagged, 0) = 0 THEN 'PASS'
            WHEN c.severity = 'BLOCKER'      THEN 'FAIL - fix before NPV run'
            WHEN c.severity = 'WARN'         THEN 'REVIEW'
            ELSE 'NOTE' END     AS status
FROM check_list c
CROSS JOIN lobs l
LEFT JOIN results r ON r.check_name = c.check_name AND r.lob = l.lob
ORDER BY CASE c.severity WHEN 'BLOCKER' THEN 1 WHEN 'WARN' THEN 2 ELSE 3 END,
         c.check_name, l.lob;

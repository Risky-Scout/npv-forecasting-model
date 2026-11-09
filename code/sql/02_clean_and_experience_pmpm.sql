/* ============================================================================
   02_clean_and_experience_pmpm.sql
   Purpose : (a) Build cleaned "silver" tables that fix the issues found by
             01_data_quality_checks.sql, then (b) produce monthly experience:
             member months, paid claims, PMPM, utilization per 1,000 and raw
             loss ratio by line of business and incurred month.
   Basis   : Exposure is measured in member months (one member enrolled for
             one month = 1). Claims are assigned to the month they were
             INCURRED, not paid, so each month's claims line up with the
             members who generated them and with that month's premium.
   Notes   : Claims here are NOT yet completed for IBNR - recent incurred
             months will look artificially low because their claims are still
             being paid. 03_completion_factors.sql fixes that. Do not trend or
             price off these PMPMs directly.
   ============================================================================ */

-- (a1) De-duplicated eligibility: one row per member per month. The GROUP BY
--      collapses exact duplicates. MAX(premium) keeps a single premium if the
--      duplicate rows disagree. Any remaining key collision (same member and
--      month, different plan or rate cell) would still show in a re-run of 01.
CREATE OR REPLACE TABLE silver_member_months AS
SELECT member_id, lob, month, state, plan, rate_cell, MAX(premium) AS premium
FROM member_months
GROUP BY member_id, lob, month, state, plan, rate_cell;

-- (a2) Clean claims.
--      Orphans: the INNER JOIN to de-duplicated eligibility drops claims with
--      no member-month exposure in the incurred month. This is the right
--      treatment for genuine orphans. Where 01 traced orphans to a missing
--      eligibility file, the preferred fix is to reload that file so neither
--      the claims nor the exposure are lost.
--      Pooling: large claims are capped at a pooling point by line, so one
--      catastrophic claim does not distort the PMPM used for assumptions.
--      paid_capped feeds the experience. paid_excess is kept separately: the
--      governance step reports it as a pooling charge (excess / pooled claims),
--      to be added back when an all-in claim cost is set from pooled experience.
CREATE OR REPLACE TABLE silver_claims AS
SELECT c.claim_id, c.member_id, c.lob, c.incurred_month, c.paid_month,
       c.service_category,
       c.paid_amount,
       CASE WHEN c.paid_amount > p.pool_point THEN p.pool_point ELSE c.paid_amount END AS paid_capped,
       CASE WHEN c.paid_amount > p.pool_point THEN c.paid_amount - p.pool_point ELSE 0 END AS paid_excess
FROM claims c
JOIN (SELECT DISTINCT member_id, month FROM silver_member_months) e
  ON e.member_id = c.member_id AND e.month = c.incurred_month
-- Pooling points by line, from config/experience_parameters.json
JOIN (VALUES {{POOLING_VALUES}})
     AS p(lob, pool_point)
  ON p.lob = c.lob;

-- (b) Monthly experience by line of business and incurred month.
--     Exposure and claims are aggregated separately and then joined, so a month
--     with members but no claims keeps its exposure (LEFT JOIN, claims -> 0).
WITH exposure AS (
    SELECT lob, month,
           COUNT(*)      AS member_months,
           SUM(premium)  AS premium
    FROM silver_member_months
    GROUP BY lob, month
),
-- Claims by incurred month, pooled amounts. claim_count excludes reversals
-- (negative lines) so utilization counts services, not adjustments.
paid AS (
    SELECT lob, incurred_month AS month,
           SUM(paid_capped)                                 AS paid_claims,
           SUM(CASE WHEN paid_amount > 0 THEN 1 ELSE 0 END) AS claim_count
    FROM silver_claims
    GROUP BY lob, incurred_month
)
-- PMPM = dollars / member months. Utilization is annualized per 1,000 members:
-- claims x 12 x 1,000 / member months. Raw loss ratio = incurred-basis paid
-- claims / premium, before completion, so it is understated in recent months.
SELECT e.lob,
       e.month,
       e.member_months,
       ROUND(e.premium, 0)                                      AS premium,
       ROUND(COALESCE(p.paid_claims, 0), 0)                     AS paid_claims_incurred,
       ROUND(e.premium / e.member_months, 2)                    AS premium_pmpm,
       ROUND(COALESCE(p.paid_claims, 0) / e.member_months, 2)   AS paid_pmpm,
       ROUND(1000.0 * 12 * COALESCE(p.claim_count, 0) / e.member_months, 1) AS claims_per_1000_per_year,
       ROUND(COALESCE(p.paid_claims, 0) / NULLIF(e.premium, 0), 4)          AS raw_loss_ratio
FROM exposure e
LEFT JOIN paid p ON p.lob = e.lob AND p.month = e.month
ORDER BY e.lob, e.month;

/* ============================================================================
   05_npv_assumption_inputs_gold.sql
   Purpose : Builds the "gold" table the NPV model reads: monthly exposure,
             premium and completed claims by line of business, then summarizes
             it by calendar year into completed claims PMPM, premium PMPM,
             completed loss ratio and the observed annual claims-cost trend
             for the most recent complete calendar years.
   Requires: silver_member_months (02), lag_triangle + completion_factors (03)
   Design  : Medallion layering: bronze (raw feeds) -> silver (cleaned, 02) ->
             gold (assumption-ready, here). The NPV model reads only gold, so
             every data fix lives upstream in one place.
             Every NPV run should be able to point at the exact gold table
             version it used (in Databricks: Delta table versions / time
             travel, e.g. reading the table AS OF a recorded version). That
             makes results reproducible and auditable: a prior quarter's NPV
             can be rerun on the data it was actually built from.
   ============================================================================ */

-- Gold monthly experience: exposure and premium from silver eligibility, claims
-- completed for IBNR using the chain-ladder factors from 03.
CREATE OR REPLACE TABLE gold_monthly_experience AS
WITH completed AS (
    -- Latest diagonal of the triangle for each incurred month, divided by the
    -- completion factor at that lag (lag 8 = fully complete, factor 1.0)
    SELECT t.lob, t.incurred_month AS month,
           t.cum_paid / COALESCE(cf.completion_factor, 1.0) AS completed_claims
    FROM lag_triangle t
    LEFT JOIN completion_factors cf ON cf.lob = t.lob AND cf.lag = t.lag
    WHERE t.lag = LEAST(t.observable_lag, 8)
),
exposure AS (
    SELECT lob, month, COUNT(*) AS member_months, SUM(premium) AS premium
    FROM silver_member_months GROUP BY lob, month
)
-- Exposure drives the row set, so a month with members but no claims is kept
-- with zero claims rather than dropped
SELECT e.lob, e.month, CAST(SUBSTR(e.month, 1, 4) AS INT) AS year,
       e.member_months, e.premium, COALESCE(c.completed_claims, 0) AS completed_claims
FROM exposure e
LEFT JOIN completed c ON c.lob = e.lob AND c.month = e.month;

-- Calendar-year summary. PMPMs and loss ratios are ratios of sums (exposure
-- weighted), not averages of monthly ratios, so small months do not get the
-- same weight as large ones.
WITH yearly AS (
    SELECT lob, year,
           SUM(member_months)                         AS member_months,
           SUM(premium) / SUM(member_months)          AS premium_pmpm,
           SUM(completed_claims) / SUM(member_months) AS claims_pmpm,
           SUM(completed_claims) / SUM(premium)       AS loss_ratio
    FROM gold_monthly_experience
    GROUP BY lob, year
),
-- Year-over-year change in completed claims PMPM. This is total observed
-- trend: unit cost, utilization and mix (including block aging for Med Supp)
-- together, not a normalized trend. A partial latest year mixes seasonality
-- into the change and should not be used as a trend point on its own.
with_trend AS (
    SELECT *,
           claims_pmpm / LAG(claims_pmpm) OVER (PARTITION BY lob ORDER BY year) - 1 AS claims_pmpm_change
    FROM yearly
)
SELECT lob, year, member_months,
       ROUND(premium_pmpm, 2)        AS premium_pmpm,
       ROUND(claims_pmpm, 2)         AS completed_claims_pmpm,
       ROUND(loss_ratio, 4)          AS completed_loss_ratio,
       ROUND(claims_pmpm_change, 4)  AS claims_pmpm_change_yoy
FROM with_trend
ORDER BY lob, year;

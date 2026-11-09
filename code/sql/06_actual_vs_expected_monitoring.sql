/* ============================================================================
   06_actual_vs_expected_monitoring.sql
   Purpose : Ongoing monitoring once an NPV model is in production. Compares
             actual completed claims and actual terminations to what the
             model's assumptions expected, by line and quarter.
             A/E near 1.00 = assumption holding; persistent drift (e.g. three
             quarters above 1.05) = trigger an assumption review.
   Bands   : Claims A/E outside +/-5% or termination A/E outside +/-15% is
             flagged REVIEW. Terminations get a wider band because they are
             counts of rare events and noisier quarter to quarter than claims
             dollars.
   Action  : A REVIEW flag is not by itself a reason to change an assumption.
             An assumption is changed when the deviation is
               persistent  - in the same direction over several quarters,
               credible    - enough exposure that it is not random noise,
               explainable - a known cause (rate action, benefit or network
                             change, policy event) that is expected to continue.
             One-quarter spikes with a known, non-recurring cause are
             documented and left alone.
   Credibility: Fixed bands treat every line the same. A small D&V block can
             breach +/-5% from noise alone, while a large Medicaid block
             breaching it is meaningful. A production version would scale the
             band with exposure (e.g. limited-fluctuation credibility, or a
             confidence interval on expected terminations).
   Inputs  : gold_monthly_experience (05), silver_member_months (02), members.
             The expected assumptions below are the "priced" assumptions
             (replace with the production assumption table). They are
             deliberately simple (no seasonality, no duration effect), so the
             flags show what a simple assumption misses: the Med Supp January
             rate-increase lapse spike (Q1 termination A/E high every year)
             and the Medicaid 2023-24 redetermination wave (eligibility
             rechecks after the continuous-enrollment period ended, so
             terminations far above base churn for several quarters).
   ============================================================================ */

WITH assumptions AS (
    -- lob, expected claims PMPM in base-year dollars, annual trend, expected monthly termination
    -- (config/experience_parameters.json -> ae_expected_basis; Med Supp trend includes aging,
    -- Medicaid termination is base churn before any redetermination wave)
    SELECT * FROM (VALUES {{AE_BASIS_VALUES}}
    ) AS a(lob, base_pmpm_2022, annual_trend, exp_monthly_term)
),
-- Quarterly claims: actual = completed claims from gold. Expected = member
-- months x base PMPM trended from January 2022 to each month at the annual
-- trend rate (fractional years by month), so expected moves with exposure.
claims_q AS (
    SELECT g.lob,
           CAST(g.year AS VARCHAR(4)) || '-Q' || CAST(CAST(CEIL(CAST(SUBSTR(g.month, 6, 2) AS INT) / 3.0) AS INT) AS VARCHAR(1)) AS quarter,
           SUM(g.completed_claims) AS actual_claims,
           SUM(g.member_months * a.base_pmpm_2022
               * POWER(1 + a.annual_trend, (g.year - {{AE_BASE_YEAR}}) + (CAST(SUBSTR(g.month, 6, 2) AS INT) - 1) / 12.0)) AS expected_claims
    FROM gold_monthly_experience g
    JOIN assumptions a ON a.lob = g.lob
    GROUP BY 1, 2
),
-- Quarterly terminations, all causes: actual = members whose term month is
-- this exposure month. Expected = sum of the monthly termination rate over
-- every exposure month (one member month contributes exp_monthly_term).
terms_q AS (
    SELECT s.lob,
           SUBSTR(s.month, 1, 4) || '-Q' || CAST(CAST(CEIL(CAST(SUBSTR(s.month, 6, 2) AS INT) / 3.0) AS INT) AS VARCHAR(1)) AS quarter,
           SUM(CASE WHEN m.term_month = s.month THEN 1 ELSE 0 END) AS actual_terms,
           SUM(a.exp_monthly_term)                                 AS expected_terms
    FROM silver_member_months s
    JOIN members m     ON m.member_id = s.member_id
    JOIN assumptions a ON a.lob = s.lob
    GROUP BY 1, 2
)
-- A/E ratios and the review flag by line and quarter. The latest quarters
-- rely most on completion factors, so a claims A/E there is less certain.
SELECT c.lob, c.quarter,
       ROUND(c.actual_claims, 0)                        AS actual_claims,
       ROUND(c.expected_claims, 0)                      AS expected_claims,
       ROUND(c.actual_claims / c.expected_claims, 3)    AS claims_a_to_e,
       t.actual_terms,
       ROUND(t.expected_terms, 0)                       AS expected_terms,
       ROUND(t.actual_terms / t.expected_terms, 3)      AS termination_a_to_e,
       CASE WHEN ABS(c.actual_claims / c.expected_claims - 1) > 0.05
              OR ABS(t.actual_terms / t.expected_terms - 1) > 0.15
            THEN 'REVIEW' ELSE 'OK' END                 AS flag
FROM claims_q c
JOIN terms_q t ON t.lob = c.lob AND t.quarter = c.quarter
ORDER BY c.lob, c.quarter;

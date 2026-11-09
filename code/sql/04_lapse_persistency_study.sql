/* ============================================================================
   04_lapse_persistency_study.sql
   Purpose : Persistency (lapse) experience study - the single biggest driver
             of a Medicare Supplement NPV (it sets how many years of margin a
             policy earns against its acquisition cost) and a major driver for
             D&V and Medicaid (disenrollment).
   Method  : Exposure = member months observed in the study window, bucketed
             by policy duration (year 1, 2, 3+). Duration matters because
             lapse is typically highest in the early policy years and then
             settles. A termination is counted in the month it happens, and
             that month is counted as exposure (the member was enrolled).
             Monthly rate = terminations / exposure months.
             Annualized   = 1 - (1 - monthly)^12, i.e. the probability of
             terminating at some point in 12 months at a constant monthly rate.
             This is not monthly x 12, which overstates at higher rates.
             Deaths are split out so lapse can be modeled separately from
             mortality. For an aging Med Supp block, mortality follows a
             mortality table by attained age while lapse follows duration and
             rate actions. A combined termination rate would mix the two and
             project poorly as the block ages.
   Also    : January-only lapse for Med Supp (rate-increase "shock lapse").
             Med Supp rate increases commonly take effect in January, and
             policyholders shop and lapse right after. The excess of January
             over other months, durations 2+, measures that shock. Duration 1
             is excluded so the early-duration lapse pattern does not confound it.
   ============================================================================ */

-- One row per member month with policy duration in completed months since issue
WITH mm AS (
    SELECT s.member_id, s.lob, s.month, m.issue_month, m.term_month, m.term_reason,
           ( (CAST(SUBSTR(s.month, 1, 4) AS INT) - CAST(SUBSTR(m.issue_month, 1, 4) AS INT)) * 12
           + (CAST(SUBSTR(s.month, 6, 2) AS INT) - CAST(SUBSTR(m.issue_month, 6, 2) AS INT)) ) AS dur_months
    FROM silver_member_months s
    JOIN members m ON m.member_id = s.member_id
),
-- Flag each exposure month: duration year, January indicator, and whether the
-- member terminated in that month by cause. Voluntary lapse (Med Supp, D&V) and
-- disenrollment (Medicaid) are counted as lapse. Death is kept separate.
flagged AS (
    SELECT lob, month,
           CASE WHEN dur_months < 12 THEN '1' WHEN dur_months < 24 THEN '2' ELSE '3+' END AS duration_year,
           CASE WHEN SUBSTR(month, 6, 2) = '01' THEN 1 ELSE 0 END                 AS is_january,
           CASE WHEN term_month = month AND term_reason IN ('LAPSE', 'DISENROLL') THEN 1 ELSE 0 END AS lapsed,
           CASE WHEN term_month = month AND term_reason = 'DEATH' THEN 1 ELSE 0 END AS died
    FROM mm
),
-- Exposure and decrements by line and duration year
by_duration AS (
    SELECT lob, duration_year,
           COUNT(*)      AS exposure_months,
           SUM(lapsed)   AS lapses,
           SUM(died)     AS deaths
    FROM flagged
    GROUP BY lob, duration_year
),
-- Monthly lapse rate in January vs all other months, durations 2+.
-- CASE without ELSE returns NULL, so each SUM covers only its own month group:
-- the numerator sums lapses and the denominator counts exposure months.
jan_vs_other AS (   -- shock lapse check for Med Supp, durations 2+
    SELECT lob,
           SUM(CASE WHEN is_january = 1 THEN lapsed END) * 1.0
         / NULLIF(SUM(CASE WHEN is_january = 1 THEN 1 END), 0)  AS jan_monthly_lapse,
           SUM(CASE WHEN is_january = 0 THEN lapsed END) * 1.0
         / NULLIF(SUM(CASE WHEN is_january = 0 THEN 1 END), 0)  AS other_monthly_lapse
    FROM flagged
    WHERE duration_year <> '1'
    GROUP BY lob
)
-- Annualized lapse and death rates by duration, plus the January excess. The
-- January figure is computed for every line but is mainly meaningful for Med Supp.
SELECT d.lob,
       d.duration_year,
       d.exposure_months,
       ROUND(d.exposure_months / 12.0, 1)                            AS exposure_years,
       d.lapses,
       d.deaths,
       ROUND(1 - POWER(1 - d.lapses * 1.0 / d.exposure_months, 12), 4) AS annual_lapse_rate,
       ROUND(1 - POWER(1 - d.deaths * 1.0 / d.exposure_months, 12), 4) AS annual_death_rate,
       ROUND(j.jan_monthly_lapse - j.other_monthly_lapse, 4)            AS january_excess_monthly_lapse
FROM by_duration d
LEFT JOIN jan_vs_other j ON j.lob = d.lob
ORDER BY d.lob, d.duration_year;

/* ============================================================================
   03_completion_factors.sql
   Purpose : Claims-lag triangle and chain-ladder completion factors, so that
             recent incurred months can be "completed" for claims incurred but
             not yet paid (IBNR). Completed PMPM is what feeds trend and the
             NPV models.
   Method  : 1. Cumulative paid by incurred month and payment lag (months
                from incurred month to paid month), through the paid-through
                date. Lags beyond 8 are grouped into lag 8.
             2. Volume-weighted age-to-age (ATA) factors: sum of cumulative
                paid at lag k+1 / sum at lag k, over the most recent 18
                incurred months that have both lags observed. Volume weighting
                lets large months count more than small noisy ones. The
                18-month window reflects current claim-processing speed while
                keeping enough volume for stable factors.
             3. Completion factor at lag k = 1 / product of ATA factors from
                lag k on (the cumulative development factor to ultimate).
   Limits  : - Tail factor = 1.00 beyond lag 8 (assumes nothing is paid after
               the last grouped lag). Reasonable for short-tailed health
               claims, but should be checked against run-out data.
             - The most recent 1-3 incurred months are poorly completed: little
               has been paid, so a small error in paid-to-date is multiplied
               by a large factor. In practice those months are set using a
               PMPM projection or Bornhuetter-Ferguson (blend of expected PMPM
               and chain-ladder) rather than pure chain ladder.
             - Chain ladder assumes a stable payment pattern. A claim system
               change or processing backlog breaks that assumption.
   Requires: silver_claims (built in 02)
   ============================================================================ */

-- Triangle: one row per line x incurred month x observable lag, cumulative paid.
CREATE OR REPLACE TABLE lag_triangle AS
WITH params AS (SELECT '{{PAID_THROUGH}}' AS paid_through),   -- valuation (paid-through) month, from run_config.json
base AS (
    -- Payment lag in whole months = (year difference x 12) + month difference
    SELECT lob, incurred_month,
           (CAST(SUBSTR(paid_month, 1, 4) AS INT) - CAST(SUBSTR(incurred_month, 1, 4) AS INT)) * 12
         + (CAST(SUBSTR(paid_month, 6, 2) AS INT) - CAST(SUBSTR(incurred_month, 6, 2) AS INT)) AS lag,
           paid_capped
    FROM silver_claims
),
max_lag AS (   -- how many lags are observable for each incurred month at paid-through
    SELECT DISTINCT b.lob, b.incurred_month,
           (CAST(SUBSTR(p.paid_through, 1, 4) AS INT) - CAST(SUBSTR(b.incurred_month, 1, 4) AS INT)) * 12
         + (CAST(SUBSTR(p.paid_through, 6, 2) AS INT) - CAST(SUBSTR(b.incurred_month, 6, 2) AS INT)) AS observable_lag
    FROM base b CROSS JOIN params p
),
lags AS (SELECT * FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8)) AS t(lag)),
-- Full grid of observable cells. Without it, a lag with zero payments would
-- have no row and the cumulative sum would skip a development period.
grid AS (
    SELECT m.lob, m.incurred_month, l.lag, m.observable_lag
    FROM max_lag m JOIN lags l ON l.lag <= m.observable_lag
),
-- Incremental paid by lag, with lags 8+ grouped into the last cell
inc AS (
    SELECT lob, incurred_month, LEAST(lag, 8) AS lag, SUM(paid_capped) AS paid
    FROM base GROUP BY lob, incurred_month, LEAST(lag, 8)
)
-- Running sum of incremental paid across lags = cumulative paid to date
SELECT g.lob, g.incurred_month, g.lag, g.observable_lag,
       SUM(COALESCE(i.paid, 0)) OVER (PARTITION BY g.lob, g.incurred_month ORDER BY g.lag
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_paid
FROM grid g
LEFT JOIN inc i ON i.lob = g.lob AND i.incurred_month = g.incurred_month AND i.lag = g.lag;

-- Age-to-age factors (lag k -> k+1), volume weighted over recent months
CREATE OR REPLACE TABLE completion_factors AS
WITH pairs AS (
    -- Self-join pairs each cell with the next lag of the same incurred month.
    -- Only months that have observed lag k+1 survive the join.
    SELECT a.lob, a.lag, a.incurred_month, a.cum_paid AS cum_k, b.cum_paid AS cum_k1
    FROM lag_triangle a
    JOIN lag_triangle b
      ON b.lob = a.lob AND b.incurred_month = a.incurred_month AND b.lag = a.lag + 1
),
ranked AS (   -- rn = 1 is the most recent incurred month with this lag pair observed
    SELECT *, ROW_NUMBER() OVER (PARTITION BY lob, lag ORDER BY incurred_month DESC) AS rn
    FROM pairs
),
ata AS (   -- volume-weighted ATA = SUM(cum at k+1) / SUM(cum at k), latest 18 months
    SELECT lob, lag, SUM(cum_k1) / NULLIF(SUM(cum_k), 0) AS ata_factor
    FROM ranked WHERE rn <= 18
    GROUP BY lob, lag
),
-- Cumulative development to ultimate from lag k = product of ATA for lags >= k.
-- SQL has no PRODUCT aggregate, so use EXP(SUM(LN(x))) = product of x, as a
-- running window from the highest lag down. Requires every ATA > 0 (LN).
-- No tail factor is applied beyond the last lag (tail = 1.00).
cdf AS (
    SELECT lob, lag, ata_factor,
           EXP(SUM(LN(ata_factor)) OVER (PARTITION BY lob ORDER BY lag DESC
                                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS dev_to_ult
    FROM ata
)
-- Completion factor = share of ultimate claims paid by lag k = 1 / dev_to_ult
SELECT lob, lag, ROUND(ata_factor, 4) AS ata_factor, ROUND(dev_to_ult, 4) AS dev_to_ult,
       ROUND(1.0 / dev_to_ult, 4) AS completion_factor
FROM cdf;

-- Completed claims and IBNR by incurred month. Each incurred month is taken at
-- its latest diagonal (current lag), and completed = paid to date / completion
-- factor. Months at lag 8 have no factor and are treated as fully complete
-- (COALESCE to 1.0). IBNR = completed - paid to date.
SELECT t.lob, t.incurred_month, t.observable_lag,
       ROUND(t.cum_paid, 0)                                   AS paid_to_date,
       COALESCE(cf.completion_factor, 1.0)                    AS completion_factor,
       ROUND(t.cum_paid / COALESCE(cf.completion_factor, 1.0), 0)          AS completed_claims,
       ROUND(t.cum_paid / COALESCE(cf.completion_factor, 1.0) - t.cum_paid, 0) AS ibnr
FROM lag_triangle t
LEFT JOIN completion_factors cf ON cf.lob = t.lob AND cf.lag = t.lag
WHERE t.lag = LEAST(t.observable_lag, 8)
ORDER BY t.lob, t.incurred_month;

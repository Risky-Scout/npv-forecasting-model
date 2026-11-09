/* ==========================================================================================
   VERSION NOTE (kit v3.0.0) - READ FIRST
   These SAS programs mirror VERSION 2 of the model (no anti-selective or dynamic shock lapse,
   one blended Medicaid rate cell, no profit test). They have NOT been executed: no SAS
   licence was available where they were written. They hold their own hard-coded copy of the
   v2 base assumptions (not read from npv_assumptions.json, so they must be kept in sync by
   hand) and should reproduce the v2 Med Supp NPV of 924,382 per 1,000 policies, which equals
   the v3 Python engine with every global.features flag set to false (tests/test_v3_model.py).
   Check the SAS log on the first run.
   Bringing SAS to v3 is listed in docs/RUNBOOK.md; Python is the production engine.
   ========================================================================================== */
/*============================================================================
  02_clean_experience_and_completion.sas
  (a) Silver tables: de-duplicate eligibility, drop orphan claims, pool large
      claims.
  (b) Claims lag triangle and chain-ladder completion factors (IBNR).
  (c) Completed monthly experience and yearly PMPM / loss ratio / trend.
  Mirrors: sql/02, sql/03, sql/05
  Databricks equivalent: sections 3 (Silver) and 4 (chain ladder, gold
  monthly experience) of npv_pipeline_databricks.py.
  Requires: 01_import_and_data_quality.sas

  STATUS: NOT YET EXECUTED (no SAS license where written). On the first run,
  review the log, in particular the MERGE and BY-group steps in (b), and tie
  the completion factors to the Databricks/Python output before using them.
============================================================================*/
%include "&root/code/sas/00_setup.sas";

/* ---- (a) Silver ----------------------------------------------------------*/
/* One row per member-month. NODUPKEY keeps the first record per key; the
   Spark version groups on all dimension columns and keeps max(premium). The
   two agree when duplicates are exact repeats; if Silver row counts differ
   between tools, inspect the duplicates.                                    */
proc sort data=npv.member_months out=npv.silver_mm nodupkey;
  by member_id month;
run;

/* Inner join to eligibility drops orphan claims; inner join to the pooling
   table caps each claim at its line pooling point. paid_excess is retained so
   a pooling charge can be added back in the NPV assumption. The pooling table
   is built from three one-row selects (FROM members(obs=1) supplies the row);
   LENGTH=13 on the first sets the width of lob for the union.
   Two-argument MIN/MAX here are row-level functions, not aggregates.        */
proc sql;
  create table npv.silver_claims as
  select c.*,
         min(c.paid_amount, p.pool_point)          as paid_capped,
         max(c.paid_amount - p.pool_point, 0)      as paid_excess
  from npv.claims c
       inner join (select distinct member_id, month from npv.silver_mm) e
         on e.member_id = c.member_id and e.month = c.incurred_month
       inner join (select 'MEDSUPP' as lob length=13, 50000 as pool_point from npv.members(obs=1)
                   union all select 'DENTAL_VISION', 10000 from npv.members(obs=1)
                   union all select 'MEDICAID', 250000 from npv.members(obs=1)) p
         on p.lob = c.lob;
quit;
/* Reconciliation check (as in the Databricks notebook): sum(paid_amount) in
   npv.claims less orphan paid should equal sum(paid_amount) here.            */

/* ---- (b) Lag triangle -----------------------------------------------------*/
/* Paid-through month as an integer index, resolved once at macro time */
%let pt_idx = %eval(%substr(&paid_thru,1,4)*12 + %substr(&paid_thru,6,2));

/* Lag in months from incurred to paid; lags of 8+ are bucketed into lag 8,
   which is treated as fully complete (tail factor 1.0).                      */
data _claim_lags;
  set npv.silver_claims;
  lag = min(%month_index(paid_month) - %month_index(incurred_month), 8);
run;

/* Incremental capped paid by (lob, incurred month, lag) */
proc sql;
  create table _inc as
  select lob, incurred_month, lag, sum(paid_capped) as paid
  from _claim_lags group by lob, incurred_month, lag;
quit;

/* full grid of observable (incurred month x lag) cells, then cumulate */
/* One row per incurred month (FIRST.), then one row per lag from 0 to the
   paid-through diagonal (max 8). Without the grid, a lag with no payments
   would be absent instead of zero. NOTSORTED tolerates the SQL output order;
   the NODUPKEY sort that follows guarantees one row per cell.               */
data _grid;
  set _inc(keep=lob incurred_month);
  by lob incurred_month notsorted;
  if first.incurred_month;
  observable_lag = &pt_idx - %month_index(incurred_month);
  do lag = 0 to min(observable_lag, 8); output; end;
run;
proc sort data=_grid nodupkey; by lob incurred_month lag; run;
proc sort data=_inc; by lob incurred_month lag; run;

/* Match-merge grid to payments (IN= keeps grid cells only). The sum statement
   cum_paid + x implies RETAIN, so cum_paid accumulates across lags and is
   reset at FIRST.incurred_month; COALESCE treats empty cells as zero. Same
   result as the Spark running-sum window.                                    */
data npv.lag_triangle;
  merge _grid(in=g) _inc;
  by lob incurred_month lag;
  if g;
  if first.incurred_month then cum_paid = 0;
  cum_paid + coalesce(paid, 0);
run;

/* age-to-age factors: volume weighted over the latest 18 incurred months */
/* Self-join pairs each cell with the same incurred month one lag later, so
   only months observed at both lags contribute. Ordered by incurred month
   descending within (lob, lag), so the counter rn keeps the 18 most recent.  */
proc sql;
  create table _pairs as
  select a.lob, a.lag, a.incurred_month, a.cum_paid as cum_k, b.cum_paid as cum_k1
  from npv.lag_triangle a inner join npv.lag_triangle b
    on b.lob = a.lob and b.incurred_month = a.incurred_month and b.lag = a.lag + 1
  order by lob, lag, incurred_month desc;
quit;
data _pairs; set _pairs; by lob lag;
  if first.lag then rn = 0; rn + 1;
  if rn <= 18;
run;
/* Volume weighted: ratio of sums, not the average of individual ratios */
proc sql;
  create table _ata as
  select lob, lag, sum(cum_k1) / sum(cum_k) as ata_factor
  from _pairs group by lob, lag order by lob, lag desc;
quit;
/* Development to ultimate = product of ATA factors from lag k to the tail.
   Processed from the highest lag down; RETAIN carries the running product,
   reset to 1 at the start of each line. completion_factor = share of
   ultimate paid by lag k. Spark computes the same product as exp(sum(log)). */
data npv.completion_factors;
  set _ata; by lob descending lag;
  retain dev_to_ult;
  if first.lob then dev_to_ult = 1;
  dev_to_ult = dev_to_ult * ata_factor;
  completion_factor = 1 / dev_to_ult;
run;
proc sort data=npv.completion_factors; by lob lag; run;

title "Chain-ladder completion factors";
proc print data=npv.completion_factors noobs; format ata_factor dev_to_ult completion_factor 8.4; run;

/* triangle view for review (incurred month x lag), last 12 months */
/* Wide layout (one column per lag) for visual review of the triangle only */
proc transpose data=npv.lag_triangle(where=(incurred_month >= '2025-03'))
               out=npv.triangle_view(drop=_name_) prefix=lag_;
  by lob incurred_month; id lag; var cum_paid;
run;

/* ---- (c) Completed experience --------------------------------------------*/
/* Exposure and premium from Silver eligibility, left-joined to each incurred
   month latest diagonal (lag = min(observable_lag, 8)) and its completion
   factor. Lag 8 has no factor (tail), so COALESCE(..., 1) treats it as
   complete; months with members but no claims get zero completed claims.
   Claims are pooled; the pooling charge is a separate load.                  */
proc sql;
  create table npv.gold_monthly as
  select e.lob, e.month, input(substr(e.month,1,4),4.) as year,
         e.member_months, e.premium,
         coalesce(t.cum_paid / coalesce(f.completion_factor, 1), 0) as completed_claims
  from (select lob, month, count(*) as member_months, sum(premium) as premium
        from npv.silver_mm group by lob, month) e
  left join npv.lag_triangle t
    on t.lob = e.lob and t.incurred_month = e.month and t.lag = min(t.observable_lag, 8)
  left join npv.completion_factors f
    on f.lob = t.lob and f.lag = t.lag;

  /* Annual PMPM and loss ratio as ratios of sums (exposure weighted) */
  create table npv.yearly_experience as
  select lob, year, sum(member_months) as member_months,
         sum(premium)/sum(member_months)          as premium_pmpm      format=8.2,
         sum(completed_claims)/sum(member_months) as claims_pmpm       format=8.2,
         sum(completed_claims)/sum(premium)       as loss_ratio        format=percent8.1
  from npv.gold_monthly group by lob, year order by lob, year;
quit;

/* Year-over-year change in completed claims PMPM (raw trend indicator).
   IFN evaluates all of its arguments, so LAG() executes on every row and its
   queue stays aligned; FIRST.lob then blanks the cross-line value. Calling
   LAG() inside IF/THEN would skip rows and return the wrong prior year.
   The latest year is the least complete; read its change with caution.       */
data npv.yearly_experience; set npv.yearly_experience; by lob;
  claims_pmpm_change = ifn(first.lob, ., claims_pmpm / lag(claims_pmpm) - 1);
  format claims_pmpm_change percent8.1;
run;
title "Completed experience by year";
proc print data=npv.yearly_experience noobs; run;
title;

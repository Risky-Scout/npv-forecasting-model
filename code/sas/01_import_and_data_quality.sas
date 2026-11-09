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
  01_import_and_data_quality.sas
  Reads the three raw CSVs with EXPLICIT informats (never let SAS guess types
  on production data), then runs the data-quality gate.
  Mirrors: sql/01_data_quality_checks.sql
  Databricks equivalent: sections 1 (Bronze ingest, control totals) and 2
  (data-quality gate) of npv_pipeline_databricks.py.

  STATUS: NOT YET EXECUTED (no SAS license where written). On the first run,
  review the log for invalid-data notes from INPUT, truncation, and the
  DQ BLOCKER warnings written by %dq_check before running 02.
============================================================================*/
%include "&root/code/sas/00_setup.sas";

/* ---- Bronze: typed raw ingest ---------------------------------------------
   DATA step INFILE instead of PROC IMPORT: PROC IMPORT guesses types and
   lengths from the first rows, which can truncate codes or read IDs as text.
   LENGTH fixes character widths before INPUT; DSD handles commas, quotes and
   consecutive delimiters (empty fields read as missing, e.g. term_month for
   members still in force); FIRSTOBS=2 skips the header; TRUNCOVER keeps short
   records from wrapping onto the next line.
---------------------------------------------------------------------------*/
data npv.members;
  infile "&datadir/members.csv" dsd firstobs=2 truncover;
  length lob $13 state $2 plan $13 rate_cell $5 issue_month term_month $7 term_reason $9;
  input member_id lob $ state $ plan $ rate_cell $ issue_month $ issue_age term_month $ term_reason $;
run;

data npv.member_months;
  infile "&datadir/member_months.csv" dsd firstobs=2 truncover;
  length lob $13 month $7 state $2 plan $13 rate_cell $5;
  input member_id lob $ month $ state $ plan $ rate_cell $ premium;
run;

data npv.claims;
  infile "&datadir/claims.csv" dsd firstobs=2 truncover;
  length lob $13 incurred_month paid_month $7 service_category $13;
  input claim_id member_id lob $ incurred_month $ paid_month $ paid_amount service_category $;
run;

/* Control totals - every later step reconciles back to these */
/* Scalar subqueries return one value each; FROM members(obs=1) supplies the
   single row they are selected onto. Compare to the Databricks control dict. */
proc sql;
  title "Control totals (bronze)";
  select (select count(*) from npv.members)        as members,
         (select count(*) from npv.member_months)  as member_months,
         (select count(*) from npv.claims)         as claims,
         (select sum(paid_amount) from npv.claims) as paid_total format=dollar18.2
  from npv.members(obs=1);
quit;

/* ---- Data-quality gate ---------------------------------------------------*/
/* Severities follow sql/01. The Databricks gate is narrower: it fails only on
   paid-before-incurred and treats duplicates and orphans as known issues that
   Silver resolves. Here a BLOCKER writes a log WARNING; it does not stop.     */
/* Start each run with an empty log so counts are not appended to a prior run */
proc datasets lib=npv nolist; delete dq_log; quit;

/* Same member and month delivered twice: overstates exposure and premium */
%dq_check(name=Duplicate member-months, severity=BLOCKER,
  sql=%str(select member_id, month from npv.member_months
           group by member_id, month having count(*) > 1));

/* Orphan claims: no member-month in the incurred month (removed in 02) */
%dq_check(name=Claims with no eligibility, severity=BLOCKER,
  sql=%str(select c.claim_id from npv.claims c
           left join (select distinct member_id, month from npv.member_months) e
             on e.member_id = c.member_id and e.month = c.incurred_month
           where e.member_id is missing));

/* Impossible lag; points to a date mapping error in the feed */
%dq_check(name=Paid before incurred, severity=BLOCKER,
  sql=%str(select claim_id from npv.claims where paid_month < incurred_month));

/* Reversals are expected; they net within the incurred month in the triangle */
%dq_check(name=Claim reversals (negative), severity=INFO,
  sql=%str(select claim_id from npv.claims where paid_amount < 0));

/* Reasonability flag for large claims. The Medicaid threshold here (500,000)
   follows sql/01 and differs from the 250,000 Medicaid pooling point applied
   in 02, so this count is not the number of claims that get pooled.         */
%dq_check(name=Large claims over pool point, severity=WARN,
  sql=%str(select claim_id from npv.claims
           where (lob='MEDSUPP' and paid_amount > 50000)
              or (lob='DENTAL_VISION' and paid_amount > 10000)
              or (lob='MEDICAID' and paid_amount > 500000)));

/* Missing month by line/state: expected calendar minus actual */
/* Expected = every month in the file from the first month each line/state
   appears; NOT EXISTS removes months actually present. What remains is a
   feed drop, which would otherwise read as a membership dip and lapse spike. */
proc sql;
  create table _cal as select distinct month from npv.member_months;
  create table _first as select lob, state, min(month) as first_month
    from npv.member_months group by lob, state;
  create table _actual as select distinct lob, state, month from npv.member_months;
  create table npv.missing_months as
    select f.lob, f.state, c.month
    from _first f, _cal c
    where c.month >= f.first_month
      and not exists (select 1 from _actual a
                      where a.lob=f.lob and a.state=f.state and a.month=c.month);
quit;
%dq_check(name=Missing eligibility month, severity=BLOCKER,
  sql=%str(select * from npv.missing_months));

/* ---- Review output: DQ log and the specific missing months ----------------*/
title "Data-quality log";
proc print data=npv.dq_log noobs; run;
title "Missing line/state/months (feed drops)";
proc print data=npv.missing_months noobs; run;
title;

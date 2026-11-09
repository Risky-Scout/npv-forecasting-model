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
  00_setup.sas
  Paths, libraries and shared macros used by every program in this folder.
  Run this first (or %include it at the top of each program).

  NOTE: These programs mirror the Python/SQL kit step for step, so you can
  compare results across tools. They were written to standard Base SAS,
  SAS/STAT (PROC GENMOD) and SAS/ETS (PROC ESM, PROC ARIMA) syntax but could
  not be executed in the environment they were written in (no SAS license
  there). Run them on SAS 9.4 / SAS Viya / SAS OnDemand for Academics (free)
  and check the log for warnings the first time.

  STATUS: NOT YET EXECUTED. On the first run, review the full log for ERROR,
  WARNING and NOTE lines (uninitialized variables, character-to-numeric
  conversion, missing values generated, MERGE with repeated BY values) before
  relying on any output.

  Run order: 00 -> 01 -> 02 -> 03 -> 04. Each later program %includes this file;
  &root must therefore be defined before the first %include (set it in the
  session, autoexec, or by running this file once interactively).
============================================================================*/

/* ---- Paths and evaluation date ------------------------------------------*/
%let root     = /home/&sysuserid/humana_prep;   /* <-- change to your folder */
%let datadir  = &root/data;
%let outdir   = &root/output/sas;
%let paid_thru = 2026-02;                       /* claims paid-through month */
/* paid_thru is the triangle evaluation date and must match PAID_THROUGH in the
   Python/Databricks pipeline, or completion factors will not tie.           */

/* validvarname=v7 keeps names SAS-standard; mprint echoes generated macro
   code to the log so each %dq_check and %medsupp call can be audited.        */
options nodate nonumber validvarname=v7 mprint;
/* The output folder must already exist; LIBNAME does not create it.         */
libname npv "&outdir";

/*--------------------------------------------------------------------------
  %month_index(var): converts a 'YYYY-MM' character month to an integer
  (year*12 + month) so lags and durations are simple subtraction.
  Expands to an expression (no semicolon), so it can be used inside a DATA
  step assignment or a PROC SQL select. Same as month_index() in the Spark
  notebook. Months stay character elsewhere; zero-padded YYYY-MM strings sort
  and compare correctly as text.
--------------------------------------------------------------------------*/
%macro month_index(var);
  (input(substr(&var, 1, 4), 4.) * 12 + input(substr(&var, 6, 2), 2.))
%mend month_index;

/*--------------------------------------------------------------------------
  %dq_check(name=, severity=, sql=): runs one data-quality query that returns
  a count, appends it to npv.dq_log, and writes a WARNING to the log when a
  BLOCKER check fails, so the problem cannot be missed.
  Parameters:
    name=      label stored in dq_log (40 characters max)
    severity=  BLOCKER, WARN or INFO
    sql=       a SELECT returning one row per flagged record; wrap it in
               %str() so commas inside the query do not split the argument
  Mirrors the Python checks dict and sql/01_data_quality_checks.sql.
  Difference from the Databricks gate: this macro logs and warns but does not
  stop the program. In a scheduled batch job, a BLOCKER would end the run
  (for example with %abort cancel) so later programs cannot read bad data.
--------------------------------------------------------------------------*/
%macro dq_check(name=, severity=, sql=);
  /* count(*) over the check query; TRIMMED strips blanks from the macro value */
  proc sql noprint;
    select count(*) into :_n trimmed from (&sql);
  quit;
  /* one-row log record for this check */
  data _dq; length check_name $40 severity $8;
    check_name = "&name"; severity = "&severity"; n_flagged = &_n;
  run;
  /* BASE= is created on the first call; FORCE appends even if attributes differ */
  proc append base=npv.dq_log data=_dq force; run;
  %if &severity = BLOCKER and &_n > 0 %then
    %put WARNING: DQ BLOCKER &name flagged &_n rows - resolve before running the NPV.;
%mend dq_check;

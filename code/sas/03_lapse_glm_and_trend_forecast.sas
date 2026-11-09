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
  03_lapse_glm_and_trend_forecast.sas
  (a) Lapse study: exposure and lapses by duration, then a Poisson GLM
      (PROC GENMOD) with log(exposure) offset - same model as the Python
      03_lapse_glm_study.py.
  (b) Claims PMPM trend: log-linear regression with month-of-year effects
      (the slope is the annual trend), plus a PROC ESM and PROC ARIMA forecast
      36 months ahead with a 12-month holdout test.
  Requires: 02_clean_experience_and_completion.sas
  Mirrors: sql/04_lapse_persistency_study.sql, python/03_lapse_glm_study.py,
  python/02_claims_trend_forecast.py; Databricks section 5 holds the raw
  lapse study only (no GLM).

  STATUS: NOT YET EXECUTED (no SAS license where written). On the first run,
  review the log and the GENMOD convergence and fit statistics, and remove
  NOPRINT from PROC ARIMA once to inspect the residual diagnostics.
============================================================================*/
%include "&root/code/sas/00_setup.sas";

/* ---- (a) Lapse study ------------------------------------------------------*/
/* Member-month exposure with issue data attached; dur_m = months since issue */
proc sql;
  create table _mm as
  select s.lob, s.month, s.plan, m.issue_age, m.term_month, m.term_reason,
         %month_index(s.month) - %month_index(m.issue_month) as dur_m
  from npv.silver_mm s inner join npv.members m on m.member_id = s.member_id;
quit;

/* Rating variables for the GLM:
     duration   policy years 1, 2, 3+ (select vs ultimate lapse)
     jan_shock  January in a renewal year: the rate-increase month, so the
                anniversary shock is estimated separately from base lapse
     lapsed     voluntary termination counted in the last covered month;
                deaths and other reasons stay in exposure, not in lapses
     age_band   attained age bands for Med Supp only (other lines: ALL)      */
data _mm;
  set _mm;
  length duration $2 age_band $5;
  duration = ifc(dur_m < 12, '1', ifc(dur_m < 24, '2', '3+'));
  january  = (substr(month, 6, 2) = '01');
  jan_shock = (january = 1 and duration ne '1');           /* rate-increase month */
  lapsed   = (term_month = month and term_reason in ('LAPSE', 'DISENROLL'));
  att_age  = issue_age + dur_m / 12;
  if lob = 'MEDSUPP' then age_band = ifc(att_age < 70, '65-69', ifc(att_age < 75, '70-74', '75+'));
  else age_band = 'ALL';
run;

/* Collapse to rating cells: exposure (member-months), lapses, log exposure.
   A Poisson GLM on grouped cells with a log-exposure offset gives the same
   estimates as fitting member-month records, at a fraction of the size.      */
proc sql;
  create table npv.lapse_cells as
  select lob, duration, jan_shock, plan, age_band,
         count(*) as exposure, sum(lapsed) as lapses, log(count(*)) as log_exposure
  from _mm group by lob, duration, jan_shock, plan, age_band;

  /* Raw monthly rate annualized as 1 - (1 - q)**12; ties to gold_lapse_study */
  title "Raw lapse experience by duration (annualized)";
  select lob, duration, sum(exposure) as exposure_months, sum(lapses) as lapses,
         1 - (1 - sum(lapses)/sum(exposure))**12 as annual_lapse format=percent8.2
  from npv.lapse_cells group by lob, duration;
quit;

/* Poisson GLM, log link, offset = log(exposure): models log(lapses/exposure),
   so exp(beta) is a multiplicative relativity on the monthly lapse rate.
   PARAM=REF with REF= sets the base cell (year 1, plan F, age 65-69); TYPE3
   adds likelihood-ratio tests for each effect. ODS OUTPUT must precede the
   PROC to capture the table. If deviance/DF is well above 1, refit with
   SCALE=PEARSON so standard errors reflect overdispersion.                  */
ods output ParameterEstimates = npv.lapse_glm_params;
proc genmod data=npv.lapse_cells(where=(lob='MEDSUPP'));
  class duration(ref='1') plan(ref='F') age_band(ref='65-69') / param=ref;
  model lapses = duration jan_shock plan age_band
        / dist=poisson link=log offset=log_exposure type3;
  title "Med Supp lapse GLM (Poisson, log link, offset = log exposure)";
run;

/* Relativities and Wald 95% limits on the rate scale */
data npv.lapse_glm_params; set npv.lapse_glm_params;
  relativity = exp(estimate);
  rel_lower  = exp(lowerwaldcl);
  rel_upper  = exp(upperwaldcl);
run;
proc print data=npv.lapse_glm_params noobs;
  var parameter level1 estimate stderr relativity rel_lower rel_upper probchisq;
run;

/* ---- (b) Trend and forecast ----------------------------------------------*/
/* Monthly completed claims PMPM as a SAS date series by line. t_years is time
   in years from Jan-2022, so the regression slope is an annual rate. A month
   with zero completed claims gives a missing log and drops out of the fit.
   The latest months carry the most IBNR and the widest estimation error.    */
data _ts;
  set npv.gold_monthly;
  pmpm     = completed_claims / member_months;
  log_pmpm = log(pmpm);
  date     = mdy(input(substr(month,6,2),2.), 1, input(substr(month,1,4),4.));
  t_years  = (intck('month', '01JAN2022'd, date)) / 12;
  moy      = month(date);
  format date monyy7.;
run;
/* BY-group processing in GLM, ESM and ARIMA requires sorted input */
proc sort data=_ts; by lob date; run;

/* Log-linear trend: exp(slope) - 1 = annual trend */
/* CLASS moy adds month-of-year effects so seasonality does not bias the slope */
ods output ParameterEstimates = _trend_params;
proc glm data=_ts;
  by lob;
  class moy;
  model log_pmpm = t_years moy / solution;
  title "Log-linear trend with month-of-year seasonality";
run; quit;
data npv.trend_rates; set _trend_params(where=(parameter = 't_years'));
  annual_trend = exp(estimate) - 1;
  format annual_trend percent8.2;
  keep lob estimate stderr annual_trend;
run;
proc print data=npv.trend_rates noobs; run;

/* Holdout test: fit through Dec-2024, forecast 2025, compare (MAPE, bias) */
/* Multiplicative Winters: level and trend with multiplicative seasonality
   (series must be positive). OUT= holds actuals for the fit period and
   forecasts for the 12 LEAD months, so the 2025 rows are pure forecasts.
   Bias is signed: positive means the model over-forecast.                    */
proc esm data=_ts(where=(date < '01JAN2025'd)) out=_esm_hold lead=12 seasonality=12;
  by lob;
  id date interval=month;
  forecast pmpm / model=multwinters;
run;
proc sql;
  create table npv.esm_holdout as
  select f.lob, mean(abs(f.pmpm - a.pmpm) / a.pmpm) as mape format=percent8.2,
         mean((f.pmpm - a.pmpm) / a.pmpm)            as bias format=percent8.2
  from _esm_hold f inner join _ts a on a.lob = f.lob and a.date = f.date
  where f.date >= '01JAN2025'd
  group by f.lob;
quit;
title "Holt-Winters holdout accuracy (2025)"; proc print data=npv.esm_holdout noobs; run;

/* Final 36-month forecasts */
/* Refit on the full history. OUTFOR= carries ACTUAL, PREDICT, STD and the
   LOWER/UPPER limits for each date.                                          */
proc esm data=_ts outfor=npv.esm_forecast lead=36 seasonality=12;
  by lob;
  id date interval=month;
  forecast pmpm / model=multwinters;
run;

/* Seasonal ARIMA on log PMPM: first and seasonal (lag 12) differences, AR(1),
   MA factors at lags 1 and 12 (an airline-type model plus AR(1)), estimated
   by maximum likelihood. OUT= gives FORECAST and STD on the log scale.       */
proc arima data=_ts;
  by lob;
  identify var=log_pmpm(1,12) noprint;
  estimate p=1 q=(1)(12) method=ml noprint;
  forecast lead=36 interval=month id=date out=npv.arima_forecast noprint;
run; quit;

/* Back-transform from logs. exp(forecast) alone is the median, which sits
   below the mean of a lognormal; adding std**2/2 gives the mean forecast.
   Interval limits are quantiles, which carry through exp() unchanged, so no
   correction applies there. 1.2816 = 90th percentile of N(0,1): an 80% band. */
data npv.arima_forecast; set npv.arima_forecast;
  pmpm_forecast = exp(forecast + std**2 / 2);          /* back-transform with bias correction */
  pmpm_l80 = exp(forecast - 1.2816 * std);
  pmpm_u80 = exp(forecast + 1.2816 * std);
run;
title;

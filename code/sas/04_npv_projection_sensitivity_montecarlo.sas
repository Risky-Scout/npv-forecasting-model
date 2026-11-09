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
  04_npv_projection_sensitivity_montecarlo.sas   (version 2: monthly)
  Medicare Supplement cohort NPV in a DATA step - the same monthly mechanics
  as python/04_npv_engine.py:
    * premium collected monthly in advance (discounted from the start of the month)
    * lapse effective at the end of the paid month; deaths mid-month
    * claims and maintenance mid-month on exposure
    * shock lapse in month 1 of each renewal year (rate increase on the
      anniversary), sized so each year's total lapse equals base + shock
    * acquisition paid at issue (not discounted)
  Expected result at base assumptions: NPV = 924,382 per 1,000 policies,
  matching the Python engine and the Excel workbook.
  Includes (a) the projection, (b) a sensitivity loop, (c) a 10,000-scenario
  Monte Carlo. Assumptions mirror python/npv_assumptions.json.
  NOTE: written to standard Base SAS syntax but not executed (no SAS license
  where it was written). Check the log on the first run.
  Databricks equivalent: medsupp_npv() and the lapse x trend grid in section 6
  of npv_pipeline_databricks.py (same base assumptions and mechanics).

  STATUS: NOT YET EXECUTED. On the first run, review the full log, then
  confirm the base NPV in (a) ties to the expected figure above before using
  (b) or (c); a mismatch means a mechanics or assumption difference.
============================================================================*/
%include "&root/code/sas/00_setup.sas";

/* ---- Base assumptions (keep in sync with npv_assumptions.json) -----------*/
/* Amounts are annual per policy and rates are annual. sel1-sel3 = claim
   selection factors and lap1-lap3 = base lapse rates for policy years 1, 2,
   3+. shock = additional lapse in each renewal year from the rate increase.
   q65 and mort_g: mortality q(x) = q65 * exp(mort_g * (x - 65)), cap 35%.   */
%let years=20;     %let cohort=1000;   %let issue_age=65;
%let prem1=1980;   %let prem_age=0.03; %let rate_inc=0.07;
%let clm1=1536;    %let clm_age=0.04;  %let trend=0.06;
%let sel1=0.78;    %let sel2=0.90;     %let sel3=1.00;
%let lap1=0.14;    %let lap2=0.11;     %let lap3=0.085;  %let shock=0.035;
%let q65=0.0085;   %let mort_g=0.085;
%let fy_comm=0.22; %let ren_comm=0.10; %let ptax=0.02;
%let acq=250;      %let maint=95;      %let infl=0.03;   %let disc=0.08;

/*--------------------------------------------------------------------------
  %medsupp(out=, lapse_mult=1, trend_add=0, rate_add=0, disc_rate=&disc,
           clm_mult=1): writes the month-by-month projection to &out.
  The NPV is the sum of pv_cash over all months minus acquisition at issue.
  Parameters (defaults reproduce the base):
    lapse_mult  multiplies base lapse rates (the anniversary shock is not scaled)
    trend_add   added to the annual claim trend
    rate_add    added to the annual premium rate increase
    disc_rate   annual discount rate
    clm_mult    multiplies the starting claim cost, and so every later year
  The step has no SET statement: it executes once, and the DO loop writes one
  observation per projection month with explicit OUTPUT. Arrays are
  _TEMPORARY_ so they are not written to the output data set.
--------------------------------------------------------------------------*/
%macro medsupp(out=, lapse_mult=1, trend_add=0, rate_add=0, disc_rate=&disc, clm_mult=1);
data &out;
  array sel[3] _temporary_ (&sel1 &sel2 &sel3);
  array lap[3] _temporary_ (&lap1 &lap2 &lap3);
  n = &cohort;
  acquisition_t0 = &cohort * &acq;
  /* k = projection month 0 to 239; year = policy year; month = month in year */
  do k = 0 to 12*&years - 1;
    year  = int(k / 12) + 1;
    month = mod(k, 12) + 1;
    d     = min(year, 3);
    age   = &issue_age + year - 1;
    /* annual decrements converted to monthly as 1 - (1 - q)**(1/12) */
    q_death_m = 1 - (1 - min(&q65 * exp(&mort_g * (age - 65)), 0.35))**(1/12);
    base   = min(lap[d] * &lapse_mult, 1);
    q_year = min(base + (year >= 2) * &shock, 1);
    base_m = 1 - (1 - base)**(1/12);
    /* Month 1 rate chosen so that it and 11 base months compound to q_year
       for the policy year; in year 1 q_year = base, so month 1 = base_m.     */
    if month = 1 then q_lapse_m = 1 - (1 - q_year) / (1 - base_m)**11;   /* anniversary month */
    else q_lapse_m = base_m;
    /* premium: issue-age aging plus the anniversary rate increase;
       claims: aging, trend and selection by policy year                     */
    prem_pp = &prem1 * (1 + &prem_age)**(year-1) * (1 + &rate_inc + &rate_add)**(year-1);
    clm_pp  = &clm1 * &clm_mult * (1 + &clm_age)**(year-1) * (1 + &trend + &trend_add)**(year-1) * sel[d];
    comm_rate = ifn(year = 1, &fy_comm, &ren_comm);
    maint_pp  = &maint * (1 + &infl)**(year-1);

    /* Decrements: deaths first, then lapses from the survivors */
    in_force_start = n;
    deaths   = n * q_death_m;
    lapses   = (n - deaths) * q_lapse_m;
    exposure = n - 0.5 * deaths;               /* lapsers covered to month end */
    premium  = n * prem_pp / 12;               /* collected in advance */
    claims   = exposure * clm_pp / 12;
    commission  = premium * comm_rate;
    premium_tax = premium * &ptax;
    maintenance = exposure * maint_pp / 12;
    /* Discount factors: start of month for premium-related items, mid-month
       for claims and maintenance                                             */
    v_prem = (1 + &disc_rate)**(-k / 12);
    v_mid  = (1 + &disc_rate)**(-(k + 0.5) / 12);
    pv_cash = (premium - commission - premium_tax) * v_prem - (claims + maintenance) * v_mid;
    output;
    n = n - deaths - lapses;
  end;
  keep k year month in_force_start deaths lapses exposure premium claims commission
       premium_tax maintenance v_prem v_mid pv_cash acquisition_t0;
run;
%mend medsupp;

/* ---- (a) Deterministic projection ----------------------------------------*/
%medsupp(out=npv.medsupp_monthly);

/* acquisition_t0 is constant on every row, so MAX() returns it once.
   PV loss ratio = PV claims / PV premium; NPV margin = NPV / PV premium.      */
proc sql;
  title "Med Supp NPV per 1,000 policies - base assumptions (expected 924,382)";
  select sum(pv_cash) - max(acquisition_t0)                          as npv          format=comma14.0,
         sum(premium * v_prem)                                       as pv_premium   format=comma14.0,
         sum(claims * v_mid) / sum(premium * v_prem)                 as pv_loss_ratio format=percent8.2,
         (sum(pv_cash) - max(acquisition_t0)) / sum(premium * v_prem) as npv_margin  format=percent8.2
  from npv.medsupp_monthly;

  /* annual view, same as the workbook's MedSupp_NPV sheet */
  /* (year = 1) is 1 or 0, so acquisition is charged in policy year 1 only */
  create table npv.medsupp_annual as
  select year,
         sum(premium) as premium format=comma14.0, sum(claims) as claims format=comma14.0,
         sum(commission + premium_tax + maintenance) + (year = 1) * max(acquisition_t0) as expenses format=comma14.0,
         sum(premium - claims - commission - premium_tax - maintenance) - (year = 1) * max(acquisition_t0) as profit format=comma14.0,
         sum(pv_cash) - (year = 1) * max(acquisition_t0) as pv_profit format=comma14.0
  from npv.medsupp_monthly group by year order by year;
quit;
proc print data=npv.medsupp_annual noobs; run;

/* ---- (b) Sensitivities ----------------------------------------------------*/
/* One assumption moved at a time from the base. %sens runs %medsupp, keeps
   the NPV and appends it to WORK.SENS (deleted first so reruns start clean).
   side=Low / High is the first / second value in the label, not the direction
   of the NPV change.                                                         */
proc datasets lib=work nolist; delete sens; quit;
%macro sens(label=, side=, lapse_mult=1, trend_add=0, rate_add=0, disc_rate=&disc, clm_mult=1);
  %medsupp(out=_s, lapse_mult=&lapse_mult, trend_add=&trend_add, rate_add=&rate_add,
           disc_rate=&disc_rate, clm_mult=&clm_mult);
  proc sql; create table _r as select "&label" as driver length=40, "&side" as side length=8,
    sum(pv_cash) - max(acquisition_t0) as npv from _s; quit;
  proc append base=sens data=_r force; run;
%mend sens;

%sens(label=Base,                       side=Base);
%sens(label=Lapse x0.8 / x1.2,          side=Low,  lapse_mult=0.8);
%sens(label=Lapse x0.8 / x1.2,          side=High, lapse_mult=1.2);
%sens(label=Claim trend -1% / +1%,      side=Low,  trend_add=-0.01);
%sens(label=Claim trend -1% / +1%,      side=High, trend_add=0.01);
%sens(label=Rate increase +1% / -1%,    side=Low,  rate_add=0.01);
%sens(label=Rate increase +1% / -1%,    side=High, rate_add=-0.01);
%sens(label=Discount rate -2% / +2%,    side=Low,  disc_rate=0.06);
%sens(label=Discount rate -2% / +2%,    side=High, disc_rate=0.10);
%sens(label=Yr-1 claim cost -5% / +5%,  side=Low,  clm_mult=0.95);
%sens(label=Yr-1 claim cost -5% / +5%,  side=High, clm_mult=1.05);

/* One row per driver with Low and High columns (NOTSORTED keeps run order);
   swing = |Low - High|, sorted descending for a tornado chart.              */
proc transpose data=sens(where=(side ne 'Base')) out=npv.sensitivity(drop=_name_);
  by driver notsorted; id side; var npv;
run;
data npv.sensitivity; set npv.sensitivity; swing = abs(low - high);
  format low high swing comma14.0; run;
proc sort data=npv.sensitivity; by descending swing; run;
title "Med Supp NPV sensitivity (one at a time)"; proc print data=npv.sensitivity noobs; run;

/* ---- (c) Monte Carlo (same shock distributions as the Python engine) ------*/
/* The projection is written inline rather than calling %medsupp 10,000 times
   (one DATA step instead of 10,000). One output row per scenario.
   Shocks are drawn once per scenario and held for the full 20 years, so they
   represent assumption (parameter) risk rather than year-to-year noise:
     lapse_mult   lognormal with mean 1 (sigma 15%) on base lapse rates
     trend_shock  normal, sd 1.0%, added to annual claim trend
     rate_short   normal, sd 0.8%, subtracted from the annual rate increase
                  (approved increases falling short of the filed amount)
   Simplifications vs %medsupp: mortality uses year - 1 in place of age - 65,
   which assumes issue age 65 (the base), and there is no clm_mult.
   STREAMINIT fixes the seed so the run is reproducible.                     */
data npv.medsupp_mc(keep=sim lapse_mult trend_shock rate_short npv);
  call streaminit(2026);
  array sel[3] _temporary_ (&sel1 &sel2 &sel3);
  array lap[3] _temporary_ (&lap1 &lap2 &lap3);
  do sim = 1 to 10000;
    lapse_mult  = exp(rand('normal', -0.5 * 0.15**2, 0.15));   /* mean-one lognormal */
    trend_shock = rand('normal', 0, 0.010);
    rate_short  = rand('normal', 0, 0.008);
    n = &cohort;
    npv = -&cohort * &acq;
    do k = 0 to 12*&years - 1;
      year = int(k / 12) + 1;  month = mod(k, 12) + 1;  d = min(year, 3);
      qd = 1 - (1 - min(&q65 * exp(&mort_g * (year - 1)), 0.35))**(1/12);
      base = min(lap[d] * lapse_mult, 1);
      qy   = min(base + (year >= 2) * &shock, 1);
      bm   = 1 - (1 - base)**(1/12);
      ql   = ifn(month = 1, 1 - (1 - qy) / (1 - bm)**11, bm);
      pp = &prem1 * (1 + &prem_age)**(year-1) * (1 + &rate_inc - rate_short)**(year-1);
      cp = &clm1 * (1 + &clm_age)**(year-1) * (1 + &trend + trend_shock)**(year-1) * sel[d];
      dth = n * qd;  lps = (n - dth) * ql;  ex = n - 0.5 * dth;
      pr  = n * pp / 12;
      /* Sum statement: adds the month PV to npv (reset above for each scenario) */
      npv + pr * (1 - ifn(year = 1, &fy_comm, &ren_comm) - &ptax) * (1 + &disc)**(-k / 12)
          - (ex * cp / 12 + ex * &maint * (1 + &infl)**(year-1) / 12) * (1 + &disc)**(-(k + 0.5) / 12);
      n = n - dth - lps;
    end;
    output;
  end;
run;
/* Note: SAS and NumPy use different random-number generators, so individual
   scenarios differ from Python; the distribution (P5/P50/P95, mean) should agree
   closely: mean ~ 924K, P(NPV<0) ~ 11%. */

/* Distribution summary: mean, P5, P50, P95, and the probability of a loss.
   mean(npv < 0) averages a 0/1 indicator, i.e. the share of negative NPVs.   */
proc univariate data=npv.medsupp_mc noprint;
  var npv;
  output out=npv.mc_stats mean=mean pctlpts=5 50 95 pctlpre=P;
run;
proc sql;
  title "Monte Carlo NPV distribution (10,000 scenarios)";
  select s.*, (select mean(npv < 0) from npv.medsupp_mc) as prob_npv_negative format=percent8.1
  from npv.mc_stats s;
quit;
/* Histogram with a reference line at break-even (NPV = 0) */
proc sgplot data=npv.medsupp_mc;
  histogram npv; refline 0 / axis=x lineattrs=(color=red);
  title "Med Supp NPV per 1,000 policies - Monte Carlo";
run;
title;

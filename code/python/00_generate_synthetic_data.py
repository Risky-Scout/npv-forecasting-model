"""
00_generate_synthetic_data.py
-------------------------------------------------------------------------------
Builds a realistic, fully SYNTHETIC practice data set for three lines of
business so every script in this kit runs end to end:

    MEDSUPP        Medicare Supplement (individual, underwritten)
    DENTAL_VISION  Individual dental & vision
    MEDICAID       Medicaid managed care (capitation by rate cell)

Tables written to ../../data/ (CSV):
    members.csv        one row per member/policy (enrollment + termination)
    member_months.csv  one row per member per month of coverage (eligibility)
    claims.csv         one row per claim (incurred month, paid month, amount)

The "true" assumptions used to simulate the data are stored in
true_assumptions.json so the experience studies and forecasts in the other
scripts can be checked for whether they recover them. Recovering known
parameters from simulated data is the basic validation test for any
estimation method (ASOP 56, model testing).

Deliberate data problems are planted so the data-quality checks have
something to find, as they would in real data (ASOP 23):
    * duplicated member-month rows
    * claims for members with no eligibility that month
    * a missing month of Medicaid eligibility for one state (feed drop)
    * claim reversals (negative paid amounts) and one extreme outlier claim

Simplifications relative to a production block (deliberate, to keep the
mechanics visible): one claim-cost curve per line, no gender or area rating,
no plan-specific trend leveraging (e.g., Part B deductible on Plan N / HDG),
no Plan F eligibility restriction for post-2019 Medicare entrants, and a
single payment-lag pattern shared by all lines.

Nothing here is Humana data. All numbers are illustrative.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import json
from pathlib import Path
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import DATA_DIR, OUTPUT_DIR, data_file  # noqa: E402,F401  (relocatable data/output, see kitpaths.py)

import numpy as np
import pandas as pd

# Fixed seed: the data set, and therefore every downstream result, is exactly
# reproducible run to run. Any change in output is then attributable to a code
# or assumption change, not to simulation noise - the same discipline used
# when versioning a production projection.
RNG = np.random.default_rng(20261028)
OUT = DATA_DIR
OUT.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------------
# Experience window and extract date.
# Four calendar years of exposure; claims are observed only if paid by the
# extract date, so the most recent incurred months are incomplete. The gap
# between WINDOW_END and VALUATION_PAID_THROUGH (two months of runout) is what
# the completion-factor / IBNR step in the SQL kit has to fill.
# ----------------------------------------------------------------------------
WINDOW_START = pd.Period("2022-01", "M")
WINDOW_END = pd.Period("2025-12", "M")
VALUATION_PAID_THROUGH = pd.Period("2026-02", "M")   # claims paid through Feb 2026
MONTHS = pd.period_range(WINDOW_START, WINDOW_END, freq="M")

# ----------------------------------------------------------------------------
# True simulation assumptions (illustrative, not calibrated to any company).
#   MEDSUPP: attained-age premium and claim curves (slopes are per year of
#     age); selection factors by policy year 1/2/3+ reflect underwriting
#     wear-off; lapse by policy year plus a January shock when the annual
#     rate increase lands.
#   DENTAL_VISION: flat premium; first-year claims loaded for pent-up demand
#     (deferred dental work on new enrollees); high early-duration lapse.
#   MEDICAID: capitation by rate cell priced to a target loss ratio; a single
#     monthly disenrollment rate (redetermination churn is layered on below).
#   claim_payment_lag_probs: share of incurred dollars paid at lag 0..8
#     months; this is the "true" development pattern the chain ladder in the
#     SQL kit should approximately recover.
# ----------------------------------------------------------------------------
TRUE = {
    "MEDSUPP": {
        "base_premium_pm_age65": 165.0, "premium_age_slope": 0.030, "annual_rate_increase": 0.07,
        "base_claim_pmpm_age65": 128.0, "claim_age_slope": 0.040, "annual_claim_trend": 0.060,
        "selection_factor_by_duration": [0.78, 0.90, 1.00],
        "annual_lapse_by_duration": [0.14, 0.11, 0.085],
        "shock_lapse_add_january": 0.035,
        "claim_frequency_pm": 0.62,
    },
    "DENTAL_VISION": {
        "premium_pm": 39.0, "annual_rate_increase": 0.03,
        "base_claim_pmpm": 29.0, "annual_claim_trend": 0.035,
        "first_year_pent_up_factor": 1.28,
        "annual_lapse_by_duration": [0.36, 0.22, 0.17],
        "claim_frequency_pm": 0.18,
    },
    "MEDICAID": {
        "capitation_pmpm": {"CHILD": 262.0, "ADULT": 431.0, "ABD": 1385.0},
        "annual_rate_update": 0.035,
        "target_loss_ratio": 0.875, "annual_claim_trend": 0.055,
        "monthly_disenrollment": 0.024,
        "claim_frequency_pm": {"CHILD": 0.45, "ADULT": 0.55, "ABD": 0.95},
    },
    "claim_payment_lag_probs": [0.33, 0.36, 0.15, 0.08, 0.04, 0.02, 0.01, 0.005, 0.005],
}


def months_between(a: pd.Period, b: pd.Period) -> int:
    """Whole months from period a to period b (policy duration in months when a = issue month)."""
    return (b.year - a.year) * 12 + (b.month - a.month)


def annual_to_monthly(q: float) -> float:
    """Convert an annual decrement rate to a constant monthly rate.

    Uses 1 - (1 - q)^(1/12), i.e., the monthly rate whose 12-month survival
    compounds back to exactly 1 - q. Simply dividing q by 12 overstates the
    annual decrement once compounded (survivors are a shrinking base), and the
    error grows with q - material for dental lapse rates in the 20-36% range.
    Assumes a constant force of decrement within the year.
    """
    return 1.0 - (1.0 - q) ** (1.0 / 12.0)


def mortality_annual(age: np.ndarray) -> np.ndarray:
    """Simple Gompertz-style mortality curve for ages 65+ (illustrative).

    Roughly 0.85% at 65, rising about 8.9% per year of age, capped at 35%.
    Stands in for a select-and-ultimate insured-lives table; production work
    would use a published table (e.g., an SOA basic table) with company
    experience adjustments and mortality improvement.
    """
    return np.clip(0.0085 * np.exp(0.085 * (age - 65)), 0.0, 0.35)


# ----------------------------------------------------------------------------
# 1. Members and month-by-month eligibility
#
# Exposure is built as one row per member per covered month (member-months).
# Member-months are the natural exposure unit for health business: premium,
# capitation and claims are all monthly, and PMPM = dollars / member-months
# normalizes for a block whose size changes every month. A member is counted
# as exposed for the full month in which they terminate (decrement applied at
# month end), which is the convention the lapse study in 03 assumes.
# Members issued before WINDOW_START are simulated from issue so their
# duration is correct, but only in-window months are written (left truncation).
# ----------------------------------------------------------------------------
def simulate_medsupp(n: int, start_id: int) -> tuple[pd.DataFrame, list[dict]]:
    """Simulate an underwritten Med Supp block: attained-age premium, duration lapse, death."""
    t = TRUE["MEDSUPP"]
    issue = pd.period_range("2018-01", "2025-12", freq="M")
    w = np.linspace(0.6, 1.4, len(issue)); w /= w.sum()          # growing new sales
    issue_m = RNG.choice(issue, size=n, p=w)
    # Issue ages concentrated at 65 (open-enrollment turning-65 sales), remainder spread to 79.
    issue_age = RNG.choice(np.arange(65, 80), size=n, p=np.r_[0.42, np.full(14, 0.58 / 14)])
    states = RNG.choice(["FL", "TX", "GA", "KY", "LA", "OH"], size=n)
    plans = RNG.choice(["G", "N", "F", "HDG"], size=n, p=[0.58, 0.22, 0.12, 0.08])
    # Benefit relativities to Plan G; HDG = high-deductible G.
    plan_factor = {"G": 1.00, "N": 0.80, "F": 1.12, "HDG": 0.35}
    lapse_m = [annual_to_monthly(q) for q in t["annual_lapse_by_duration"]]

    members, mm = [], []
    for i in range(n):
        mid = start_id + i
        im, age0, pl = issue_m[i], issue_age[i], plans[i]
        term_m, reason = None, None
        cur = im
        while cur <= WINDOW_END:
            dur_m = months_between(im, cur)
            dur_y = dur_m // 12
            att_age = age0 + dur_m / 12
            if cur >= WINDOW_START:
                # Rate increases are filed annually and take effect each January,
                # so premium steps by calendar year rather than trending monthly.
                yrs_since_2022 = (cur.year - 2022)
                prem = (t["base_premium_pm_age65"] * (1 + t["premium_age_slope"]) ** (att_age - 65)
                        * (1 + t["annual_rate_increase"]) ** yrs_since_2022 * plan_factor[pl])
                mm.append((mid, "MEDSUPP", str(cur), states[i], pl, None, round(prem, 2)))
            # decrements applied at month end
            q_lapse = lapse_m[min(dur_y, 2)]
            if cur.month == 1 and dur_m >= 12:
                # one-time extra lapse in the month the annual rate increase hits
                q_lapse += t["shock_lapse_add_january"]
            q_death = annual_to_monthly(float(mortality_annual(np.array([att_age]))[0]))
            # One uniform draw tests death then lapse, so the two are mutually
            # exclusive in a month and each occurs with its stated probability.
            u = RNG.random()
            if u < q_death:
                term_m, reason = cur, "DEATH"; break
            if u < q_death + q_lapse:
                term_m, reason = cur, "LAPSE"; break
            cur = cur + 1
        members.append(dict(member_id=mid, lob="MEDSUPP", state=states[i], plan=pl, rate_cell=None,
                            issue_month=str(im), issue_age=int(age0),
                            term_month=str(term_m) if term_m is not None else None, term_reason=reason))
    return pd.DataFrame(members), mm


def simulate_dv(n: int, start_id: int):
    """Simulate an individual dental & vision block: flat premium, steep early-duration lapse.

    Mortality is ignored (immaterial at these ages relative to 17-36% lapse).
    Issue age is recorded for completeness but does not drive claims or lapse.
    """
    t = TRUE["DENTAL_VISION"]
    issue = pd.period_range("2020-01", "2025-12", freq="M")
    issue_m = RNG.choice(issue, size=n)
    states = RNG.choice(["FL", "TX", "GA", "KY", "LA", "OH"], size=n)
    plans = RNG.choice(["DENTAL", "DENTAL_VISION", "VISION"], size=n, p=[0.45, 0.40, 0.15])
    plan_factor = {"DENTAL": 1.0, "DENTAL_VISION": 1.25, "VISION": 0.30}
    lapse_m = [annual_to_monthly(q) for q in t["annual_lapse_by_duration"]]
    members, mm = [], []
    for i in range(n):
        mid = start_id + i
        im, pl = issue_m[i], plans[i]
        term_m, reason = None, None
        cur = im
        while cur <= WINDOW_END:
            dur_y = months_between(im, cur) // 12
            if cur >= WINDOW_START:
                prem = t["premium_pm"] * plan_factor[pl] * (1 + t["annual_rate_increase"]) ** (cur.year - 2022)
                mm.append((mid, "DENTAL_VISION", str(cur), states[i], pl, None, round(prem, 2)))
            if RNG.random() < lapse_m[min(dur_y, 2)]:
                term_m, reason = cur, "LAPSE"; break
            cur = cur + 1
        members.append(dict(member_id=mid, lob="DENTAL_VISION", state=states[i], plan=pl, rate_cell=None,
                            issue_month=str(im), issue_age=int(RNG.integers(25, 75)),
                            term_month=str(term_m) if term_m is not None else None, term_reason=reason))
    return pd.DataFrame(members), mm


def simulate_medicaid(n: int, start_id: int):
    """Simulate Medicaid managed-care enrollment: capitation by rate cell, eligibility churn.

    "Issue month" here is the enrollment month with the MCO. Revenue is the
    state capitation rate for the member's rate cell (CHILD / ADULT / ABD =
    aged, blind and disabled), updated annually like a state rate year.
    Termination is disenrollment (loss of eligibility or plan change), not a
    voluntary lapse decision, which is why it is modeled as a flat monthly rate.
    """
    t = TRUE["MEDICAID"]
    issue = pd.period_range("2019-01", "2025-12", freq="M")
    issue_m = RNG.choice(issue, size=n)
    states = RNG.choice(["FL", "KY", "LA", "OH", "SC"], size=n, p=[0.40, 0.18, 0.15, 0.15, 0.12])
    cells = RNG.choice(["CHILD", "ADULT", "ABD"], size=n, p=[0.52, 0.36, 0.12])
    q = t["monthly_disenrollment"]
    members, mm = [], []
    for i in range(n):
        mid = start_id + i
        im, cell = issue_m[i], cells[i]
        term_m, reason = None, None
        cur = im
        while cur <= WINDOW_END:
            if cur >= WINDOW_START:
                cap = t["capitation_pmpm"][cell] * (1 + t["annual_rate_update"]) ** (cur.year - 2022)
                mm.append((mid, "MEDICAID", str(cur), states[i], "MCO", cell, round(cap, 2)))
            # redetermination waves raise churn in 2023-2024 (post-PHE unwinding)
            # The 1.8x multiplier is deliberately NOT stored in TRUE: the lapse
            # study in 03 should detect it as a one-off inside the experience.
            q_eff = q * (1.8 if pd.Period("2023-04", "M") <= cur <= pd.Period("2024-06", "M") else 1.0)
            if RNG.random() < q_eff:
                term_m, reason = cur, "DISENROLL"; break
            cur = cur + 1
        members.append(dict(member_id=mid, lob="MEDICAID", state=states[i], plan="MCO", rate_cell=cell,
                            issue_month=str(im), issue_age=int(RNG.integers(0, 64)),
                            term_month=str(term_m) if term_m is not None else None, term_reason=reason))
    return pd.DataFrame(members), mm


# Distinct member_id ranges by line make the source line identifiable from the
# key alone and leave room (9,000,000+) for the orphan-claim test below.
print("Simulating members and eligibility ...")
ms_mem, ms_mm = simulate_medsupp(9000, 1_000_000)
dv_mem, dv_mm = simulate_dv(7000, 2_000_000)
md_mem, md_mm = simulate_medicaid(9000, 3_000_000)

members = pd.concat([ms_mem, dv_mem, md_mem], ignore_index=True)
mm = pd.DataFrame(ms_mm + dv_mm + md_mm,
                  columns=["member_id", "lob", "month", "state", "plan", "rate_cell", "premium"])

# ----------------------------------------------------------------------------
# 2. Claims (one row per claim) with payment lags
#
# Claims are generated off the member-month they belong to, so each claim is
# assigned to its INCURRED month (date of service), the month whose exposure
# and premium it should be matched against. Paid month = incurred month + a
# random payment lag. Analysing on an incurred basis is what makes the PMPM a
# measure of cost per month of coverage; a paid basis would mix in changes in
# claim-processing speed and distort trend.
#
# Expected PMPM by line:
#   MEDSUPP       age-65 cost x age curve x trend x selection x plan factor
#   DENTAL_VISION base cost x trend x pent-up load (policy year 1) x plan
#   MEDICAID      capitation x target loss ratio x trend
# Claim trend accrues continuously by fractional year (unit cost and
# utilization drift monthly), unlike premium, which steps each January.
# ----------------------------------------------------------------------------
print("Simulating claims ...")
mem_info = members.set_index("member_id")[["issue_month", "issue_age"]]
mmx = mm.join(mem_info, on="member_id")
per = pd.PeriodIndex(mmx["month"], freq="M")
iss = pd.PeriodIndex(mmx["issue_month"], freq="M")
dur_m = (per.year - iss.year) * 12 + (per.month - iss.month)
dur_y = np.minimum(dur_m // 12, 2)
yrs = per.year - 2022 + (per.month - 1) / 12

expected = np.zeros(len(mmx))
freq = np.zeros(len(mmx))
lob = mmx["lob"].to_numpy()

m = lob == "MEDSUPP"
t = TRUE["MEDSUPP"]
att_age = mmx["issue_age"].to_numpy() + dur_m / 12
plan_f = mmx["plan"].map({"G": 1.00, "N": 0.80, "F": 1.12, "HDG": 0.35}).to_numpy()
sel = np.array(t["selection_factor_by_duration"])[dur_y]
expected[m] = (t["base_claim_pmpm_age65"] * (1 + t["claim_age_slope"]) ** (att_age[m] - 65)
               * (1 + t["annual_claim_trend"]) ** yrs[m] * sel[m] * plan_f[m])
freq[m] = t["claim_frequency_pm"]

m = lob == "DENTAL_VISION"
t = TRUE["DENTAL_VISION"]
plan_f = mmx["plan"].map({"DENTAL": 1.0, "DENTAL_VISION": 1.25, "VISION": 0.30}).to_numpy()
pent = np.where(dur_y == 0, t["first_year_pent_up_factor"], 1.0)
expected[m] = t["base_claim_pmpm"] * (1 + t["annual_claim_trend"]) ** yrs[m] * pent[m] * plan_f[m]
freq[m] = t["claim_frequency_pm"]

m = lob == "MEDICAID"
t = TRUE["MEDICAID"]
cap0 = mmx["rate_cell"].map(t["capitation_pmpm"]).to_numpy(dtype=float)
expected[m] = cap0[m] * t["target_loss_ratio"] * (1 + t["annual_claim_trend"]) ** yrs[m]
freq[m] = mmx["rate_cell"].map(t["claim_frequency_pm"]).to_numpy(dtype=float)[m]

# number of claims per member-month ~ Poisson(freq); severity gamma so mean PMPM = expected
# Frequency-severity (compound Poisson-gamma) construction. Gamma shape 0.9
# gives a coefficient of variation just over 1: right-skewed severities without
# a heavy tail. The single large claim planted in section 3 supplies the tail
# event that a pooling / large-claim threshold would handle in practice.
n_claims = RNG.poisson(freq)
idx = np.repeat(np.arange(len(mmx)), n_claims)
sev_mean = expected[idx] / freq[idx]
paid = RNG.gamma(shape=0.9, scale=sev_mean / 0.9)
lag_p = np.array(TRUE["claim_payment_lag_probs"]); lag_p /= lag_p.sum()   # guard against rounding
lag = RNG.choice(np.arange(len(lag_p)), size=len(idx), p=lag_p)
inc = per[idx]
paid_month = inc + lag
claims = pd.DataFrame({
    "member_id": mmx["member_id"].to_numpy()[idx],
    "lob": lob[idx],
    "incurred_month": inc.astype(str),
    "paid_month": paid_month.astype(str),
    "paid_amount": np.round(paid, 2),
})
# Service category is a random label for grouping practice only; it does not
# affect amounts.
cats = {"MEDSUPP": ["PART_B_COINS", "PART_A_DEDUCT", "EXCESS", "SNF_COINS"],
        "DENTAL_VISION": ["PREVENTIVE", "BASIC", "MAJOR", "VISION"],
        "MEDICAID": ["IP", "OP", "PROF", "RX", "BH"]}
claims["service_category"] = [RNG.choice(cats[l]) for l in claims["lob"]]

# keep only claims paid by the extract date (later claims are not yet known = IBNR)
# This truncation is what produces a genuine run-off triangle: recent incurred
# months are understated on a paid basis and must be completed (chain ladder,
# or Bornhuetter-Ferguson for the least mature months) before trend fitting.
claims = claims[pd.PeriodIndex(claims["paid_month"], freq="M") <= VALUATION_PAID_THROUGH]

# ----------------------------------------------------------------------------
# 3. Plant realistic data problems
#
# Each defect mirrors a common production failure and has a matching check in
# the SQL kit (ASOP 23: review data for reasonableness and consistency, and
# disclose what was done about defects). Fixed random_state values keep the
# planted defects identical across runs so the checks have known answers.
# ----------------------------------------------------------------------------
print("Planting data-quality issues ...")
# Duplicate eligibility rows inflate exposure and depress PMPM if not removed.
dups = mm.sample(25, random_state=1)
mm = pd.concat([mm, dups], ignore_index=True)                       # duplicate member-months

# A missing month of eligibility for one state: the month's claims still
# arrive but now look like orphans, and exposure dips for one month. Detected
# by a month-over-month enrollment check; the fix is to reload the file.
drop = (mm["lob"] == "MEDICAID") & (mm["state"] == "KY") & (mm["month"] == "2025-07")
mm = mm[~drop]                                                      # feed drop: KY Medicaid Jul-2025

# Claims with no matching eligibility (e.g., ID mismatches between claim and
# enrollment systems). Must be reconciled, not silently dropped or kept.
orphans = claims.sample(40, random_state=2).copy()
orphans["member_id"] = RNG.integers(9_000_000, 9_000_999, size=40)  # member not on eligibility
claims = pd.concat([claims, orphans], ignore_index=True)

# Reversals post one month after the original payment; net paid is correct
# only when originals and reversals are summed together on an incurred basis.
rev = claims.sample(30, random_state=3).copy()
rev["paid_amount"] = -rev["paid_amount"]                            # reversals
rev["paid_month"] = (pd.PeriodIndex(rev["paid_month"], freq="M") + 1).astype(str)
rev = rev[pd.PeriodIndex(rev["paid_month"], freq="M") <= VALUATION_PAID_THROUGH]
claims = pd.concat([claims, rev], ignore_index=True)

# One catastrophic claim: tests large-claim detection and pooling. Left in the
# experience it distorts one month's PMPM and any trend fitted through it.
big = claims[claims["lob"] == "MEDICAID"].sample(1, random_state=4).copy()
big["paid_amount"] = 1_850_000.00                                   # extreme outlier
claims = pd.concat([claims, big], ignore_index=True)

# Shuffle so planted rows are not clustered at the end of the file, then
# assign claim_id after shuffling so IDs carry no information.
claims = claims.sample(frac=1.0, random_state=5).reset_index(drop=True)
claims.insert(0, "claim_id", np.arange(1, len(claims) + 1))

# ----------------------------------------------------------------------------
# 4. Write outputs. true_assumptions.json is the answer key for validating
#    the experience studies and forecasts; it would not exist in practice.
# ----------------------------------------------------------------------------
members.to_csv(OUT / "members.csv", index=False)
mm.to_csv(OUT / "member_months.csv", index=False)
claims.to_csv(OUT / "claims.csv", index=False)
with open(OUT / "true_assumptions.json", "w") as f:
    json.dump(TRUE, f, indent=2)

print(f"members:       {len(members):>10,}")
print(f"member_months: {len(mm):>10,}")
print(f"claims:        {len(claims):>10,}")
print(f"written to {OUT}")

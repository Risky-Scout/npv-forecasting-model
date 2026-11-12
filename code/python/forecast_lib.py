"""
forecast_lib.py  (v4: in-force financial forecast)
-------------------------------------------------------------------------------
Library behind the in-force forecast (09), the actual-vs-expected variance
waterfall (10) and the forecast-vintage accuracy study (11).

What it forecasts. The EXISTING experience book (the members, premium and
claims in the extracts), month by month on a calendar basis, 36 months ahead:
the forecast a Humana forecasting team produces for plan / outlook cycles.
This is different from the NPV engine (04), which prices NEW-business cohorts
and a new-state Medicaid contract. The two share the approved assumption file
(trend, lapse, shock lapse, mortality, payment patterns) for Med Supp and D&V.
Medicaid is the exception: the approved file holds the new-state CONTRACT
assumptions, so Medicaid churn, trend and rate update for the book are measured
from the book itself (config/forecast_config.json, medicaid_book).

Method, in the order the forecast is built
  1. Data cut. Any vintage can be rebuilt: eligibility to the as-of month E,
     claims paid to the paid-through month P (synthetic: P = E + 2). Members
     terminated after E are in force at E; claims incurred after E have no
     eligibility in the cut and are dropped, as the SQL kit drops orphans.
  2. Completion. The lag triangle and chain-ladder factors are rebuilt in
     Python exactly as SQL 03 builds them (volume-weighted age-to-age factors
     over the latest 18 incurred months, lags 8+ grouped, tail 1.00), so a
     vintage uses only what was paid at its P. Incurred months with 0..2
     months of run-out are completed by Bornhuetter-Ferguson:
         ultimate = paid to date + (1 - completion factor) x a-priori,
     a-priori = member months x PMPM from a log-linear trend fitted to the
     line's completed PMPM on months with at least 6 months of run-out (the
     latest 36). BF stops one or two large or small early payments being
     multiplied by a factor of 1.5-3; chain ladder is used where little is
     left to estimate. Segment months are completed with their line's
     ultimate / paid ratio for that incurred month (one payment pattern per line).
  3. Claims base. The latest 12 completed incurred months to E, normalized
     by a manual relativity per member month and trended to E:
         Med Supp  plan x (1 + age slope)^(attained age - 65) x select factor
                   (config/claim_cost_curves.json; Humana's morbidity study in production)
         D&V       plan premium relativity x pent-up factor in policy year 1
         Medicaid  rate-cell capitation relativity
     Each segment (state x plan, or state x rate cell) gets a limited-fluctuation
     credibility-weighted A/E to that manual, Z = min(1, sqrt(n / n_full)),
     n_full = 1082 x (1 + CV^2) claims, complement 1.0 (the manual), then an
     off-balance so the line's base-period claims are reproduced exactly.
     The plan / rate-cell complement at the premium relativity is a
     loss-ratio-neutral manual, which is how rate cells are priced.
  4. Members. Seriatim, on expected values: every member in force at E is a
     unit of weight 1; new sales are added each month as units copied from the
     trailing 12 months' new issues (mix of state, plan, rate cell, issue age
     and first premium), weight 1/12 each, unless Finance's sales plan is given.
     Decrements at month end, the member exposed the whole month (the
     experience convention): Med Supp base lapse by policy duration plus the
     annual shock lapse added in January for policies past their first year
     (the experience puts the shock in the January the rate increase takes
     effect, and the lapse study measures it there), and Gompertz mortality on
     continuous attained age, the two mutually exclusive; D&V lapse by duration;
     Medicaid churn measured on the book outside the redetermination window.
  5. Premium. Each unit's own premium at the reference month, times the rate
     action every January (Med Supp and D&V: the approved annual increase;
     Medicaid: the state's latest January step measured on continuing members),
     and for Med Supp the attained-age step by month.
  6. Claims. Unit x month expected pooled claims = base x segment relativity
     x manual relativity x (1 + trend)^((month - E)/12) x seasonality, plus the
     pooling charge (expected excess over the pooling point) for an all-in
     incurred figure. Paid = incurred on the line's payment pattern, and the
     IBNR at E runs off from P + 1 on the pattern's remaining lags.

Outputs: a tidy monthly frame by lob x state x plan x rate cell x duration
band x cohort (INFORCE / NEW), the completion table, the segment credibility
table and a diagnostics dict of every estimated parameter.
All data is the synthetic practice book; not Humana data.
-------------------------------------------------------------------------------
"""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitpaths import OUTPUT_DIR  # noqa: E402
import assumption_control as ac  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CFG_FILE = ROOT / "config" / "forecast_config.json"
LOBS = ["MEDSUPP", "DENTAL_VISION", "MEDICAID"]
SEG_KEYS = ["lob", "state", "plan", "rate_cell"]
OUT_KEYS = SEG_KEYS + ["duration_band", "cohort"]


def load_config() -> dict:
    return json.loads(CFG_FILE.read_text())


def _curves() -> dict:
    return json.loads((ROOT / "config" / "claim_cost_curves.json").read_text())["MEDSUPP"]


def pooling_credibility(excess: "pd.Series", pc_cfg: dict) -> tuple[float, float, int]:
    """(Z, n_full, n_large) for the pooling charge, by limited fluctuation (ASOP 25).

    The charge is an AGGREGATE (frequency x excess severity), so the full-credibility standard is the
    claim-count standard scaled by (1 + CV^2) of the excess severity: n_full = 1082 x (1 + CV^2), as for
    the segment claims base. CV is measured on the line's claims above the pooling point when there are
    at least two; otherwise 1.0 (judgment: an exponential-like tail). Z = min(1, sqrt(n_large / n_full)).
    """
    x = np.asarray(excess[excess > 0], dtype=float)
    n = int(len(x))
    cv = float(x.std(ddof=1) / x.mean()) if n >= 2 and x.mean() > 0 else float(pc_cfg.get("default_excess_cv", 1.0))
    n_full = float(pc_cfg.get("full_credibility_claims", 1082)) * (1 + cv ** 2)
    return min(1.0, (n / n_full) ** 0.5), n_full, n


def _pooling_points() -> dict:
    return json.loads((ROOT / "config" / "experience_parameters.json").read_text())["pooling_points"]


# ---------------------------------------------------------------- month helpers
def mi(s) -> np.ndarray | int:
    """'YYYY-MM' (scalar, Series or Period) -> integer month index year x 12 + month - 1."""
    if isinstance(s, pd.Period):
        return s.year * 12 + s.month - 1
    if isinstance(s, str):
        return int(s[:4]) * 12 + int(s[5:7]) - 1
    s = pd.Series(s).astype("string")
    return (s.str[:4].astype(int) * 12 + s.str[5:7].astype(int) - 1).to_numpy()


def ms(i: int) -> str:
    """Integer month index -> 'YYYY-MM'."""
    i = int(i)
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def a2m(q):
    """Annual decrement -> monthly with the same annual survival (the engine's convention)."""
    return 1.0 - (1.0 - np.asarray(q, dtype=float)) ** (1.0 / 12.0)


# ---------------------------------------------------------------- data
@dataclass
class Book:
    """Experience data cut at an eligibility month E and a claims paid-through month P."""
    members: pd.DataFrame
    mm: pd.DataFrame
    claims: pd.DataFrame
    elig_as_of: int
    paid_through: int
    notes: list = field(default_factory=list)


def load_book(db_path: Path | None = None) -> Book:
    """Read the SQL kit's silver tables (de-duplicated eligibility, reconciled and pooled claims)."""
    import duckdb
    db = Path(db_path or OUTPUT_DIR / "practice.duckdb")
    if not db.exists():
        raise SystemExit(f"{db} not found: run 01_run_sql_kit.py first")
    con = duckdb.connect(str(db), read_only=True)
    try:
        members = con.execute("SELECT member_id, lob, state, plan, rate_cell, issue_month, issue_age, term_month, term_reason FROM members").fetchdf()
        mm = con.execute("SELECT member_id, lob, month, state, plan, rate_cell, premium FROM silver_member_months").fetchdf()
        claims = con.execute("SELECT member_id, lob, incurred_month, paid_month, paid_amount, paid_capped, paid_excess FROM silver_claims").fetchdf()
    finally:
        con.close()
    for df in (members, mm):
        df["rate_cell"] = df["rate_cell"].fillna("").astype(str)
        df["plan"] = df["plan"].fillna("").astype(str)
    members["issue_mi"] = mi(members["issue_month"])
    members["term_mi"] = np.where(members["term_month"].isna(), -1,
                                  mi(members["term_month"].fillna("0000-01")))
    mm["mi"] = mi(mm["month"])
    claims["inc_mi"] = mi(claims["incurred_month"])
    claims["paid_mi"] = mi(claims["paid_month"])
    E = int(mm["mi"].max())
    P = int(claims["paid_mi"].max())
    return Book(members, mm, claims, E, P)


def cut_book(book: Book, elig_as_of: int, paid_through: int) -> Book:
    """The data as it stood at (E, P): eligibility to E, claims paid to P and incurred to E.

    A member terminated after E was in force at E, so the termination is unknown in the cut.
    Claims incurred after E have no eligibility in the cut and are dropped, the treatment the
    SQL kit gives orphans (silver_claims is an inner join to eligibility).
    """
    E, P = int(elig_as_of), int(paid_through)
    mem = book.members[book.members["issue_mi"] <= E].copy()
    later = mem["term_mi"] > E
    mem.loc[later, "term_mi"] = -1
    mem.loc[later, "term_reason"] = None
    mm = book.mm[book.mm["mi"] <= E]
    cl = book.claims[(book.claims["paid_mi"] <= P) & (book.claims["inc_mi"] <= E)]
    return Book(mem, mm, cl, E, P)


# ---------------------------------------------------------------- completion
def lag_triangle(claims: pd.DataFrame, paid_through: int, max_lag: int = 8) -> pd.DataFrame:
    """Cumulative pooled paid by line x incurred month x lag, through paid_through (SQL 03 layout)."""
    c = claims[claims["paid_mi"] <= paid_through]
    lag = np.minimum(c["paid_mi"].to_numpy() - c["inc_mi"].to_numpy(), max_lag)
    inc = (pd.DataFrame({"lob": c["lob"].to_numpy(), "inc_mi": c["inc_mi"].to_numpy(), "lag": lag,
                         "paid": c["paid_capped"].to_numpy()})
           .groupby(["lob", "inc_mi", "lag"], as_index=False)["paid"].sum())
    months = c[["lob", "inc_mi"]].drop_duplicates()
    rows = []
    for lob, im in months.itertuples(index=False):
        obs = paid_through - im
        for k in range(0, min(obs, max_lag) + 1):
            rows.append((lob, im, k, obs))
    grid = pd.DataFrame(rows, columns=["lob", "inc_mi", "lag", "observable_lag"])
    t = grid.merge(inc, on=["lob", "inc_mi", "lag"], how="left").fillna({"paid": 0.0})
    t = t.sort_values(["lob", "inc_mi", "lag"])
    t["cum_paid"] = t.groupby(["lob", "inc_mi"])["paid"].cumsum()
    return t.drop(columns="paid").reset_index(drop=True)


def cl_factors(tri: pd.DataFrame, window: int = 18, max_lag: int = 8) -> pd.DataFrame:
    """Volume-weighted age-to-age factors over the latest `window` incurred months with both lags.

    Completion factor at lag k = 1 / product of ATA factors from lag k to the last lag; lag
    max_lag (grouped 8+) is taken as complete (tail 1.00), as in SQL 03.
    """
    out = []
    for lob, g in tri.groupby("lob"):
        piv = g.pivot(index="inc_mi", columns="lag", values="cum_paid")
        ata = {}
        for k in range(max_lag):
            if k + 1 not in piv.columns:
                continue
            pair = piv[[k, k + 1]].dropna().sort_index(ascending=False).head(window)
            ata[k] = pair[k + 1].sum() / pair[k].sum() if pair[k].sum() else 1.0
        dev = 1.0
        cdf = {}
        for k in sorted(ata, reverse=True):
            dev *= ata[k]
            cdf[k] = dev
        for k in range(max_lag + 1):
            d = cdf.get(k, 1.0)
            out.append(dict(lob=lob, lag=k, ata_factor=ata.get(k, np.nan), dev_to_ult=d, completion_factor=1.0 / d))
    return pd.DataFrame(out)


def bf_ultimate(paid: np.ndarray, cf: np.ndarray, apriori: np.ndarray, observable_lag: np.ndarray, bf_max_lag: int) -> np.ndarray:
    """Bornhuetter-Ferguson for observable lag <= bf_max_lag, chain ladder (paid / CF) otherwise.

    BF ultimate = paid + (1 - CF) x a-priori: the unpaid share comes from the a-priori estimate,
    not from the few dollars paid so far. With a-priori = paid / CF it equals chain ladder.
    bf_max_lag = -1 gives pure chain ladder.
    """
    paid, cf, apriori, ol = (np.asarray(x, dtype=float) for x in (paid, cf, apriori, observable_lag))
    cl = np.divide(paid, cf, out=np.zeros_like(paid), where=cf > 0)
    bf = paid + (1.0 - cf) * apriori
    return np.where(ol <= bf_max_lag, bf, cl)


def _loglinear(t: np.ndarray, y: np.ndarray, w: np.ndarray | None = None):
    """Weighted least squares of log(y) on t: (intercept, slope per month, residual variance)."""
    X = np.column_stack([np.ones_like(t, dtype=float), t.astype(float)])
    w = np.ones_like(y, dtype=float) if w is None else np.asarray(w, dtype=float)
    W = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * W[:, None], np.log(y) * W, rcond=None)
    res = np.log(y) - X @ beta
    s2 = float((w * res ** 2).sum() / max(w.sum() * (len(y) - 2) / len(y), 1e-9))
    return float(beta[0]), float(beta[1]), s2


def completion_table(book: Book, cfg: dict) -> pd.DataFrame:
    """Line x incurred month: member months, paid to date, CL and BF ultimate (pooled), excess."""
    cc = cfg["completion"]
    P = book.paid_through
    tri = lag_triangle(book.claims, P, cc["max_lag"])
    cf = cl_factors(tri, cc["ata_window_months"], cc["max_lag"])
    diag = tri[tri["lag"] == np.minimum(tri["observable_lag"], cc["max_lag"])][["lob", "inc_mi", "observable_lag", "cum_paid"]]
    mmx = book.mm.groupby(["lob", "mi"]).size().rename("member_months").reset_index().rename(columns={"mi": "inc_mi"})
    ex = book.claims.groupby(["lob", "inc_mi"])["paid_excess"].sum().rename("excess_paid").reset_index()
    t = mmx.merge(diag, on=["lob", "inc_mi"], how="left").merge(ex, on=["lob", "inc_mi"], how="left")
    t["observable_lag"] = t["observable_lag"].fillna(P - t["inc_mi"]).astype(int)
    t["cum_paid"] = t["cum_paid"].fillna(0.0)
    t["excess_paid"] = t["excess_paid"].fillna(0.0)
    t["lag_used"] = np.minimum(t["observable_lag"], cc["max_lag"])
    t = t.merge(cf[["lob", "lag", "completion_factor"]].rename(columns={"lag": "lag_used"}), on=["lob", "lag_used"], how="left")
    t["completion_factor"] = t["completion_factor"].fillna(1.0)
    t["cl_ultimate"] = t["cum_paid"] / t["completion_factor"]
    t["apriori"] = np.nan
    for lob, g in t.groupby("lob"):
        idx = g.index
        mat = g[(g["observable_lag"] >= cc["apriori_min_observable_lag"]) &
                (g["inc_mi"] > book.elig_as_of - cc["apriori_window_months"]) & (g["cl_ultimate"] > 0)]
        if len(mat) >= 6:
            a, b, s2 = _loglinear(mat["inc_mi"].to_numpy() - book.elig_as_of, (mat["cl_ultimate"] / mat["member_months"]).to_numpy(),
                                  mat["member_months"].to_numpy())
            pmpm = np.exp(a + b * (g["inc_mi"].to_numpy() - book.elig_as_of) + s2 / 2)
        else:   # too little mature history: the a-priori falls back to chain ladder (BF = CL)
            pmpm = (g["cl_ultimate"] / g["member_months"]).to_numpy()
        t.loc[idx, "apriori"] = g["member_months"].to_numpy() * pmpm
    t["bf_ultimate"] = bf_ultimate(t["cum_paid"], t["completion_factor"], t["apriori"], t["observable_lag"], cc["bf_max_lag"])
    t["method"] = np.where(t["observable_lag"] <= cc["bf_max_lag"], "BF", "CL")
    t["ultimate"] = t["bf_ultimate"]
    t["ultimate_to_paid"] = np.divide(t["ultimate"], t["cum_paid"], out=np.ones(len(t)), where=t["cum_paid"] > 0)
    # v4.1: IBNR on large-claim EXCESS (over the pooling point). Bornhuetter-Ferguson with the line's
    # pooling charge as the a-priori: excess IBNR = (1 - CF) x pooling charge x a-priori pooled
    # ultimate, for months not yet fully developed. The pooled lag pattern stands in for the excess
    # pattern (large claims usually pay slower, so this understates excess IBNR somewhat; disclosed).
    # Pooling charge = excess / pooled on fully developed months.
    mature = t["observable_lag"] >= cc["max_lag"]
    pc = (t[mature].groupby("lob")["excess_paid"].sum() / t[mature].groupby("lob")["cum_paid"].sum()).fillna(0.0)
    # Same credibility treatment as the forecast's pooling charge (one large claim is not credible).
    pcc = cfg.get("pooling_charge", {})
    for lob_ in pc.index:
        z_, _, _ = pooling_credibility(book.claims.loc[book.claims["lob"] == lob_, "paid_excess"], pcc)
        pc[lob_] = z_ * pc[lob_] + (1 - z_) * float(pcc.get("complement", {}).get(lob_, 0.0))
    t["pooling_charge"] = t["lob"].map(pc).fillna(0.0)
    t["excess_ibnr"] = np.where(mature, 0.0, (1 - t["completion_factor"]) * t["pooling_charge"] * t["apriori"])
    t["month"] = t["inc_mi"].map(ms)
    return t.rename(columns={"cum_paid": "paid_to_date"}).sort_values(["lob", "inc_mi"]).reset_index(drop=True)


def segment_ultimate(book: Book, comp: pd.DataFrame) -> pd.DataFrame:
    """Member x incurred month pooled ultimate: member paid to date x the line's ultimate / paid."""
    c = book.claims.groupby(["member_id", "lob", "inc_mi"], as_index=False)[["paid_capped", "paid_excess"]].sum()
    c = c.merge(comp[["lob", "inc_mi", "ultimate_to_paid"]], on=["lob", "inc_mi"], how="left")
    c["ultimate"] = c["paid_capped"] * c["ultimate_to_paid"].fillna(1.0)
    return c


# ---------------------------------------------------------------- parameters
def _book_medicaid(book: Book, cfg: dict, comp: pd.DataFrame) -> dict:
    """Medicaid book churn (outside the redetermination window), wave multiplier, trend, rate update."""
    E = book.elig_as_of
    wave = cfg["medicaid_book"]["redetermination_wave"]
    w0, w1 = mi(wave["start"]), mi(wave["end"])
    mm = book.mm[book.mm["lob"] == "MEDICAID"]
    expo = mm.groupby("mi").size()
    mem = book.members[(book.members["lob"] == "MEDICAID") & (book.members["term_reason"] == "DISENROLL")]
    terms = mem.groupby("term_mi").size().reindex(expo.index, fill_value=0)
    in_w = (expo.index >= w0) & (expo.index <= w1)
    base = terms[~in_w].sum() / expo[~in_w].sum()
    n_w = int(in_w.sum())
    if n_w >= 3:
        mult, mult_src = float((terms[in_w].sum() / expo[in_w].sum()) / base), f"E: estimated from {n_w} observed window months"
    else:
        mult, mult_src = float(wave["default_churn_multiplier"]), "J: default (fewer than 3 window months observed)"
    # Book trend: log-linear fit of completed pooled PMPM on mature months (the step-02 method).
    g = comp[(comp["lob"] == "MEDICAID") & (comp["observable_lag"] >= cfg["completion"]["apriori_min_observable_lag"])]
    g = g[g["inc_mi"] > E - cfg["completion"]["apriori_window_months"]]
    _, b, _ = _loglinear(g["inc_mi"].to_numpy(), (g["ultimate"] / g["member_months"]).to_numpy(), g["member_months"].to_numpy())
    return dict(monthly_churn=float(base), annual_churn=float(1 - (1 - base) ** 12), wave_multiplier=mult,
                wave_multiplier_source=mult_src, wave=(w0, w1), trend=float(np.exp(12 * b) - 1))


def infer_rate_actions(book: Book, cfg: dict) -> dict:
    """Latest January premium step for members in force in both December and January, by line.

    Med Supp premium also moves with attained age every month, so its ratio is divided by one
    month of the approved age step before it is read as a rate action.
    """
    A = ac.load_assumptions()
    eff = cfg["rate_actions"]["effective_month"]
    out = {}
    mm = book.mm
    for lob in LOBS:
        g = mm[mm["lob"] == lob]
        jans = sorted(m for m in g["mi"].unique() if m % 12 == eff - 1 and (m - 1) in set(g["mi"]))
        if not jans:
            out[lob] = None
            continue
        j = jans[-1]
        a = g[g["mi"] == j - 1].set_index("member_id")["premium"]
        b = g[g["mi"] == j].set_index("member_id")["premium"]
        both = a.index.intersection(b.index)
        r = float((b[both] / a[both]).median())
        if lob == "MEDSUPP":
            r /= (1 + A["MEDSUPP"]["premium_age_step"]) ** (1 / 12)
        out[lob] = dict(january=ms(j), rate_action=r - 1, members=len(both))
    return out


def _seasonality(comp: pd.DataFrame, lob: str, E: int, cfg: dict) -> tuple[np.ndarray, float]:
    """Month-of-year factors (geometric mean 1) if month dummies are jointly significant, else 1s."""
    import statsmodels.api as sm
    s = cfg["seasonality"]
    g = comp[(comp["lob"] == lob) & (comp["observable_lag"] >= cfg["completion"]["apriori_min_observable_lag"]) & (comp["ultimate"] > 0)]
    if len(g) < 24:
        return np.ones(12), float("nan")
    y = np.log((g["ultimate"] / g["member_months"]).to_numpy())
    t = (g["inc_mi"].to_numpy() - E).astype(float)
    moy = g["inc_mi"].to_numpy() % 12
    D = np.column_stack([(moy == k).astype(float) for k in range(1, 12)])
    X0 = sm.add_constant(t)
    X1 = np.column_stack([X0, D])
    f0, f1 = sm.WLS(y, X0, weights=g["member_months"]).fit(), sm.WLS(y, X1, weights=g["member_months"]).fit()
    p = float(f1.compare_f_test(f0)[1])
    if p >= s["p_value"]:
        return np.ones(12), p
    eff = np.r_[0.0, f1.params[2:]]
    fac = np.exp(eff - eff.mean())
    return fac, p


def _book_lapse_indications() -> dict:
    f = OUTPUT_DIR / "assumptions" / "proposed_npv_assumptions.json"
    if not f.exists():
        return {}
    P = json.loads(f.read_text())
    return {l: {k: P[l][k] for k in ("lapse_by_duration", "shock_lapse_add_from_year2") if k in P[l]} for l in ("MEDSUPP", "DENTAL_VISION")}


def line_params(lob: str, book: Book, cfg: dict, comp: pd.DataFrame, overrides: dict | None = None) -> dict:
    """Every parameter the projection of one line uses, with its source."""
    A = ac.load_assumptions()
    a = A[lob]
    curves = _curves()
    p: dict = dict(lob=lob)
    # v4.1: persistency of the EXISTING book comes from its own credibility-weighted lapse experience
    # (03b's indications, output/assumptions/proposed_npv_assumptions.json), as Medicaid churn already
    # does. The approved file holds the NEW-BUSINESS pricing basis (public sources), which describes a
    # different population. Falls back to the approved values if 03b has not run.
    ex = _book_lapse_indications()
    lapse_src = "book experience (03b credibility-weighted)" if lob in ex else "approved pricing assumption (no 03b indication)"
    book_lapse = ex.get(lob, {}).get("lapse_by_duration", a.get("lapse_by_duration"))
    book_shock = ex.get(lob, {}).get("shock_lapse_add_from_year2", a.get("shock_lapse_add_from_year2"))
    if lob == "MEDSUPP":
        p.update(trend=a["claim_trend"], trend_source="approved assumption (pure cost trend)",
                 lapse=list(book_lapse), shock=book_shock, lapse_source=lapse_src,
                 q65=a["mortality_q65"], mort_growth=a["mortality_growth"],
                 # The in-force forecast projects the EXISTING book, so it uses that book's own morbidity
                 # curves (config/claim_cost_curves.json: age slope, selection, plan relativities incl.
                 # closed Plan F), the same curves that normalize its experience. New-business pricing
                 # (04) uses the public basis in npv_assumptions.json. Both files are covered by the
                 # approval hash (assumption_control.GOVERNED_CONFIGS), so neither can change unapproved.
                 age_slope=curves["claim_age_slope"], selection=list(curves["selection_factor_by_duration"]),
                 plan_rel=dict(curves["plan_relativity"]), premium_age_step=a["premium_age_step"],
                 rate_action=a["annual_rate_increase"], rate_action_source="approved annual_rate_increase")
    elif lob == "DENTAL_VISION":
        # v4.3: the existing book's rate action is read off its own January premium step when the config
        # says "inferred" (as Medicaid). The approved annual_rate_increase prices NEW business; the in-force
        # forecast and the A/E vintages project the book's own filing history.
        inf = infer_rate_actions(book, cfg).get("DENTAL_VISION") if cfg["rate_actions"]["DENTAL_VISION"]["source"] == "inferred" else None
        p.update(trend=a["claim_trend"], trend_source="approved assumption", lapse=list(book_lapse), lapse_source=lapse_src,
                 pent_up=a["first_year_pent_up_factor"],
                 rate_action=(inf["rate_action"] if inf else a["annual_rate_increase"]),
                 rate_action_source=(f"inferred from the {inf['january']} premium step" if inf else "approved annual_rate_increase"),
                 premium_age_step=0.0)
    else:
        mb = _book_medicaid(book, cfg, comp)
        inf = infer_rate_actions(book, cfg).get("MEDICAID")
        p.update(trend=mb["trend"], trend_source="book fit on mature months (not the new-state contract)",
                 monthly_churn=mb["monthly_churn"], annual_churn=mb["annual_churn"], wave=mb["wave"],
                 wave_multiplier=mb["wave_multiplier"], wave_multiplier_source=mb["wave_multiplier_source"],
                 rate_action=(inf["rate_action"] if inf else a["annual_rate_update"]),
                 rate_action_source=(f"inferred from the {inf['january']} capitation step" if inf else "approved (no January observed)"),
                 premium_age_step=0.0)
    sea, pval = _seasonality(comp, lob, book.elig_as_of, cfg) if cfg["seasonality"]["method"] != "none" else (np.ones(12), float("nan"))
    p.update(seasonality=sea, seasonality_p=pval, pattern=list(a["claim_payment_pattern"]))
    for k, v in (overrides or {}).get(lob, {}).items():
        p[k] = v
    return p


# ---------------------------------------------------------------- base and credibility
def _manual(lob: str, p: dict, plan, cell, issue_age, dur_m, prem_rel) -> np.ndarray:
    """Manual claim relativity per member month (the complement of credibility)."""
    band = np.minimum(np.asarray(dur_m) // 12, 2)
    if lob == "MEDSUPP":
        pr = pd.Series(plan).map(p["plan_rel"]).fillna(1.0).to_numpy()
        att = np.asarray(issue_age, dtype=float) + np.asarray(dur_m) / 12.0
        return pr * (1 + p["age_slope"]) ** (att - 65) * np.asarray(p["selection"])[band]
    if lob == "DENTAL_VISION":
        return np.asarray(prem_rel) * np.where(band == 0, p["pent_up"], 1.0)
    return np.asarray(prem_rel)


def _premium_relativity(mm: pd.DataFrame, lob: str) -> dict:
    """Plan (D&V) or rate cell (Medicaid) premium PMPM relative to the line, base period."""
    key = "plan" if lob == "DENTAL_VISION" else "rate_cell"
    g = mm.groupby(key)["premium"].mean()
    return (g / mm["premium"].mean()).to_dict(), key


def claims_base(lob: str, book: Book, cfg: dict, comp: pd.DataFrame, p: dict) -> tuple[float, pd.DataFrame, dict]:
    """(line base PMPM at E on the manual, segment credibility table, premium relativities)."""
    E = book.elig_as_of
    nb = cfg["base_period_months"]["value"]
    mm = book.mm[(book.mm["lob"] == lob) & (book.mm["mi"] > E - nb)].merge(
        book.members[["member_id", "issue_mi", "issue_age"]], on="member_id", how="left")
    prel, key = _premium_relativity(mm, lob) if lob != "MEDSUPP" else ({}, None)
    rel = mm[key].map(prel).to_numpy() if key else np.ones(len(mm))
    dur = mm["mi"].to_numpy() - mm["issue_mi"].to_numpy()
    man = _manual(lob, p, mm["plan"].to_numpy(), mm["rate_cell"].to_numpy(), mm["issue_age"].to_numpy(), dur, rel)
    tf = (1 + p["trend"]) ** ((mm["mi"].to_numpy() - E) / 12.0) * p["seasonality"][mm["mi"].to_numpy() % 12]
    mm = mm.assign(manual=man * tf)
    su = segment_ultimate(book, comp)
    su = su[(su["lob"] == lob) & (su["inc_mi"] > E - nb)]
    cl = book.claims[(book.claims["lob"] == lob) & (book.claims["inc_mi"] > E - nb) & (book.claims["paid_amount"] > 0)]
    sev = cl["paid_capped"]
    cv = float(sev.std() / sev.mean()) if len(sev) > 1 else 1.0
    n_full = cfg["credibility"]["full_credibility_claims_frequency_standard"] * (1 + cv ** 2)
    seg = ["state", "plan", "rate_cell"]
    keyed = mm[["member_id", "mi"] + seg].rename(columns={"mi": "inc_mi"})
    su = su.merge(keyed, on=["member_id", "inc_mi"], how="left")
    act = su.groupby(seg)["ultimate"].sum()
    cnt = cl.merge(keyed, on=["member_id", "inc_mi"], how="left").groupby(seg).size()
    expw = mm.groupby(seg)["manual"].sum()
    base = float(act.sum() / expw.sum())
    t = pd.DataFrame({"manual_exposure": expw}).join(act.rename("actual")).join(cnt.rename("claims")).fillna(0.0)
    t["expected"] = base * t["manual_exposure"]
    t["ae"] = t["actual"] / t["expected"]
    t["z"] = np.minimum(1.0, np.sqrt(t["claims"] / n_full))
    t["rel_raw"] = t["z"] * t["ae"] + (1 - t["z"])
    off = t["actual"].sum() / (t["expected"] * t["rel_raw"]).sum()
    t["relativity"] = t["rel_raw"] * off
    t["off_balance"] = off
    t["n_full"] = n_full
    t["severity_cv"] = cv
    t = t.reset_index()
    t.insert(0, "lob", lob)
    return base, t, prel


# ---------------------------------------------------------------- units
def _units(lob: str, book: Book, cfg: dict, H: int, overrides: dict | None) -> pd.DataFrame:
    """In-force members at E (weight 1) and new-sales units for each forecast month."""
    E = book.elig_as_of
    mem = book.members[book.members["lob"] == lob]
    last = book.mm[(book.mm["lob"] == lob) & (book.mm["mi"] == E)][["member_id", "state", "plan", "rate_cell", "premium"]]
    inf = last.merge(mem[["member_id", "issue_mi", "issue_age", "term_mi"]], on="member_id", how="left")
    inf = inf[inf["term_mi"] != E]                      # terminated at the end of E: not in force at E + 1
    u_if = pd.DataFrame(dict(state=inf["state"], plan=inf["plan"], rate_cell=inf["rate_cell"],
                             issue_mi=inf["issue_mi"].astype(int), issue_age=inf["issue_age"].astype(float),
                             prem0=inf["premium"], ref_mi=E, age_ref_mi=E, start_mi=E + 1, weight=1.0, cohort="INFORCE"))
    # New sales: templates from the trailing 12 months' new issues with their first-month premium.
    tm = mem[(mem["issue_mi"] > E - 12) & (mem["issue_mi"] <= E)][["member_id", "issue_mi", "issue_age"]]
    first = book.mm[book.mm["lob"] == lob].merge(tm, on="member_id")
    first = first[first["mi"] == first["issue_mi"]]
    n_month = len(tm) / 12.0
    # v4.1: growth-adjusted default. A flat trailing average lags a growing book (the v4 vintage test
    # showed Med Supp member months -0.9% biased). Growth = issues in the last 12 months / the 12
    # before, capped; future months grow from the trailing window's midpoint (E - 5.5).
    growth = 0.0
    if cfg["new_sales"].get("method", "trailing_12_month_average") == "trailing_12_month_trend":
        prev12 = int(((mem["issue_mi"] > E - 24) & (mem["issue_mi"] <= E - 12)).sum())
        if prev12 > 0 and len(tm) > 0:
            cap_g = float(cfg["new_sales"].get("growth_cap", 0.3))
            growth = float(np.clip(len(tm) / prev12 - 1.0, -cap_g, cap_g))
    plan = cfg["new_sales"].get("plan_by_line_per_month", {}).get(lob)
    if plan is not None:
        n_month = float(plan); growth = 0.0            # a Finance plan is used as given
    ov = (overrides or {}).get(lob, {})
    if "new_sales_per_month" in ov:
        n_month = float(ov["new_sales_per_month"]); growth = 0.0
    frames = [u_if]
    if len(first) and n_month > 0:
        w = n_month / len(first)
        for f in range(E + 1, E + H + 1):
            wf = w * (1 + growth) ** ((f - (E - 5.5)) / 12.0)
            frames.append(pd.DataFrame(dict(state=first["state"].to_numpy(), plan=first["plan"].to_numpy(), rate_cell=first["rate_cell"].to_numpy(),
                                            issue_mi=f, issue_age=first["issue_age"].to_numpy(dtype=float), prem0=first["premium"].to_numpy(),
                                            ref_mi=first["issue_mi"].to_numpy(), age_ref_mi=f, start_mi=f, weight=wf, cohort="NEW")))
    u = pd.concat(frames, ignore_index=True)
    u["lob"] = lob
    return u, n_month, len(first)


# ---------------------------------------------------------------- projection
@dataclass
class ForecastResult:
    frame: pd.DataFrame
    completion: pd.DataFrame
    credibility: pd.DataFrame
    diagnostics: dict
    ibnr_at_as_of: pd.DataFrame


def _januaries(ref: np.ndarray, m: int, eff_month: int) -> np.ndarray:
    """Number of rate-effective months in (ref, m] for each unit (vectorized)."""
    e = eff_month - 1
    def count_le(x):  # number of months n <= x with n % 12 == e
        return np.floor((x - e) / 12.0) + 1
    return np.maximum(count_le(m) - count_le(ref), 0)


def forecast(book: Book, horizon: int | None = None, cfg: dict | None = None, overrides: dict | None = None,
             lobs: list | None = None) -> ForecastResult:
    """In-force forecast from the cut book's E (eligibility) and P (paid-through)."""
    cfg = cfg or load_config()
    H = horizon or cfg["horizon_months"]
    E, P = book.elig_as_of, book.paid_through
    comp = completion_table(book, cfg)
    pool, pool_diag = {}, {}
    pc_cfg = cfg.get("pooling_charge", {})
    for lob in LOBS:
        c = book.claims[book.claims["lob"] == lob]
        expv = float(c["paid_excess"].sum() / c["paid_capped"].sum()) if c["paid_capped"].sum() else 0.0
        # v4.1: limited-fluctuation credibility on the number of claims above the pooling point
        # (Z = min(1, sqrt(n / n_full))), complemented by a judgment charge. One large claim in four
        # years (the synthetic Medicaid book) is not credible: v4 applied its 2.5% charge in full and the
        # vintage test found all-in incurred biased +2.8%.
        # The standard is scaled by (1 + CV^2) of excess severity, because the charge is an aggregate.
        z, n_full, n_large = pooling_credibility(c["paid_excess"], pc_cfg)
        comp_v = float(pc_cfg.get("complement", {}).get(lob, 0.0))
        pool[lob] = z * expv + (1 - z) * comp_v
        pool_diag[lob] = dict(experience=expv, large_claims=n_large, n_full=n_full, z=z, complement=comp_v, used=pool[lob])
    eff = cfg["rate_actions"]["effective_month"]
    frames, creds, diag, ibnr_rows = [], [], {"as_of": ms(E), "paid_through": ms(P), "lines": {}}, []
    for lob in (lobs or LOBS):
        p = line_params(lob, book, cfg, comp, overrides)
        base, cred, prel = claims_base(lob, book, cfg, comp, p)
        creds.append(cred)
        u, n_month, n_tmpl = _units(lob, book, cfg, H, overrides)
        relmap = cred.set_index(["state", "plan", "rate_cell"])["relativity"]
        seg_rel = relmap.reindex(pd.MultiIndex.from_frame(u[["state", "plan", "rate_cell"]])).fillna(1.0).to_numpy()
        prel_key = "plan" if lob == "DENTAL_VISION" else ("rate_cell" if lob == "MEDICAID" else None)
        prem_rel = u[prel_key].map(prel).fillna(1.0).to_numpy() if prel_key else np.ones(len(u))
        pc = p.get("pooling_charge", pool[lob])
        months = np.arange(E + 1, E + H + 1)
        is_new = (u["cohort"] == "NEW").to_numpy()
        n = np.where(is_new, 0.0, u["weight"].to_numpy())   # members in force at E enter month E + 1 in force
        recs = []
        for m in months:
            new = np.where(is_new & (u["start_mi"].to_numpy() == m), u["weight"].to_numpy(), 0.0)
            active = u["start_mi"].to_numpy() <= m
            expo = n + new                                   # whole-month exposure, joiners included
            dur = m - u["issue_mi"].to_numpy()
            band = np.minimum(np.maximum(dur, 0) // 12, 2)
            if lob == "MEDSUPP":
                att = u["issue_age"].to_numpy() + dur / 12.0
                q_l = a2m(np.minimum(np.asarray(p["lapse"])[band], 1.0)) + np.where((m % 12 == 0) & (dur >= 12), p["shock"], 0.0)
                q_d = a2m(np.minimum(p["q65"] * np.exp(p["mort_growth"] * (att - 65)), 0.35))
            elif lob == "DENTAL_VISION":
                q_l = a2m(np.minimum(np.asarray(p["lapse"])[band], 0.95)); q_d = np.zeros(len(u))
            else:
                mult = p["wave_multiplier"] if p["wave"][0] <= m <= p["wave"][1] else 1.0
                q_l = np.full(len(u), p["monthly_churn"] * mult); q_d = np.zeros(len(u))
            q_l, q_d = np.minimum(q_l, 1.0), np.minimum(q_d, 1.0 - np.minimum(q_l, 1.0))
            deaths, lapses = expo * q_d, expo * q_l
            prem = (u["prem0"].to_numpy() * (1 + p["premium_age_step"]) ** ((m - u["age_ref_mi"].to_numpy()) / 12.0)
                    * (1 + p["rate_action"]) ** _januaries(u["ref_mi"].to_numpy(), m, eff))
            man = _manual(lob, p, u["plan"].to_numpy(), u["rate_cell"].to_numpy(), u["issue_age"].to_numpy(), np.maximum(dur, 0), prem_rel)
            pmpm = base * seg_rel * man * (1 + p["trend"]) ** ((m - E) / 12.0) * p["seasonality"][m % 12]
            recs.append(pd.DataFrame(dict(month_mi=m, state=u["state"], plan=u["plan"], rate_cell=u["rate_cell"],
                                          duration_band=band + 1, cohort=u["cohort"], members_start=n * active, new_members=new,
                                          deaths=deaths, lapses=lapses, member_months=expo, premium=expo * prem,
                                          incurred_pooled=expo * pmpm)))
            n = expo - deaths - lapses
        f = pd.concat(recs, ignore_index=True)
        f = f.groupby(["month_mi", "state", "plan", "rate_cell", "duration_band", "cohort"], as_index=False).sum(numeric_only=True)
        f["lob"] = lob
        f["pooling_charge"] = f["incurred_pooled"] * pc
        f["incurred"] = f["incurred_pooled"] + f["pooling_charge"]
        f["members_end"] = f["members_start"] + f["new_members"] - f["deaths"] - f["lapses"]
        # Paid: incurred on the payment pattern, by segment, within the horizon.
        pat = np.asarray(p["pattern"], dtype=float)
        f = f.sort_values(["state", "plan", "rate_cell", "duration_band", "cohort", "month_mi"])
        f["paid"] = 0.0
        # Paid is aggregated at segment level without duration band (bands move over time).
        g2 = f.groupby(["state", "plan", "rate_cell", "cohort", "month_mi"], as_index=False)["incurred"].sum()
        paid_rows = []
        for keys, g in g2.groupby(["state", "plan", "rate_cell", "cohort"]):
            inc = pd.Series(g["incurred"].to_numpy(), index=g["month_mi"].to_numpy()).reindex(months, fill_value=0.0).to_numpy()
            pd_ = np.convolve(inc, pat)[:H]
            paid_rows.append(pd.DataFrame(dict(state=keys[0], plan=keys[1], rate_cell=keys[2], cohort=keys[3], month_mi=months, paid=pd_)))
        paid = pd.concat(paid_rows, ignore_index=True)
        # IBNR at E (incurred <= E, unpaid at P), run off from P + 1 on the remaining pattern lags.
        cl_ = comp[comp["lob"] == lob]
        su = segment_ultimate(book, comp)
        su = su[su["lob"] == lob].merge(book.mm[book.mm["lob"] == lob][["member_id", "mi", "state", "plan", "rate_cell"]].rename(columns={"mi": "inc_mi"}),
                                        on=["member_id", "inc_mi"], how="left")
        su["ibnr"] = su["ultimate"] - su["paid_capped"]
        ib = su.groupby(["state", "plan", "rate_cell", "inc_mi"], as_index=False)["ibnr"].sum()
        run_rows = []
        for r in ib.itertuples(index=False):
            ol = P - r.inc_mi
            w = pat[ol + 1:] if ol + 1 < len(pat) else np.array([])
            if r.ibnr == 0:
                continue
            if w.sum() <= 0:
                w = np.array([1.0])
            w = w / w.sum()
            for j, wj in enumerate(w):
                run_rows.append((r.state, r.plan, r.rate_cell, P + 1 + j, r.ibnr * wj))
        run = (pd.DataFrame(run_rows, columns=["state", "plan", "rate_cell", "month_mi", "ibnr_runoff"])
               .groupby(["state", "plan", "rate_cell", "month_mi"], as_index=False).sum()) if run_rows else pd.DataFrame(columns=["state", "plan", "rate_cell", "month_mi", "ibnr_runoff"])
        ibnr_rows.append(dict(lob=lob, ibnr_pooled=float(su["ibnr"].sum()),
                              ibnr_line_check=float((cl_["ultimate"] - cl_["paid_to_date"]).sum())))
        # Attach paid and runoff to the duration-band-1 row of the in-force cohort... instead, keep them on
        # their own rows (duration_band 0 = not applicable) so no amount is allocated by judgment.
        paid["duration_band"] = 0
        run["duration_band"] = 0; run["cohort"] = "INFORCE"
        f = pd.concat([f, paid, run], ignore_index=True).fillna(0.0)
        f["lob"] = lob
        f = f.groupby(["lob", "month_mi", "state", "plan", "rate_cell", "duration_band", "cohort"], as_index=False).sum(numeric_only=True)
        frames.append(f)
        diag["lines"][lob] = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in p.items()
                              if k not in ("plan_rel",)} | dict(base_pmpm_on_manual=base, pooling_charge=pc,
                                                                 new_sales_per_month=n_month, new_sales_templates=n_tmpl)
    fr = pd.concat(frames, ignore_index=True)
    fr = fr[fr["month_mi"] <= E + H]
    fr.insert(0, "as_of", ms(E)); fr.insert(1, "paid_through", ms(P))
    fr["month"] = fr["month_mi"].map(ms)
    cols = ["as_of", "paid_through", "month", "month_mi", "lob", "state", "plan", "rate_cell", "duration_band", "cohort",
            "members_start", "new_members", "deaths", "lapses", "members_end", "member_months", "premium",
            "incurred_pooled", "pooling_charge", "incurred", "paid", "ibnr_runoff"]
    for c in cols:
        if c not in fr:
            fr[c] = 0.0
    fr = fr[cols].sort_values(["lob", "month_mi", "state", "plan", "rate_cell", "duration_band", "cohort"]).reset_index(drop=True)
    return ForecastResult(fr, comp, pd.concat(creds, ignore_index=True), diag, pd.DataFrame(ibnr_rows))


# ---------------------------------------------------------------- actuals on current data
def actuals(book: Book, cfg: dict | None = None, vintage_E: int | None = None) -> pd.DataFrame:
    """Actual experience by line x month x segment x duration band x cohort on current data.

    Pooled claims are completed with the current completion table (BF / CL); excess over the
    pooling point is paid to date plus a BF IBNR at the line level (v4.1; completion_table), allocated
    to segments by member months. cohort = NEW if issued after vintage_E.
    """
    cfg = cfg or load_config()
    comp = completion_table(book, cfg)
    su = segment_ultimate(book, comp)
    mm = book.mm.merge(book.members[["member_id", "issue_mi"]], on="member_id", how="left")
    dur = mm["mi"] - mm["issue_mi"]
    mm["duration_band"] = np.minimum(np.maximum(dur, 0) // 12, 2) + 1
    mm["cohort"] = np.where(mm["issue_mi"] > (vintage_E if vintage_E is not None else 10 ** 9), "NEW", "INFORCE")
    x = mm.merge(su[["member_id", "inc_mi", "ultimate", "paid_excess"]].rename(columns={"inc_mi": "mi"}), on=["member_id", "mi"], how="left").fillna({"ultimate": 0.0, "paid_excess": 0.0})
    g = x.groupby(["lob", "mi", "state", "plan", "rate_cell", "duration_band", "cohort"], as_index=False).agg(
        member_months=("member_id", "size"), premium=("premium", "sum"), incurred_pooled=("ultimate", "sum"), excess=("paid_excess", "sum"))
    # Allocate the line-month excess IBNR to segments by member months (IBNR is not known by member).
    lm = g.groupby(["lob", "mi"])["member_months"].transform("sum")
    ib = comp.set_index(["lob", "inc_mi"])["excess_ibnr"]
    g["excess_ibnr"] = [ib.get((l, m_), 0.0) for l, m_ in zip(g["lob"], g["mi"])] * (g["member_months"] / lm)
    g["excess"] = g["excess"] + g["excess_ibnr"]
    g["incurred"] = g["incurred_pooled"] + g["excess"]
    g["observable_lag"] = book.paid_through - g["mi"]
    g["month"] = g["mi"].map(ms)
    return g.rename(columns={"mi": "month_mi"})


# ---------------------------------------------------------------- vintage registry
def register_vintage(registry_dir: Path, run_id: str, frame: pd.DataFrame, meta: dict) -> Path:
    """Store a forecast vintage, keyed on its as-of month (one vintage per as-of).

    v4 review fix: the key was run_id, which is a fixed value in run_config.json for a scheduled
    job, so a reused run_id overwrote an earlier month's vintage and the same as-of under two
    run_ids was stored twice. Now:
      * re-running the same as-of replaces that vintage (idempotent rerun of a cycle);
      * a run_id already registered for a DIFFERENT as-of is refused (set a new run_id per cycle),
        so history can never be overwritten silently.
    """
    registry_dir = Path(registry_dir)
    registry_dir.mkdir(parents=True, exist_ok=True)
    as_of = str(meta["as_of"])
    idx_f = registry_dir / "registry.csv"
    idx = pd.read_csv(idx_f, dtype=str) if idx_f.exists() else pd.DataFrame(columns=["run_id", "as_of", "paid_through", "file", "registered_utc"])
    clash = idx[(idx["run_id"] == run_id) & (idx["as_of"] != as_of)]
    if len(clash):
        raise SystemExit(f"vintage registry: run_id {run_id!r} is already registered for as-of {clash['as_of'].iloc[0]}; "
                         f"this cycle is as-of {as_of}. Set a new run_id in config/run_config.json for each cycle.")
    for old_f in idx.loc[idx["as_of"] == as_of, "file"]:
        (registry_dir / old_f).unlink(missing_ok=True)
    idx = idx[idx["as_of"] != as_of]
    f = registry_dir / f"vintage_{as_of}.csv.gz"
    frame.to_csv(f, index=False)
    row = dict(run_id=run_id, file=f.name, **{k: str(v) for k, v in meta.items()})
    idx = pd.concat([idx, pd.DataFrame([row])], ignore_index=True).sort_values("as_of")
    idx.to_csv(idx_f, index=False)
    return f


def read_registry(registry_dir: Path) -> pd.DataFrame:
    f = Path(registry_dir) / "registry.csv"
    return pd.read_csv(f, dtype=str) if f.exists() else pd.DataFrame(columns=["run_id", "as_of", "paid_through", "file"])


def vintage_dates(cfg: dict, as_of: str) -> tuple[int, int]:
    E = mi(as_of)
    return E, E + int(cfg["eligibility_lag_months"]["value"])


def deepcopy_cfg(cfg: dict) -> dict:
    return copy.deepcopy(cfg)

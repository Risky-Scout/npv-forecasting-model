"""
Tests for the v4 in-force forecast (forecast_lib.py, 09, 10, 11). Each pins down a property a
reviewer would otherwise take on trust:

  * Completion: Bornhuetter-Ferguson equals chain ladder when the a-priori equals paid / CF,
    and bf_max_lag = -1 is pure chain ladder; the Python triangle reproduces the SQL kit's
    completion factors, and a vintage cut at today's dates reproduces today's factors.
  * Roll-forward: members at the start of each month = last month's start + new - decrements.
  * Neutrality: with zero trend, decrements, rate actions, aging and new sales, members and
    PMPM stay flat.
  * The A/E waterfall components sum exactly to the total variance.
  * The vintage registry keeps one vintage per as-of month and refuses a reused run id.

Most tests run on a small generated book, so they need no pipeline output. The two that compare
with the SQL kit read output/practice.duckdb and are skipped if it has not been built.
"""
import copy
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "code" / "python"
sys.path.insert(0, str(PY))
import forecast_lib as fl  # noqa: E402
from kitpaths import OUTPUT_DIR  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


ae = _load("ae_v4", PY / "10_actual_vs_expected.py")
CFG = fl.load_config()
DB = OUTPUT_DIR / "practice.duckdb"
needs_db = pytest.mark.skipif(not DB.exists(), reason="output/practice.duckdb not built (run the SQL kit first)")


# ---------------------------------------------------------------- a small generated book
def make_book(seed: int = 0) -> fl.Book:
    """Three lines, 2021-2025 issues, eligibility 2023-01..2025-12, claims paid to 2026-02."""
    rng = np.random.default_rng(seed)
    E, P = fl.mi("2025-12"), fl.mi("2026-02")
    start = fl.mi("2023-01")
    pat = np.array([0.33, 0.36, 0.15, 0.08, 0.04, 0.02, 0.01, 0.005, 0.005])
    spec = {"MEDSUPP": (["G", "N"], [""], 180.0, 0.010, "LAPSE"),
            "DENTAL_VISION": (["DENTAL", "VISION"], [""], 40.0, 0.020, "LAPSE"),
            "MEDICAID": (["MCO"], ["CHILD", "ADULT", "ABD"], 450.0, 0.022, "DISENROLL")}
    mem, mm, cl = [], [], []
    mid = 1
    for lob, (plans, cells, prem, q, reason) in spec.items():
        for _ in range(400):
            im = int(rng.integers(fl.mi("2021-01"), E + 1))
            plan, cell = rng.choice(plans), rng.choice(cells)
            pr = prem * (0.8 if plan in ("N", "VISION") else 1.0) * (2.0 if cell == "ABD" else 1.0)
            term = -1
            for m in range(im, E + 1):
                if m >= start:
                    mm.append((mid, lob, fl.ms(m), "GA", plan, cell, pr, m))
                    for _c in range(rng.poisson(0.5)):
                        amt = rng.gamma(0.9, (pr * 0.8 / 0.5) / 0.9)
                        lag = int(rng.choice(9, p=pat / pat.sum()))
                        if m + lag <= P:
                            cl.append((mid, lob, fl.ms(m), fl.ms(m + lag), amt, amt, 0.0, m, m + lag))
                if rng.random() < q:
                    term = m
                    break
            mem.append(dict(member_id=mid, lob=lob, state="GA", plan=plan, rate_cell=cell, issue_month=fl.ms(im),
                            issue_age=int(rng.integers(65, 75)) if lob == "MEDSUPP" else 40,
                            term_month=fl.ms(term) if term >= 0 else None, term_reason=reason if term >= 0 else None,
                            issue_mi=im, term_mi=term))
            mid += 1
    members = pd.DataFrame(mem)
    mmf = pd.DataFrame(mm, columns=["member_id", "lob", "month", "state", "plan", "rate_cell", "premium", "mi"])
    clf = pd.DataFrame(cl, columns=["member_id", "lob", "incurred_month", "paid_month", "paid_amount", "paid_capped",
                                    "paid_excess", "inc_mi", "paid_mi"])
    return fl.Book(members, mmf, clf, E, P)


BOOK = make_book()


# ---------------------------------------------------------------- completion
def test_bf_equals_cl_when_apriori_is_chain_ladder():
    rng = np.random.default_rng(1)
    paid = rng.uniform(1e4, 1e5, 20)
    cf = rng.uniform(0.3, 1.0, 20)
    ol = np.arange(20) % 9
    cl = paid / cf
    assert np.allclose(fl.bf_ultimate(paid, cf, cl, ol, bf_max_lag=8), cl, rtol=0, atol=1e-8)


def test_bf_max_lag_minus_one_is_pure_chain_ladder():
    cfg = copy.deepcopy(CFG); cfg["completion"]["bf_max_lag"] = -1
    t = fl.completion_table(BOOK, cfg)
    assert np.allclose(t["ultimate"], t["cl_ultimate"], rtol=0, atol=1e-6)
    assert (t["method"] == "CL").all()


def test_bf_moves_only_the_least_mature_months():
    t = fl.completion_table(BOOK, CFG)
    old = t[t["observable_lag"] > CFG["completion"]["bf_max_lag"]]
    assert np.allclose(old["ultimate"], old["cl_ultimate"], rtol=0, atol=1e-6)


@needs_db
def test_python_triangle_reproduces_sql_completion_factors():
    import duckdb
    book = fl.load_book()
    con = duckdb.connect(str(DB), read_only=True)
    sql = con.execute("SELECT lob, lag, completion_factor FROM completion_factors").fetchdf(); con.close()
    py = fl.cl_factors(fl.lag_triangle(book.claims, book.paid_through))
    m = sql.merge(py, on=["lob", "lag"], suffixes=("_sql", "_py"))
    assert len(m) == len(sql)
    assert np.allclose(m["completion_factor_py"].round(4), m["completion_factor_sql"], rtol=0, atol=1e-12)


@needs_db
def test_vintage_cut_at_todays_dates_reproduces_todays_completion():
    book = fl.load_book()
    t_full = fl.completion_table(book, CFG)
    t_cut = fl.completion_table(fl.cut_book(book, book.elig_as_of, book.paid_through), CFG)
    m = t_full.merge(t_cut, on=["lob", "inc_mi"], suffixes=("_a", "_b"))
    assert len(m) == len(t_full)
    for c in ["completion_factor", "paid_to_date", "ultimate"]:
        assert np.allclose(m[f"{c}_a"], m[f"{c}_b"], rtol=0, atol=1e-6), c


# ---------------------------------------------------------------- projection
@pytest.fixture(scope="module")
def base_forecast():
    return fl.forecast(BOOK, horizon=24, cfg=CFG)


def test_roll_forward_conserves_members(base_forecast):
    f = base_forecast.frame
    for (lob, cohort), g in f.groupby(["lob", "cohort"]):
        m = g.groupby("month_mi")[["members_start", "new_members", "deaths", "lapses", "members_end"]].sum().sort_index()
        assert np.allclose(m["members_end"], m["members_start"] + m["new_members"] - m["deaths"] - m["lapses"], atol=1e-9)
        assert np.allclose(m["members_end"].to_numpy()[:-1], m["members_start"].to_numpy()[1:], atol=1e-6), (lob, cohort)


def test_inforce_start_equals_members_in_force_at_as_of(base_forecast):
    f = base_forecast.frame
    first = f[(f["month_mi"] == BOOK.elig_as_of + 1) & (f["cohort"] == "INFORCE")].groupby("lob")["members_start"].sum()
    last = BOOK.mm[BOOK.mm["mi"] == BOOK.elig_as_of].merge(BOOK.members[["member_id", "term_mi"]], on="member_id")
    expect = last[last["term_mi"] != BOOK.elig_as_of].groupby("lob").size()
    assert np.allclose(first.sort_index(), expect.sort_index().astype(float))


def test_ibnr_runoff_equals_ibnr_held(base_forecast):
    f, ib = base_forecast.frame, base_forecast.ibnr_at_as_of
    for r in ib.itertuples(index=False):
        assert abs(f.loc[f["lob"] == r.lob, "ibnr_runoff"].sum() - r.ibnr_pooled) < 1e-6


def test_flat_assumptions_give_flat_members_and_pmpm():
    flat = {"MEDSUPP": dict(trend=0.0, lapse=[0.0, 0.0, 0.0], shock=0.0, q65=0.0, age_slope=0.0, selection=[1.0, 1.0, 1.0],
                            premium_age_step=0.0, rate_action=0.0, new_sales_per_month=0.0, seasonality=np.ones(12)),
            "DENTAL_VISION": dict(trend=0.0, lapse=[0.0, 0.0, 0.0], pent_up=1.0, rate_action=0.0, new_sales_per_month=0.0,
                                  seasonality=np.ones(12)),
            "MEDICAID": dict(trend=0.0, monthly_churn=0.0, rate_action=0.0, new_sales_per_month=0.0, seasonality=np.ones(12))}
    f = fl.forecast(BOOK, horizon=24, cfg=CFG, overrides=flat).frame
    m = f.groupby(["lob", "month_mi"])[["member_months", "premium", "incurred_pooled"]].sum()
    for lob, g in m.groupby(level="lob"):
        assert np.allclose(g["member_months"], g["member_months"].iloc[0], rtol=1e-12), lob
        pm = g["incurred_pooled"] / g["member_months"]
        pp = g["premium"] / g["member_months"]
        assert np.allclose(pm, pm.iloc[0], rtol=1e-12), lob
        assert np.allclose(pp, pp.iloc[0], rtol=1e-12), lob


def test_trend_compounds_monthly():
    ov = {l: dict(seasonality=np.ones(12), new_sales_per_month=0.0) for l in fl.LOBS}
    ov["DENTAL_VISION"].update(lapse=[0.0, 0.0, 0.0], pent_up=1.0, trend=0.05)
    f = fl.forecast(BOOK, horizon=24, cfg=CFG, overrides=ov, lobs=["DENTAL_VISION"]).frame
    m = f.groupby("month_mi")[["member_months", "incurred_pooled"]].sum()
    pm = (m["incurred_pooled"] / m["member_months"]).to_numpy()
    assert np.isclose(pm[12] / pm[0], 1.05, rtol=1e-12)


# ---------------------------------------------------------------- A/E waterfall
def test_waterfall_components_sum_to_total_variance():
    E_v = BOOK.elig_as_of - 12
    P_v = E_v + 2
    exp_f = fl.forecast(fl.cut_book(BOOK, E_v, P_v), cfg=CFG).frame
    rst = fl.forecast(fl.cut_book(BOOK, E_v, BOOK.paid_through), cfg=CFG).frame
    comp_v = fl.completion_table(fl.cut_book(BOOK, E_v, P_v), CFG)
    comp_c = fl.completion_table(BOOK, CFG)
    act = fl.actuals(BOOK, CFG, vintage_E=E_v)
    months = list(range(E_v + 1, E_v + 13))
    for lob in fl.LOBS:
        t = ae.segment_table(exp_f, rst, act, lob, months)
        rows, s = ae.waterfall(lob, t, exp_f, act, months, comp_v, comp_c, E_v)   # asserts internally
        w = pd.DataFrame(rows)
        c = w[w["measure"] == "claims"]["amount"].to_numpy()
        assert abs(c[0] + c[1:-1].sum() - c[-1]) < 1e-6
        p = w[w["measure"] == "premium"]["amount"].to_numpy()
        assert abs(p[0] + p[1:-1].sum() - p[-1]) < 1e-6
        assert np.isclose(s["margin_variance"], (p[-1] - p[0]) - (c[-1] - c[0]))


def test_waterfall_steps_match_independent_calculations():
    """The sum test above holds by construction (trend and morbidity is the residual), so it cannot
    catch a wrong step. Check individual steps against independent calculations instead:
      * restated base = the vintage's own forecast  ->  base re-estimate is exactly 0;
      * volume steps  = (actual - expected member months) x expected average PMPM, by cohort;
      * mix           = sum(actual mm x expected segment PMPM) - actual mm x expected average PMPM."""
    E_v = BOOK.elig_as_of - 12
    P_v = E_v + 2
    exp_f = fl.forecast(fl.cut_book(BOOK, E_v, P_v), cfg=CFG).frame
    comp_v = fl.completion_table(fl.cut_book(BOOK, E_v, P_v), CFG)
    comp_c = fl.completion_table(BOOK, CFG)
    act = fl.actuals(BOOK, CFG, vintage_E=E_v)
    months = list(range(E_v + 1, E_v + 13))
    for lob in fl.LOBS:
        t = ae.segment_table(exp_f, exp_f, act, lob, months)          # hindsight restatement := the forecast itself
        rows, _ = ae.waterfall(lob, t, exp_f, act, months, comp_v, comp_c, E_v)
        w = pd.DataFrame(rows).set_index("step")
        cl = w[w["measure"] == "claims"]["amount"]
        assert abs(cl["base re-estimate (hindsight completion)"]) < 1e-6
        avg = t["clm_e"].sum() / t["mm_e"].sum()
        inf = t["cohort"] == "INFORCE"
        assert np.isclose(cl["volume: in-force retention"], (t.loc[inf, "mm_a"].sum() - t.loc[inf, "mm_e"].sum()) * avg)
        assert np.isclose(cl["volume: new sales"], (t.loc[~inf, "mm_a"].sum() - t.loc[~inf, "mm_e"].sum()) * avg)
        pm_e = np.where(t["mm_e"] > 0, t["clm_e"] / t["mm_e"].where(t["mm_e"] > 0, 1), avg)
        assert np.isclose(cl["mix"], float((t["mm_a"] * pm_e).sum() - t["mm_a"].sum() * avg))


# ---------------------------------------------------------------- vintage registry
def test_registry_keeps_one_vintage_per_as_of_and_refuses_reused_run_id(tmp_path):
    frame = pd.DataFrame({"lob": ["MEDSUPP"], "month_mi": [1], "member_months": [1.0]})
    fl.register_vintage(tmp_path, "RUN-1", frame, dict(as_of="2025-12", paid_through="2026-02"))
    # Rerunning the same cycle replaces its vintage.
    fl.register_vintage(tmp_path, "RUN-1", frame.assign(member_months=2.0), dict(as_of="2025-12", paid_through="2026-02"))
    reg = fl.read_registry(tmp_path)
    assert list(reg["as_of"]) == ["2025-12"]
    assert pd.read_csv(tmp_path / reg["file"].iloc[0])["member_months"].iloc[0] == 2.0
    # The same as-of under another run id is still one vintage, not two.
    fl.register_vintage(tmp_path, "RUN-1b", frame, dict(as_of="2025-12", paid_through="2026-02"))
    assert list(fl.read_registry(tmp_path)["as_of"]) == ["2025-12"]
    # A new cycle with a new run id is added.
    fl.register_vintage(tmp_path, "RUN-2", frame, dict(as_of="2026-03", paid_through="2026-05"))
    assert sorted(fl.read_registry(tmp_path)["as_of"]) == ["2025-12", "2026-03"]
    # Reusing a run id for a different month would overwrite history: refused.
    with pytest.raises(SystemExit, match="new run_id"):
        fl.register_vintage(tmp_path, "RUN-2", frame, dict(as_of="2026-04", paid_through="2026-06"))
    assert sorted(fl.read_registry(tmp_path)["as_of"]) == ["2025-12", "2026-03"]


def test_cut_book_hides_later_terminations_and_claims():
    E, P = fl.mi("2024-12"), fl.mi("2025-02")
    b = fl.cut_book(BOOK, E, P)
    assert b.mm["mi"].max() <= E and b.claims["paid_mi"].max() <= P and b.claims["inc_mi"].max() <= E
    assert not (b.members["term_mi"] > E).any()


def test_pooling_credibility_scales_the_standard_by_excess_severity_cv():
    """v4.1: the pooling charge is an aggregate, so n_full = 1082 x (1 + CV^2) of excess severity."""
    import pandas as pd
    z1, nf1, n1 = fl.pooling_credibility(pd.Series([0.0, 1_600_000.0, 0.0]), {"full_credibility_claims": 1082})
    assert n1 == 1 and nf1 == pytest.approx(1082 * 2) and z1 == pytest.approx((1 / 2164) ** 0.5)   # CV default 1
    x = pd.Series([100.0, 300.0, 0.0, 200.0])
    cv = float(np.std([100.0, 300.0, 200.0], ddof=1) / 200.0)
    z2, nf2, n2 = fl.pooling_credibility(x, {"full_credibility_claims": 1082})
    assert n2 == 3 and nf2 == pytest.approx(1082 * (1 + cv ** 2)) and z2 == pytest.approx((3 / nf2) ** 0.5)
    z3, _, _ = fl.pooling_credibility(pd.Series(np.full(5000, 10.0)), {"full_credibility_claims": 1082})
    assert z3 == 1.0                                                                         # capped at full

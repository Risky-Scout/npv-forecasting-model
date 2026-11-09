"""
Tests for the version 3 model. Each test pins down one property a reviewer would
otherwise have to take on trust:

  * Regression: with every v3 feature off, the engine reproduces the archived
    version 2 engine to the cent (deterministic NPVs, monthly cash flows, and the
    Monte Carlo draws), so every change from v2 is attributable to a named feature.
  * Profit-test identities: with no tax, no capital, assets earning the hurdle rate
    and immediate claim payment, DE NPV equals cash NPV exactly; with a lag only, it
    equals an independently computed PV of the re-timed cash flows.
  * Each v3 mechanic behaves as specified (anti-selection, dynamic lapse, rate
    cells, risk adjustment, correlated Monte Carlo) and is neutral at its neutral
    setting.
Run: python -m pytest -q tests   (no prior pipeline output needed)
"""
import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "code" / "python"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


eng = _load("engine_v3", PY / "04_npv_engine.py")
eng2 = _load("engine_v2", PY / "archive" / "v2_monthly_npv_engine.py")
A = eng.A
G = A["global"]
G_OFF = eng.without_features(G)
LOBS = ["MEDSUPP", "DENTAL_VISION", "MEDICAID"]


# ---------------------------------------------------------------- regression to version 2
@pytest.mark.parametrize("lob", LOBS)
def test_features_off_reproduce_version2_npv(lob):
    assert abs(eng.npv(lob, A[lob], G_OFF) - eng2.npv(lob, eng2.A[lob], eng2.A["global"])) < 0.01


@pytest.mark.parametrize("lob", LOBS)
def test_features_off_reproduce_version2_cash_flows(lob):
    p3, p2 = eng.PROJECT[lob](A[lob], G_OFF), eng2.PROJECT[lob](eng2.A[lob], eng2.A["global"])
    for key in ["premium", "claims", "revenue", "medical", "pv_cash", "pv_operating"]:
        if key in p2:
            assert np.allclose(p3[key], p2[key], rtol=0, atol=1e-6), key


def test_features_off_reproduce_version2_monte_carlo():
    AA = copy.deepcopy(A); AA["global"] = G_OFF
    mc3, mc2 = eng.monte_carlo(assumptions=AA), eng2.monte_carlo()
    for lob in LOBS:
        assert np.allclose(mc3[lob].values, mc2[lob].values, rtol=0, atol=1e-4)


# ---------------------------------------------------------------- profit-test identities
@pytest.mark.parametrize("lob", LOBS)
def test_profit_test_identity_equals_cash_npv(lob):
    p = eng.PROJECT[lob](A[lob], G)
    ov = dict(federal_income_tax_rate=0.0, investment_earned_rate=G["discount_rate"],
              required_capital_pct_of_annual_claims=0.0, claim_payment_pattern=[1.0])
    assert abs(float(eng.profit_test(lob, p, A[lob], G, ov)["de_npv"]) - float(p["npv"])) < 0.01


@pytest.mark.parametrize("lob", ["MEDSUPP", "DENTAL_VISION"])
def test_lag_only_equals_independent_pv_of_retimed_cash(lob):
    """Reserves earning the hurdle rate are PV-neutral, so DE NPV = PV of cash with claims re-timed."""
    p = eng.PROJECT[lob](A[lob], G)
    pat = np.array(A[lob]["claim_payment_pattern"])
    i = G["discount_rate"]
    K = len(p["claims"])
    pv_paid = sum(p["claims"][k] * w * (1 + i) ** (-(k + lag + 0.5) / 12) for k in range(K) for lag, w in enumerate(pat))
    pv_other = float((p["cf_start"] * (1 + i) ** (-np.arange(K) / 12)).sum() + (p["cf_mid"] * (1 + i) ** (-(np.arange(K) + 0.5) / 12)).sum())
    expected = pv_other - pv_paid + float(p["t0"])
    ov = dict(federal_income_tax_rate=0.0, investment_earned_rate=i, required_capital_pct_of_annual_claims=0.0)
    assert abs(float(eng.profit_test(lob, p, A[lob], G, ov)["de_npv"]) - expected) < 0.01


@pytest.mark.parametrize("lob", LOBS)
def test_liabilities_run_off_and_stay_non_negative(lob):
    p = eng.PROJECT[lob](A[lob], G)
    pt = eng.profit_test(lob, p, A[lob], G)
    assert abs(pt["claim_reserve"][-1]) < 1e-6 and abs(pt["mlr_liability"][-1]) < 1e-6
    assert (pt["claim_reserve"] > -1e-6).all() and (pt["required_capital"] >= 0).all()
    assert abs(pt["paid_claims"].sum() - pt["incurred"].sum()) < 1e-4          # every claim is paid once


def test_tax_and_capital_reduce_value():
    i = G["discount_rate"]
    for lob in LOBS:
        p = eng.PROJECT[lob](A[lob], G)
        no_tax = eng.profit_test(lob, p, A[lob], G, dict(federal_income_tax_rate=0.0))["de_npv"]
        pt = eng.profit_test(lob, p, A[lob], G)
        taxed = pt["de_npv"]
        # Tax costs value exactly when the PV of TAXABLE income (statutory book profit + investment
        # income on capital, plus the t0 item, plus the §848 timing cost) is positive. Losses are used
        # immediately (documented simplification), so a line with negative taxable income receives a
        # tax benefit (v4.1: D&V). This is the sign of taxable income, not of distributable earnings:
        # Medicaid is profitable before the cost of capital, so tax costs it value even though its
        # DE NPV after the capital charge is negative.
        v = (1 + i) ** (-(np.arange(pt["months"]) + 1) / 12)
        pv_tax = float((np.asarray(pt["tax"]) * v).sum()) + float(p["t0"]) * G["profit_test"]["federal_income_tax_rate"] \
            + float(pt["dac"]["pv"])
        assert abs((no_tax - taxed) - pv_tax) < 1e-4 * max(1.0, abs(pv_tax))   # the tax cost IS the PV of tax
        assert (taxed < no_tax) if pv_tax > 0 else (taxed > no_tax)
        no_cap = eng.profit_test(lob, p, A[lob], G, dict(federal_income_tax_rate=0.0, required_capital_pct_of_annual_claims=0.0))["de_npv"]
        assert no_tax < no_cap        # capital earning less than the hurdle rate costs value


def test_payment_pattern_must_sum_to_one():
    p = eng.PROJECT["DENTAL_VISION"](A["DENTAL_VISION"], G)
    with pytest.raises(AssertionError):
        eng.profit_test("DENTAL_VISION", p, A["DENTAL_VISION"], G, dict(claim_payment_pattern=[0.5, 0.4]))


# ---------------------------------------------------------------- anti-selective and dynamic lapse
def test_antiselection_neutral_in_year1_and_raises_claim_cost_later():
    p_on, p_off = eng.project_medsupp(A["MEDSUPP"], G), eng.project_medsupp(A["MEDSUPP"], G_OFF)
    assert np.allclose(p_on["claims"][:12], p_off["claims"][:12])           # issue-mix average relativity = 1
    rel = p_on["claim_relativity"][::12]
    assert np.all(np.diff(rel) >= -1e-12) and rel[-1] > 1.05                 # shoppers leave, cost drifts up
    assert np.allclose(p_on["in_force_start"], p_off["in_force_start"])     # block lapse unchanged


def test_antiselection_with_equal_costs_is_version2():
    a = copy.deepcopy(A["MEDSUPP"]); a["antiselection"]["shopper_relative_claim_cost"] = 1.0
    g = copy.deepcopy(G); g["features"]["dynamic_shock_lapse"] = False
    assert abs(eng.npv("MEDSUPP", a, g) - eng2.npv("MEDSUPP", eng2.A["MEDSUPP"], eng2.A["global"])) < 0.01


def test_dynamic_lapse_neutral_at_reference_and_responds_to_rate():
    a = A["MEDSUPP"]
    assert float(eng.medsupp_shock(a, G, a["dynamic_shock_lapse"]["reference_rate_increase"])) == pytest.approx(a["shock_lapse_add_from_year2"])
    assert float(eng.medsupp_shock(a, G, 0.09)) > a["shock_lapse_add_from_year2"] > float(eng.medsupp_shock(a, G, 0.05))
    assert float(eng.medsupp_shock(a, G, -1.0)) == 0.0                       # floored at zero
    assert float(eng.medsupp_shock(a, G_OFF, 0.09)) == a["shock_lapse_add_from_year2"]


# ---------------------------------------------------------------- Medicaid rate cells and risk adjustment
def test_rate_cells_blend_to_contract_level_assumptions():
    _, s, cap, med, chn = eng.medicaid_cells(A["MEDICAID"], G)
    assert abs((s * cap).sum() - 1) < 1e-12 and abs((s * cap * med).sum() - 1) < 1e-12 and abs((s * chn).sum() - 1) < 1e-12
    # Revenue booked in month 1 is the blended capitation net of the quality withhold (v4.1: 2% public
    # basis); the expected earn-back is received after the contract year, not in month 1.
    p = eng.project_medicaid(A["MEDICAID"], G)
    w = A["MEDICAID"]["contract_terms"]["quality_withhold"]["withhold_pct"]
    assert p["revenue"][0] / p["member_months"][0] == pytest.approx(A["MEDICAID"]["capitation_pmpm_year1"] * (1 - w), rel=1e-9)
    a = copy.deepcopy(A["MEDICAID"]); a["contract_terms"]["quality_withhold"]["withhold_pct"] = 0.0
    p = eng.project_medicaid(a, G)                               # no withhold: exactly the contract capitation
    assert p["revenue"][0] / p["member_months"][0] == pytest.approx(A["MEDICAID"]["capitation_pmpm_year1"], rel=1e-9)


def test_rate_cells_mix_drift_when_churn_differs():
    a = copy.deepcopy(A["MEDICAID"]); a["rate_cells"]["disenrollment_relativity"] = [1.1, 1.1, 0.6]
    p = eng.project_medicaid(a, G)
    abd_share = p["members_ABD"] / p["members_start"]
    assert abd_share[-1] > abd_share[0] + 0.02                                 # stickier ABD members accumulate
    assert p["revenue"][-1] / p["member_months"][-1] > A["MEDICAID"]["capitation_pmpm_year1"] * (1 + A["MEDICAID"]["annual_rate_update"]) ** 9


def test_risk_adjustment_neutral_and_partial_capture():
    assert tuple(float(x) for x in eng.medicaid_risk_adjustment(A["MEDICAID"], G)) == (1.0, 1.0)
    a = copy.deepcopy(A["MEDICAID"]); a["risk_adjustment"]["plan_acuity"] = 1.03
    rev, med = (float(x) for x in eng.medicaid_risk_adjustment(a, G))
    assert med == pytest.approx(1.03) and rev == pytest.approx(1 + a["risk_adjustment"]["revenue_capture"] * 0.03)
    assert eng.npv("MEDICAID", a, G) < eng.npv("MEDICAID", A["MEDICAID"], G)  # sicker plan, partly paid for


# ---------------------------------------------------------------- correlated Monte Carlo
def test_correlated_draws_recover_the_matrix():
    mc = A["monte_carlo"]
    lm, tr, rs, mr, _ = eng.mc_draws(G, mc, n=50000)
    z = np.vstack([np.log(lm), tr, rs, mr])
    R = np.corrcoef(z)
    assert np.allclose(R, np.array(mc["correlations"]["matrix"]), atol=0.02)


def test_non_positive_definite_correlation_is_rejected():
    mc = copy.deepcopy(A["monte_carlo"])
    mc["correlations"]["matrix"] = [[1, 0.9, 0.9, 0], [0.9, 1, -0.9, 0], [0.9, -0.9, 1, 0], [0, 0, 0, 1]]
    with pytest.raises(np.linalg.LinAlgError):
        eng.correlation_factor(G, mc)


# ---------------------------------------------------------------- labels and bridge
def test_no_driver_is_mislabelled_as_year_one_only():
    labels = [d[0] for v in eng.SENS.values() for d in v]
    assert not any(l.lower().startswith(("year-1 claim", "yr-1")) for l in labels)
    assert any("year 1 only" in l for l in labels)                             # the one genuine year-1 driver


def test_bridge_adds_up():
    br = eng.bridge()
    for lob in LOBS:
        b = br[br.lob == lob]
        start = b.iloc[0]["value"]; changes = b["change"].dropna().sum()
        assert abs(start + changes - b.iloc[-1]["value"]) < 1e-4
        assert abs(b.iloc[-1]["value"] - eng.de_npv(lob, A[lob], G)) < 1e-4


@pytest.mark.parametrize("lob", LOBS)
def test_required_capital_runs_off_with_the_claim_reserve(lob):
    """Capital is held while claims are still being paid after the projection, and released at the end."""
    p = eng.PROJECT[lob](A[lob], G)
    pt = eng.profit_test(lob, p, A[lob], G)
    K = len(p["incurred"])
    assert pt["claim_reserve"][K - 1] > 0 and pt["required_capital"][K] > 0      # runoff month still holds capital
    assert abs(pt["required_capital"][-1]) < 1e-9


def test_engine_refuses_unapproved_set_in_production(tmp_path):
    """Running the engine on its own cannot bypass the approval gate in production mode."""
    import json, os, subprocess, sys
    cfg = json.loads((ROOT / "config" / "run_config.json").read_text()); cfg["mode"] = "production"
    (tmp_path / "run_config.json").write_text(json.dumps(cfg))
    edited = copy.deepcopy(A); edited["DENTAL_VISION"]["claim_trend"] = 0.036
    (tmp_path / "a.json").write_text(json.dumps(edited))
    env = dict(os.environ, RUN_CONFIG=str(tmp_path / "run_config.json"), NPV_ASSUMPTIONS=str(tmp_path / "a.json"),
               NPV_OUTPUT_DIR=str(tmp_path / "out"))
    r = subprocess.run([sys.executable, str(PY / "04_npv_engine.py")], cwd=PY, env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "production run refused" in (r.stdout + r.stderr)

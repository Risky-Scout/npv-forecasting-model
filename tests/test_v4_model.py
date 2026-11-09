"""
Tests for the version 4 model additions. As with the v3 tests, each one pins down a property a
reviewer would otherwise take on trust:

  * Neutrality: every v4 feature at its neutral setting reproduces v3 to the cent
    (deterministic NPVs and the v3-basis Monte Carlo), so each change is attributable.
  * Contract mechanics against hand calculations: the risk corridor, the quality withhold,
    directed payments, the acuity path with lagged risk adjustment, D&V waiting periods.
  * Monte Carlo layers: zero process volatility = level shifts only; a zero-strength filing
    response = no response; the response and the v3 correlation proxy cannot both be on; with full
    gap closure the premium index tracks the lagged claim index exactly.
  * Reverse stress and portfolio: NPV is zero at each reported break-even; the base model point
    is the headline cohort; Plan F is not sold to new business.
Run: python -m pytest -q tests
"""
import copy
import sys
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "code" / "python"
sys.path.insert(0, str(PY))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


eng = _load("engine_v4", PY / "04_npv_engine.py")
A = eng.A
G = A["global"]
LOBS = ["MEDSUPP", "DENTAL_VISION", "MEDICAID"]
V4 = ["medicaid_contract_terms", "dv_benefit_categories", "mc_process_risk", "mc_management_response"]


def g_v4_off():
    g = copy.deepcopy(G)
    for f in V4:
        g["features"][f] = False
    return g


def neutral_md():
    """Medicaid assumptions with every contract term at its neutral (v3) value. The shipped public
    basis carries a 2% quality withhold (v4.1), so tests of the neutral property start from here."""
    a = copy.deepcopy(A["MEDICAID"])
    c = a["contract_terms"]
    c["acuity_shift"]["medical_factor_by_year"] = []
    c["quality_withhold"]["withhold_pct"] = 0.0
    c["directed_payments"]["pmpm_year1"] = 0.0
    c["mlr_rule"]["type"] = "floor"
    return a


def md(**terms):
    """Medicaid assumptions from the NEUTRAL contract terms, with terms overridden ('__' = nesting)."""
    a = neutral_md()
    for path, v in terms.items():
        tgt = a["contract_terms"]
        *head, leaf = path.split("__")
        for h in head:
            tgt = tgt[h]
        tgt[leaf] = v
    return a


def g_terms_on():
    g = copy.deepcopy(G); g["features"]["medicaid_contract_terms"] = True
    return g


# ---------------------------------------------------------------- neutrality to v3
@pytest.mark.parametrize("lob", LOBS)
def test_v4_deterministic_features_neutral_reproduce_v3(lob):
    g_off = g_v4_off()
    a = neutral_md() if lob == "MEDICAID" else A[lob]          # neutral contract terms = v3
    assert abs(eng.npv(lob, a, G) - eng.npv(lob, a, g_off)) < 0.01
    assert abs(eng.de_npv(lob, a, G) - eng.de_npv(lob, a, g_off)) < 0.01


def test_shipped_withhold_is_the_only_non_neutral_contract_term_and_costs_value():
    c = A["MEDICAID"]["contract_terms"]
    assert c["acuity_shift"]["medical_factor_by_year"] == [] and c["directed_payments"]["pmpm_year1"] == 0.0
    assert c["mlr_rule"]["type"] == "floor"
    assert eng.npv("MEDICAID", A["MEDICAID"], G) < eng.npv("MEDICAID", neutral_md(), G)


def test_growth_index_without_path_is_exact_power():
    for t in range(1, 21):
        assert eng.growth_index(0.06, t) == (1.06) ** (t - 1)
    dev = np.zeros((20, 3))
    for t in range(1, 21):
        assert np.allclose(eng.growth_index(0.06, t, dev), 1.06 ** (t - 1), rtol=1e-12)


def _v3_basis():
    AA = copy.deepcopy(A)
    AA["global"] = g_v4_off()
    R = AA["monte_carlo"]["correlations"]["matrix"]; R[1][2] = R[2][1] = eng.V3_TREND_RATE_CORR
    return AA


def test_process_risk_with_zero_volatility_is_level_shifts_only():
    base = eng.monte_carlo(assumptions=_v3_basis())
    AA = _v3_basis(); AA["global"]["features"]["mc_process_risk"] = True
    pr = AA["monte_carlo"]["process_risk"]
    pr.update(trend_sd_annual=0.0, medsupp_approval_noise_sd=0.0, medicaid_rate_update_noise_sd=0.0)
    for col in base:
        assert np.allclose(eng.monte_carlo(assumptions=AA)[col].values, base[col].values, rtol=1e-9, atol=1e-3), col


def test_zero_strength_response_equals_no_response():
    AA = copy.deepcopy(A)
    for l in ("MEDSUPP", "DENTAL_VISION"):
        AA["monte_carlo"]["management_response"][l]["gap_closure_per_filing"] = 0.0
    BB = copy.deepcopy(A); BB["global"]["features"]["mc_management_response"] = False
    a_, b_ = eng.monte_carlo(assumptions=AA), eng.monte_carlo(assumptions=BB)
    for col in a_:
        assert np.allclose(a_[col].values, b_[col].values, rtol=1e-9, atol=1e-3), col


def test_response_and_v3_correlation_proxy_cannot_both_be_on():
    AA = copy.deepcopy(A)
    R = AA["monte_carlo"]["correlations"]["matrix"]; R[1][2] = R[2][1] = -0.5
    with pytest.raises(AssertionError, match="counted twice"):
        eng.monte_carlo(assumptions=AA)


def test_full_gap_closure_tracks_lagged_claim_index_exactly():
    # Constant +1 pt trend deviation, lag 2, h = 1: after each filing the premium index deviation
    # equals the claim index deviation visible two years earlier.
    Y, r, tau = 12, 0.07, 0.06
    dev = np.full((Y, 1), 0.01)
    rho = eng.management_rate_path(r, tau, dev, np.zeros((Y, 1)), {"lag_years": 2, "gap_closure_per_filing": 1.0})
    D = np.r_[0.0, np.cumsum(np.log((1 + tau + dev[:, 0]) / (1 + tau)))]          # D[t-1] for year t
    P = np.cumsum(np.log((1 + r + rho[:, 0]) / (1 + r)))                          # after step s
    for s in range(1, Y + 1):
        known = s + 1 - 2
        assert abs(P[s - 1] - (D[known - 1] if known >= 1 else 0.0)) < 1e-12


def test_response_cuts_the_adverse_tail_for_med_supp():
    on = eng.monte_carlo()
    AA = copy.deepcopy(A); AA["global"]["features"]["mc_management_response"] = False
    off = eng.monte_carlo(assumptions=AA)
    assert np.percentile(on["MEDSUPP"], 5) > np.percentile(off["MEDSUPP"], 5)


# ---------------------------------------------------------------- Medicaid contract terms
def test_risk_corridor_matches_hand_calculation():
    ct = eng.medicaid_terms(md(mlr_rule__type="corridor"), g_terms_on())
    co = dict(target_mlr=0.86, band_1=0.02, band_2=0.05, plan_share_band_2=0.5, plan_share_beyond=0.0)
    ct["corridor"] = co
    f = lambda med: float(eng.mlr_settlement(100.0, med, A["MEDICAID"], ct))
    # MLR 80%: state takes 50% of (84 - 81) + 100% of (81 - 80) = 1.5 + 1.0
    assert abs(f(80.0) - 2.5) < 1e-12
    assert abs(f(92.0) + 2.5) < 1e-12          # mirror image: state pays the plan
    assert f(86.0) == 0.0 and f(87.5) == 0.0 and f(84.5) == 0.0
    # floor rule unchanged
    ctf = dict(ct, rule="floor")
    assert abs(float(eng.mlr_settlement(100.0, 80.0, A["MEDICAID"], ctf)) - 5.0) < 1e-12


def test_withhold_fully_lost_costs_its_pv_and_full_earn_back_costs_only_timing():
    g = g_terms_on()
    a0 = md(); a0["mlr_floor"] = 0.0                      # isolate the withhold from the floor
    p0 = eng.project_medicaid(a0, g)
    w = 0.02
    lost = eng.project_medicaid(md(quality_withhold__withhold_pct=w, quality_withhold__expected_earn_back=0.0) | {"mlr_floor": 0.0}, g)
    full = eng.project_medicaid(md(quality_withhold__withhold_pct=w, quality_withhold__expected_earn_back=1.0) | {"mlr_floor": 0.0}, g)
    wgt = np.repeat(p0["yearly"]["prob_in_force"], 12)
    pv_gross_cap = float((wgt * p0["revenue"] * p0["v_mid"]).sum())     # revenue = gross capitation at neutral terms
    assert abs((float(p0["npv"]) - float(lost["npv"])) - w * pv_gross_cap) < 1e-3
    assert float(lost["npv"]) < float(full["npv"]) < float(p0["npv"])
    # Full earn-back costs exactly the timing: each month's withheld amount is received at the
    # contract-year end + 6 months instead of in its month (about a year's delay on average).
    i = G["discount_rate"]
    y = p0["yearly"]
    cap_y = pd.Series(p0["revenue"]).groupby(np.arange(len(p0["revenue"])) // 12).sum().values
    timing = w * (pv_gross_cap - float((y["prob_in_force"] * cap_y * (1 + i) ** (-(y["year"] + 0.5))).sum()))
    assert abs((float(p0["npv"]) - float(full["npv"])) - timing) < 1e-3


def test_directed_payments_are_a_pass_through_except_premium_tax():
    g = g_terms_on()
    a0 = md(); a0["mlr_floor"] = 0.0; a0["premium_tax"] = 0.0
    a1 = md(directed_payments__pmpm_year1=40.0, directed_payments__annual_trend=0.03); a1["mlr_floor"] = 0.0; a1["premium_tax"] = 0.0
    assert abs(float(eng.project_medicaid(a0, g)["npv"]) - float(eng.project_medicaid(a1, g)["npv"])) < 1e-3
    # With the floor binding, pass-through payments raise the MLR and lower the remittance.
    lo = md(); lo["medical_cost_ratio_year1"] = 0.78
    hi = md(directed_payments__pmpm_year1=40.0); hi["medical_cost_ratio_year1"] = 0.78
    r_lo = eng.project_medicaid(lo, g)["yearly"]["mlr_remittance"].sum()
    r_hi = eng.project_medicaid(hi, g)["yearly"]["mlr_remittance"].sum()
    assert r_lo > 0 and r_hi < r_lo


def test_acuity_path_hits_medical_now_and_revenue_after_the_lag():
    g = g_terms_on()
    base = eng.project_medicaid(md(), g)["yearly"]
    sh = eng.project_medicaid(md(acuity_shift__medical_factor_by_year=[1.0, 1.10], acuity_shift__risk_adjustment_lag_years=1), g)["yearly"]
    cap = A["MEDICAID"]["risk_adjustment"]["revenue_capture"]
    assert abs(sh["medical"][1] / base["medical"][1] - 1.10) < 1e-9          # year 2 medical x 1.10
    assert abs(sh["revenue"][1] / base["revenue"][1] - 1.0) < 1e-9           # revenue not yet
    assert abs(sh["revenue"][2] / base["revenue"][2] - (1 + cap * 0.10)) < 1e-9   # year 3: risk adjustment catches up
    assert abs(sh["medical"][2] / base["medical"][2] - 1.0) < 1e-9


# ---------------------------------------------------------------- D&V benefit categories
def test_dv_waiting_period_removes_the_category_and_defers_its_pent_up():
    g = copy.deepcopy(G); g["features"]["dv_benefit_categories"] = True
    a = copy.deepcopy(A["DENTAL_VISION"]); a["benefit_categories"]["waiting_period_months"] = [0, 0, 12, 0]
    base, wp = eng.project_dv(A["DENTAL_VISION"], g), eng.project_dv(a, g)
    share_major = a["benefit_categories"]["cost_share"][2]
    assert np.allclose(wp["claims"][:12], base["claims"][:12] * (1 - share_major), rtol=1e-12)
    # v4.1: the waited category's pent-up demand is deferred to its first 12 eligible months (13-24),
    # so year 2 carries the MAJOR pent-up load; from year 3 claims are unchanged.
    pent = A["DENTAL_VISION"]["first_year_pent_up_factor"]
    exp_y2 = base["claims"][12:24] * (1 + share_major * (pent - 1))      # base year 2 has no pent-up
    assert np.allclose(wp["claims"][12:24], exp_y2, rtol=1e-12)
    assert np.allclose(wp["claims"][24:], base["claims"][24:], rtol=1e-12)


def test_dv_pent_up_relativities_keep_the_blended_year1_factor():
    g = copy.deepcopy(G); g["features"]["dv_benefit_categories"] = True
    a = copy.deepcopy(A["DENTAL_VISION"]); a["benefit_categories"]["pent_up_relativity"] = [0.5, 1.0, 2.0, 0.7]
    assert np.allclose(eng.project_dv(a, g)["claims"][:12], eng.project_dv(A["DENTAL_VISION"], g)["claims"][:12], rtol=1e-12)


def test_dv_category_shares_must_sum_to_one():
    g = copy.deepcopy(G); g["features"]["dv_benefit_categories"] = True
    a = copy.deepcopy(A["DENTAL_VISION"]); a["benefit_categories"]["cost_share"] = [0.3, 0.3, 0.3, 0.3]
    with pytest.raises(AssertionError):
        eng.project_dv(a, g)


# ---------------------------------------------------------------- reverse stress, scenarios, portfolio
def test_npv_is_zero_at_every_reported_breakeven():
    be = eng.breakeven()
    hit = be[be.breakeven.notna()]
    assert len(hit) > 0
    for r in hit.itertuples():
        key, how = next((k, h) for (lab, k, h, _) in eng.BREAKEVEN[r.lob] if lab == r.driver)
        x = r.breakeven - r.base_level if how == "add" else r.breakeven
        assert abs(eng.shocked(r.lob, key, how, x, r.measure)) < 1.0, (r.lob, r.driver, r.measure)


def test_planning_base_scenario_is_the_headline():
    ps = eng.planning_scenarios()
    b = ps[ps.scenario == "Base"].set_index("lob")
    for lob in LOBS:
        assert abs(b.loc[lob, "cash_npv"] - eng.npv(lob, A[lob], G)) < 1e-6


def test_portfolio_base_cell_is_headline_and_mix_excludes_plan_f():
    pf = eng.medsupp_portfolio(measure_de=False)
    assert "F" not in A["MEDSUPP"]["model_points"]["plans"]
    cell = pf[(pf.band == "65") & (pf.plan == "G")].iloc[0]
    assert abs(cell.cash_npv_per_1000 - eng.npv("MEDSUPP", A["MEDSUPP"], G)) < 1e-6
    assert abs(pf[pf.band != "PORTFOLIO"].mix.sum() - 1.0) < 1e-9


def test_trend_assumptions_follow_the_credibility_blend_when_indications_exist():
    import json
    f = ROOT / "output" / "assumptions" / "assumption_indications.csv"
    if not f.exists():
        pytest.skip("run 03b first")
    ind = pd.read_csv(f)
    for lob in ("MEDSUPP", "DENTAL_VISION"):
        r = ind[(ind.lob == lob) & (ind.assumption == "claim_trend")].iloc[0]
        assert 0.0 < r.credibility_z < 1.0
        # Either the approved trend is the indication, or the approval records why not (v4.1: the
        # synthetic book is not relevant experience, so the public benchmark is approved).
        if abs(r.indicated - A[lob]["claim_trend"]) > r.tolerance + 1e-12:
            import assumption_control as ac
            st = ac.approval_status(A)
            ap = next(a for a in json.loads(ac.approvals_file().read_text())["approvals"] if a["id"] == st["approval_id"])
            assert f"{lob}.claim_trend" in ap["review_dispositions"], f"{lob} trend differs from the indication without a disposition"


# ---------------------------------------------------------------- Monte Carlo parameter estimator
def test_mc_parameter_estimator_recovers_known_parameters():
    est = _load("est", PY / "estimate_mc_parameters.py")
    rng = np.random.default_rng(7)
    sd, phi, n, reps = 0.012, 0.5, 400, 1
    e = rng.standard_normal(n)
    x = np.zeros(n); x[0] = sd * e[0]
    for t in range(1, n):
        x[t] = phi * x[t - 1] + sd * np.sqrt(1 - phi ** 2) * e[t]
    md_upd = 0.04 + 0.5 * x / sd * 0.01 + np.sqrt(1 - 0.25) * 0.01 * rng.standard_normal(n)
    h = pd.DataFrame({"year": range(n), "medsupp_trend": 0.06 + x, "medicaid_rate_update": md_upd,
                      "medsupp_rate_assumed": 0.07, "medsupp_rate_approved": 0.07 - 0.008 * rng.standard_normal(n)})
    p = est.estimate(h)["proposed"]
    assert abs(p["process_risk.trend_sd_annual"] - sd) < 0.0015
    assert abs(p["process_risk.trend_ar1_phi"] - phi) < 0.1
    assert abs(p["process_risk.medsupp_approval_noise_sd"] - 0.008) < 0.001
    # Level SD is the standard error of the long-run mean, not the yearly SD (no double count).
    assert abs(p["medsupp_rate_increase_deviation_sd"] - 0.008 / np.sqrt(n)) < 0.0003
    assert abs(p["correlation.trend_medicaid_rate_update"] - 0.5) < 0.1


def test_mc_parameter_estimator_refuses_short_history():
    est = _load("est", PY / "estimate_mc_parameters.py")
    with pytest.raises(SystemExit):
        est.estimate(pd.DataFrame({"year": range(4), "medsupp_trend": [0.05, 0.06, 0.07, 0.06]}))


# ---------------------------------------------------------------- v4 governance (review fixes)
def _prod_env(tmp_path, status: dict | None):
    import json, os
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT / "config" / "run_config.json").read_text()); cfg["mode"] = "production"
    (tmp_path / "run_config.json").write_text(json.dumps(cfg))
    out = tmp_path / "out"; (out / "assumptions").mkdir(parents=True)
    if status is not None:
        (out / "assumptions" / "assumption_status.json").write_text(json.dumps(status))
    return dict(os.environ, RUN_CONFIG=str(tmp_path / "run_config.json"), NPV_OUTPUT_DIR=str(out))


def _run_engine(env):
    import subprocess, sys
    return subprocess.run([sys.executable, str(PY / "04_npv_engine.py")], cwd=PY, env=env, capture_output=True, text=True)


def test_engine_in_production_needs_this_runs_gate_to_have_approved(tmp_path):
    """The hash matching an approval is not enough: 03b must have approved the set in this run
    (it alone checks that every REVIEW item has a disposition)."""
    import assumption_control as ac
    h = ac.approval_hash(A)
    for i, status in enumerate((None, {"sha256": h, "approved": False, "reason": "no disposition for REVIEW items"},
                                {"sha256": "other", "approved": True})):
        r = _run_engine(_prod_env(tmp_path / f"case{i}", status))
        assert r.returncode != 0 and "production run refused" in (r.stdout + r.stderr), status


def test_editing_a_governed_config_invalidates_the_approval(monkeypatch):
    import assumption_control as ac
    assert ac.approval_status(A)["approved"]
    cfgs = ac.governed_configs()
    cfgs["config/claim_cost_curves.json"]["MEDSUPP"]["selection_factor_by_duration"] = [0.6, 0.8, 1.0]
    monkeypatch.setattr(ac, "governed_configs", lambda: cfgs)
    assert not ac.approval_status(A)["approved"]


def test_manifest_withholds_results_when_the_gate_did_not_approve(tmp_path):
    import json, os, shutil, subprocess, sys, time
    out = tmp_path / "out"
    (out / "npv").mkdir(parents=True); (out / "controls").mkdir(); (out / "assumptions").mkdir()
    (out / "run_start.json").write_text(json.dumps({"run_id": "T", "mode": "production",
                                                    "started_at_utc": "2000-01-01T00:00:00.000000Z"}))
    time.sleep(0.01)
    shutil.copy(ROOT / "output" / "npv" / "npv_summary.csv", out / "npv" / "npv_summary.csv")
    (out / "controls" / "reconciliation.json").write_text(json.dumps({"result": "PASS"}))
    (out / "assumptions" / "assumption_status.json").write_text(json.dumps({"approved": False, "reason": "test"}))
    env = dict(os.environ, NPV_OUTPUT_DIR=str(out))
    subprocess.run([sys.executable, str(PY / "08_run_manifest.py")], cwd=PY, env=env, capture_output=True, text=True, check=True)
    m = json.loads((out / "run_manifest.json").read_text())
    assert m["results"] is None and m["results_status"].startswith("WITHHELD: assumption set not approved")


def test_higher_rates_scenario_moves_the_earned_rate_too():
    a = copy.deepcopy(A["MEDSUPP"]); g = copy.deepcopy(G)
    eng._apply(a, g, "profit_test.investment_earned_rate", "add", 0.01)
    assert abs(g["profit_test"]["investment_earned_rate"] - (G["profit_test"]["investment_earned_rate"] + 0.01)) < 1e-12


# ---------------------------------------------------------------- v4.1 assumption basis register
def _leaves(x, p=()):
    if isinstance(x, dict):
        for k, v in x.items():
            if not k.startswith("_") and k != "label":
                yield from _leaves(v, p + (k,))
    else:
        yield ".".join(p), x


def test_every_assumption_has_a_documented_basis():
    """Every assumption in the file is covered by config/assumption_basis.json, with the documented
    value, a source for public / statutory values and a sensitivity range for judgment values."""
    import json
    reg = json.loads((ROOT / "config" / "assumption_basis.json").read_text())["entries"]
    exact = {e["key"]: e for e in reg if not e["key"].endswith("*")}
    blocks = [e["key"][:-1] for e in reg if e["key"].endswith("*")]
    missing, drift = [], []
    for k, v in _leaves(A):
        if k in exact:
            if exact[k]["value"] != v:
                drift.append((k, exact[k]["value"], v))
        elif not any(k == b or k.startswith(b + ".") or k.startswith(b) for b in blocks):
            missing.append(k)
    assert not missing, f"assumptions without a documented basis: {missing}"
    assert not drift, f"assumption values differ from the basis register: {drift}"
    for e in reg:
        assert e["status"] in {"P", "S", "D", "J", "E", "M"}, e["key"]
        assert e["basis"].strip(), e["key"]
        if e["status"] in {"P", "S"}:
            assert e["sources"] and all(s.get("url") and s.get("figure") for s in e["sources"]), f"{e['key']}: no source"
        if e["status"] == "J":                                   # v4.1: blocks too
            assert e["sensitivity"], f"{e['key']}: judgment assumption without a sensitivity range"
    # v4.1: a block entry stores the block's value (without documentation strings); it must match.
    strip = lambda x: ({k: strip(v) for k, v in x.items() if not k.startswith("_")} if isinstance(x, dict) else x)
    for e in reg:
        if e["key"].endswith("*"):
            node = A
            for k_ in e["key"][:-1].split("."):
                node = node[k_]
            assert e["value"] == strip(node), f"{e['key']}: block differs from the basis register"
        if e["status"] in {"P", "S", "D", "J"} and not e["key"].endswith("*"):
            assert e["asop"] or e["status"] == "S", f"{e['key']}: no ASOP basis"


def test_synthetic_links_are_disclosed_and_complete():
    """No assumption is set from synthetic EXPERIENCE (status E). Values that equal the synthetic
    generator's own are flagged 'equals_generator', recomputed here from data/true_assumptions.json
    so the disclosure cannot go stale; distribution inputs read off the synthetic book are 'derived'.
    Both appear in docs/ASSUMPTION_BASIS.md."""
    import json
    reg = json.loads((ROOT / "config" / "assumption_basis.json").read_text())["entries"]
    assert not [e["key"] for e in reg if e["status"] == "E"]
    T = json.loads((ROOT / "data" / "true_assumptions.json").read_text())
    gen = {"MEDSUPP.annual_premium_year1": 12 * T["MEDSUPP"]["base_premium_pm_age65"],
           "MEDSUPP.annual_claim_cost_year1_ultimate": 12 * T["MEDSUPP"]["base_claim_pmpm_age65"],
           "MEDSUPP.premium_age_step": T["MEDSUPP"]["premium_age_slope"], "MEDSUPP.claim_age_slope": T["MEDSUPP"]["claim_age_slope"],
           "MEDSUPP.annual_rate_increase": T["MEDSUPP"]["annual_rate_increase"], "MEDSUPP.claim_trend": T["MEDSUPP"]["annual_claim_trend"],
           "MEDSUPP.selection_factors": T["MEDSUPP"]["selection_factor_by_duration"],
           "MEDSUPP.lapse_by_duration": T["MEDSUPP"]["annual_lapse_by_duration"],
           "MEDSUPP.shock_lapse_add_from_year2": T["MEDSUPP"]["shock_lapse_add_january"],
           "DENTAL_VISION.annual_premium_year1": 12 * T["DENTAL_VISION"]["premium_pm"],
           "DENTAL_VISION.annual_rate_increase": T["DENTAL_VISION"]["annual_rate_increase"],
           "DENTAL_VISION.annual_claim_cost_year1_ultimate": 12 * T["DENTAL_VISION"]["base_claim_pmpm"],
           "DENTAL_VISION.claim_trend": T["DENTAL_VISION"]["annual_claim_trend"],
           "DENTAL_VISION.first_year_pent_up_factor": T["DENTAL_VISION"]["first_year_pent_up_factor"],
           "DENTAL_VISION.lapse_by_duration": T["DENTAL_VISION"]["annual_lapse_by_duration"],
           "MEDICAID.annual_rate_update": T["MEDICAID"]["annual_rate_update"], "MEDICAID.claim_trend": T["MEDICAID"]["annual_claim_trend"],
           "MEDICAID.annual_disenrollment": 1 - (1 - T["MEDICAID"]["monthly_disenrollment"]) ** 12}
    for lob in LOBS:
        gen[f"{lob}.claim_payment_pattern"] = T["claim_payment_lag_probs"]
    close = lambda x, y: (np.allclose(x, y, rtol=0.02) if np.ndim(x) and np.shape(x) == np.shape(y) else
                          (not np.ndim(x) and not np.ndim(y) and abs(x - y) <= 0.02 * abs(y)))
    def get(k):
        node = A
        for k_ in k.split("."):
            node = node[k_]
        return node
    want = {k for k, v in gen.items() if close(get(k), v)}
    have = {e["key"] for e in reg if e.get("synthetic_link") == "equals_generator"}
    assert have == want, (sorted(have ^ want))
    derived = {e["key"] for e in reg if e.get("synthetic_link") == "derived"}
    assert derived == {"MEDSUPP.model_points.bands", "MEDSUPP.model_points.mix*", "DENTAL_VISION.benefit_categories*",
                       "MEDICAID.rate_cells.member_share"}
    doc = (ROOT / "docs" / "ASSUMPTION_BASIS.md").read_text()
    assert "## Values linked to the synthetic book" in doc and all(f"`{k}`" in doc for k in have | derived)


# ---------------------------------------------------------------- v4.1 DAC tax and rating bases
def test_dac_tax_amortization_deducts_exactly_what_was_capitalized_and_is_a_timing_cost():
    p = eng.project_medsupp(A["MEDSUPP"], G)
    pt = eng.profit_test("MEDSUPP", p, A["MEDSUPP"], G)
    d = pt["dac"]
    assert abs(d["rate"] - 0.092) < 1e-12
    assert np.isclose(np.sum(d["capitalized"]), np.sum(d["amortized"]), rtol=1e-12)          # fully deducted in the end
    assert np.isclose(np.sum(d["capitalized"]), 0.092 * p["premium"].sum(), rtol=1e-12)
    assert abs(np.sum(d["extra_tax"])) < 1e-6                                                 # undiscounted: no net tax
    assert float(d["pv"]) > 0                                                                 # discounted: a cost
    assert abs(sum(eng.DAC_WEIGHTS) - 1.0) < 1e-12 and len(eng.DAC_WEIGHTS) == 16             # 180 months, half-year convention
    off = eng.profit_test("MEDSUPP", p, A["MEDSUPP"], G, dict(dac_tax_rate=0.0))
    assert np.isclose(float(off["de_npv"]) - float(pt["de_npv"]), float(d["pv"]), rtol=1e-12)


def test_rating_bases_are_actuarially_equivalent():
    rb = eng.medsupp_rating_bases()
    port = rb[rb.band == "PORTFOLIO"].set_index("basis")
    assert np.allclose(port["pv_loss_ratio"], port.loc["attained_age", "pv_loss_ratio"], rtol=1e-6)
    cells = rb[rb.band != "PORTFOLIO"]
    a_ = cells[cells.basis == "attained_age"].set_index(["band", "plan"])
    i_ = cells[cells.basis == "issue_age"].set_index(["band", "plan"])
    assert np.allclose(i_.loc[(slice(None), "G"), "pv_loss_ratio"], a_.loc[(slice(None), "G"), "pv_loss_ratio"], rtol=1e-6)
    base = a_.loc[("65", "G")]
    assert abs(base.cash_npv_per_1000 - eng.npv("MEDSUPP", A["MEDSUPP"], G)) < 1e-6
    com = cells[cells.basis == "community"]
    assert com.groupby("plan")["year1_premium"].nunique().eq(1).all()                         # one premium for every age


def test_breakeven_reports_which_side_of_breakeven_the_base_is_on():
    """v4.1: headroom is a signed direction; the base NPV and position make a shortfall explicit."""
    b = eng.breakeven()
    s = {lob: (eng.npv(lob, A[lob], G), eng.de_npv(lob, A[lob], G)) for lob in LOBS}
    for r in b.itertuples():
        want = s[r.lob][0 if r.measure == "cash" else 1]
        assert abs(r.base_npv - want) < 0.01
        assert r.position == ("above break-even" if want > 0 else "below break-even")


def test_drift_monitor_alerts_every_line_below_de_breakeven():
    """v4.1: a negative base DE NPV is ONE ALERT however far from break-even, including a line where no
    driver reaches break-even inside its bracket; a profitable line close to break-even is a WATCH."""
    dm = _load("drift_v41", PY / "12_drift_monitoring.py")
    rows = [  # lob, driver, measure, base_level, breakeven, headroom, base_npv
        ("A", "Claim trend", "de", 0.065, 0.0744, 0.0094, 400.0),          # profitable, thin headroom -> WATCH
        ("A", "Claim trend", "cash", 0.065, 0.0773, 0.0123, 700.0),
        ("B", "Claim trend", "de", 0.035, -0.0036, -0.0386, -40.0),        # below, far from break-even -> ALERT
        ("C", "Claim trend", "de", 0.045, None, None, -9e6),               # below, no break-even in bracket -> ALERT
        ("C", "Lapse multiplier", "de", 1.0, None, None, -9e6),
        ("D", "Claim trend", "de", 0.045, 0.09, 0.045, 50.0)]              # profitable, wide headroom -> nothing
    b = pd.DataFrame(rows, columns=["lob", "driver", "measure", "base_level", "breakeven", "headroom", "base_npv"])
    out = pd.DataFrame(dm.breakeven_alerts(b, {"Claim trend": 0.01}))
    assert sorted(out[out.level == "ALERT"].lob) == ["B", "C"]
    assert list(out[out.level == "WATCH"].lob) == ["A"]
    assert "no single driver" in out[out.lob == "C"].detail.iloc[0]
    # and on the shipped engine: exactly the lines with negative base DE NPV are ALERTs
    be = eng.breakeven()
    alerts = pd.DataFrame(dm.breakeven_alerts(be, dm.RULES["breakeven_headroom"]))
    assert set(alerts[alerts.level == "ALERT"].lob) == {lob for lob in LOBS if eng.de_npv(lob, A[lob], G) < 0}


# ---------------------------------------------------------------- v4.2 risk-based pricing solve
@pytest.fixture(scope="module")
def pricing():
    return eng.pricing_solve()


def test_pricing_premium_lines_meet_the_risk_tolerance(pricing):
    lines, _ = pricing
    tol = eng.PRICING["risk_tolerance"]
    for lob in ("MEDSUPP", "DENTAL_VISION"):
        r = lines[(lines.lob == lob) & (lines.item == "Premium (risk-based target)")].iloc[0]
        assert r.p_de_negative_recommended <= tol + 1e-12
        assert r.recommended >= r.required_minimum - 1e-9 and r.recommended >= r.current - 1e-9   # never cut a price
        assert abs(round(r.recommended / 12, 2) * 12 - r.recommended) < 1e-9                       # whole cents a month
        # at the solved minimum the tolerance-quantile of DE NPV is zero (the definition of the target)
        de = eng._mc_de(lob, lambda AA, v=r.required_minimum: AA[lob].__setitem__("annual_premium_year1", v))
        assert abs(np.quantile(de, tol)) < 1e-3 * abs(r.de_npv_recommended) + 50
        # deterministic results at the recommended premium are the engine's
        a = copy.deepcopy(A[lob]); a["annual_premium_year1"] = r.recommended
        assert abs(eng.de_npv(lob, a, G) - r.de_npv_recommended) < 0.01
        if r.constraint.startswith("PV loss ratio"):
            assert r.pv_loss_ratio_recommended >= eng.PRICING["lines"][lob]["min_pv_loss_ratio"]
    ms = lines[(lines.lob == "MEDSUPP") & (lines.item == "Premium (risk-based target)")].iloc[0]
    dv = lines[(lines.lob == "DENTAL_VISION") & (lines.item == "Premium (risk-based target)")].iloc[0]
    assert ms.status.startswith("MEETS") and ms.required_minimum < ms.current       # profitable as priced
    assert dv.status == "REPRICE" and dv.recommended > dv.current and dv.de_npv_recommended > 0


def test_pricing_medicaid_bid_recommendation_follows_the_lever_feasibility(pricing):
    lines, _ = pricing
    md = lines[lines.lob == "MEDICAID"]
    levers = md[md.item.str.startswith("Required")]
    assert len(levers) == len(eng.PRICING["lines"]["MEDICAID"]["levers"])
    feasible = (levers.status == "ACHIEVABLE").any()
    rec = md[md.item == "Bid recommendation"].iloc[0].status
    assert rec.startswith("BID") if feasible else rec.startswith("DO NOT BID")
    for r in levers.itertuples():                    # each required level really meets the target
        if r.required_minimum is not None and not pd.isna(r.required_minimum):
            key = [k for k, L in eng.PRICING["lines"]["MEDICAID"]["levers"].items() if L["label"] == r.lever][0]
            de = eng._mc_de("MEDICAID", lambda AA, k=key, v=r.required_minimum: AA["MEDICAID"].__setitem__(k, v))
            assert abs(np.quantile(de, eng.PRICING["risk_tolerance"])) < 5e3


def test_pricing_model_points_carry_the_base_margin_or_hit_the_loss_ratio_cap(pricing):
    lines, cells = pricing
    ms = lines[(lines.lob == "MEDSUPP") & (lines.item == "Premium (risk-based target)")].iloc[0]
    c = cells[cells.band != "PORTFOLIO"]
    min_lr = eng.PRICING["lines"]["MEDSUPP"]["min_pv_loss_ratio"]
    assert c.min_loss_ratio_met.all()
    capped = c.status.str.startswith("CAPPED")
    up = ~capped & (c.change > 1e-9)
    assert np.allclose(c[up].de_margin_required, ms.de_margin_recommended, atol=1e-8)     # repriced to the margin
    assert (c[~capped].de_margin_required >= ms.de_margin_recommended - 1e-8).all()      # no cell left below it
    assert (c.change >= -1e-12).all()                                                     # never cut a price
    assert np.allclose(c[capped].pv_loss_ratio_required, min_lr, atol=1e-8)
    assert (c[capped].de_margin_required < ms.de_margin_recommended).all()
    port = cells[cells.band == "PORTFOLIO"].iloc[0]
    assert abs(port.de_npv_per_1000_required - (c.mix * c.de_npv_per_1000_required).sum()) < 1e-6
    risk = lines[lines.item == "Portfolio risk check (all model points)"].iloc[0]
    assert abs(risk.p_de_negative_recommended - port.p_de_negative_required) < 1e-12
    assert risk.status.startswith("MEETS") == (port.p_de_negative_required <= eng.PRICING["risk_tolerance"])

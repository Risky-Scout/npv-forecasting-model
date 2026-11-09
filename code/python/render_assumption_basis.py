"""Renders docs/ASSUMPTION_BASIS.md from config/assumption_basis.json (the register is the source of
truth; tests/test_v4_model.py checks it covers every assumption). Usage: python render_assumption_basis.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R = json.loads((ROOT / "config" / "assumption_basis.json").read_text())
NAMES = {"P": "Public source", "S": "Statute / regulation", "D": "Derived (internal consistency)", "J": "Judgment",
         "E": "Company experience", "M": "Model setting"}


def fmt(v):
    if v is None:
        return "(block)"
    if isinstance(v, float) and abs(v) < 1:
        return f"{v:.4g}"
    s = json.dumps(v)
    return s if len(s) < 60 else s[:57] + "..."


lines = ["# Assumption basis (v4.2)", "",
         "Generated from `config/assumption_basis.json` (written by `code/python/write_assumption_basis.py`) by `code/python/render_assumption_basis.py`. Do not edit by hand.", "",
         "## Policy", "",
         "- **Standards.** The ASOPs prescribe a process, not values. Each assumption is selected from data relevant to the subject (ASOP 25 §3.3, ASOP 8 §3.4, ASOP 49 for Medicaid) and documented with its source. Judgment values are disclosed (ASOP 41) and sensitivity-tested (ASOP 56 §3.6.1).",
         "- **No Humana data.** The shipped values rest on public sources: CMS / Medicare Trustees, CMS NHE, MACPAC, SSA, NAIC, federal regulation, and industry studies (Gen Re, Milliman, Segal, ADA HPI).",
         "- **Synthetic experience gets no credibility in the NPV pricing assumptions.** The synthetic practice book is not relevant experience for a Humana block, so its trend, lapse and cost indications are not used for pricing. They are reported by `03b` and dispositioned in the approval. (The in-force forecast of the existing book uses that book's own experience by design: that is its job.) When Humana experience is loaded, the same credibility blend weights it against these public complements.",
         "- **Values linked to the synthetic book are disclosed** (section below). A few distribution inputs (sales mix, category shares, Medicaid cell mix) are read off the synthetic book as placeholders, and several values equal the synthetic generator's own and are kept because a public figure (status P) or a judgment range (status J) supports them. For those the source is a reasonableness check, not the derivation. The test suite recomputes this list from the generator's file.",
         "- **Status codes:** " + "; ".join(f"**{k}** = {v}" for k, v in NAMES.items()) + ".", ""]
counts = {}
for e in R["entries"]:
    counts[e["status"]] = counts.get(e["status"], 0) + 1
lines += ["Entries by status: " + ", ".join(f"{NAMES[k]} {v}" for k, v in sorted(counts.items())), ""]
sections = [("global", "Global and profit test"), ("MEDSUPP", "Medicare Supplement"), ("DENTAL_VISION", "Dental & Vision"),
            ("MEDICAID", "Medicaid"), ("monte_carlo", "Monte Carlo")]
for pre, title in sections:
    lines += [f"## {title}", "", "| Assumption | Value | Status | Basis | Sources | ASOP | Sensitivity |", "|---|---|---|---|---|---|---|"]
    for e in R["entries"]:
        if not e["key"].startswith(pre):
            continue
        src = "<br>".join(f"[{s['title']}]({s['url']}) ({s['date']}): {s['figure']}" for s in e["sources"]) or "—"
        asop = "<br>".join(e["asop"]) or "—"
        lines.append(f"| `{e['key'].replace(pre + '.', '')}` | {'(block)' if e['key'].endswith('*') else fmt(e['value'])} | {e['status']} | {e['basis']} | {src} | {asop} | {e['sensitivity'] or '—'} |")
    lines.append("")
LINK = {"derived": "read off the synthetic book (placeholder for a Humana input)",
        "equals_generator": "equals the synthetic generator's value; kept because the public figure supports it"}
linked = [e for e in R["entries"] if e.get("synthetic_link")]
lines += ["## Values linked to the synthetic book", "",
          f"{len(linked)} entries. What this means for validation: backtest Test B (estimation error against the generator's truth) "
          "can only measure error in values that differ from the generator's, chiefly trend; and the forecast's accuracy results "
          "use the generator's claim-cost curves (`config/claim_cost_curves.json`) and payment pattern, i.e. known curves.", "",
          "| Assumption | Status | Link | Effect on the headline results |", "|---|---|---|---|"]
EFFECT = {"MEDSUPP.model_points.bands": "none on the Plan G cell; drives the portfolio and rating-basis results", "MEDSUPP.model_points.mix*": "none on the Plan G cell; drives the portfolio and rating-basis results",
          "DENTAL_VISION.benefit_categories*": "none at base; waiting-period options only", "MEDICAID.rate_cells.member_share": "none at equal churn by cell; starting point of the churn-by-cell finding (swept)"}
for e in linked:
    lk = LINK[e["synthetic_link"]]
    if e["synthetic_link"] == "equals_generator" and e["status"] == "J":
        lk = "equals the synthetic generator's value; judgment (no public figure), run across its range"
    lines.append(f"| `{e['key']}` | {e['status']} | {lk} | {EFFECT.get(e['key'], 'direct: see the tornado and judgment sensitivity')} |")
lines.append("")
lines += ["## Material findings from the basis review", "",
          "- **Commission broke the regulatory cap.** Med Supp commission of 22% first year against 10% renewal exceeded the model regulation's 200% first-year cap. Changed to 20% / 10%.",
          "- **Mortality was too light.** q65 = 0.0085 was 59% of SSA unisex. Now 75% of SSA (judgment for an underwritten population), with growth fitted to SSA.",
          "- **The Med Supp rate increase is now consistent with trend and aging,** so the ultimate loss ratio holds level. Industry increases of 10.6–11.7% are the upside test.",
          "- **The Medicaid rate update now equals trend.** A permanent rate-below-trend gap is not a defensible expectation under ASOP 49 §2.1.",
          "- **The Medicaid cost ratio, admin and tax now match industry results.** The old set implied a 2.8% margin against industry results of 0% to −0.6%.",
          "- **Georgia, Arizona, Florida and Missouri prohibit attained-age Med Supp rating.** The engine reprices the portfolio on issue-age and community bases at the same lifetime loss ratio (`output/npv/medsupp_rating_bases.csv`, v4.1).",
          "- **The DAC tax applies to Med Supp** (IRC §848, 9.2% of net premiums, 180-month amortization). It is modelled in v4.1 and costs about a fifth of Med Supp DE before it; the classification by line is for Corporate Tax to confirm.",
          "- **Medicaid contracts withhold capitation for quality.** v4.1 carries a 2% withhold with 85% expected earn-back (Louisiana, CMS) instead of neutral terms.",
          "- **D&V lapse is 30% flat** from filed individual dental memoranda, not the synthetic book's duration curve.",
          "- **Values with no public basis stay labelled judgment** and are tested across their stated ranges.", ""]
(ROOT / "docs" / "ASSUMPTION_BASIS.md").write_text("\n".join(lines) + "\n")
print("written docs/ASSUMPTION_BASIS.md", counts)

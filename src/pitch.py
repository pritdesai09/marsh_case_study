"""generateMarketingPitch: turn a company profile + selected policies into a 5-slide pitch.

How grounding works:
  1. Scoring is plain code: each workforce risk is matched to verified policy facts, weighted by
     whether the benefit is included, optional, an add-on or plan-dependent. Same input, same answer.
  2. The AI only writes sentences. Every policy sentence must cite fact IDs from the list it is
     given; code drops any ID it invents and flags sentences with no valid citation.
  3. The comparison table and "Why Marsh" slide are built by code, not the AI.
  4. If a cited fact is optional / add-on / plan-dependent, code makes sure the sentence says so.
If the AI is unavailable, a template writer produces a plainer pitch from the same facts.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from src import config, fact_store, llm
from src.profile import RISK_TAXONOMY

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- scoring

# Which benefits answer each workforce risk.
#   strong: the benefit clearly addresses the risk (counts fully)
#   weak:   loosely related, e.g. general e-consultations for stress (counts 60%)
# Keywords are matched in the benefit NAME first; categories are a fallback.
RISK_NEEDS = {
    "sedentary_lifestyle": {"strong": ["chronic", "diabet", "health check", "check-up", "checkup", "healthreturns"],
                            "strong_cats": ["chronic_care", "wellness_checkup"],
                            "weak": ["consult", "wellness", "fitness"], "weak_cats": ["opd_consultation"]},
    "chronic_conditions": {"strong": ["chronic", "day 1", "pre-existing", "ped"], "strong_cats": ["chronic_care"],
                           "weak": ["consult"], "weak_cats": ["opd_consultation"]},
    "young_families": {"strong": ["maternity", "newborn", "ivf", "parenthood"], "strong_cats": ["maternity"],
                       "weak": ["family floater", "floater"], "weak_cats": []},
    "dependent_parents": {"strong": ["entry age", "parents", "pre-existing", "ped"], "strong_cats": [],
                          "weak": ["exit age", "senior"], "weak_cats": []},
    "occupational_injury": {"strong": ["accident", "ambulance", "trauma"], "strong_cats": ["personal_accident", "ambulance"],
                            "weak": ["restore", "recharge", "reload", "reassure"], "weak_cats": ["hospitalisation", "restore_recharge"]},
    "shift_work_fatigue": {"strong": ["health check", "check-up", "checkup", "chronic"], "strong_cats": ["wellness_checkup", "chronic_care"],
                           "weak": ["wellness", "hospital cash", "daily cash"], "weak_cats": ["daily_cash"]},
    "mental_health_stress": {"strong": ["mental", "psychiatr", "counsel"], "strong_cats": [],
                             "weak": ["consult", "wellness"], "weak_cats": ["opd_consultation"]},
    "frequent_travel": {"strong": ["abroad", "global", "international", "worldwide", "air ambulance"],
                        "strong_cats": ["international"], "weak": ["ambulance"], "weak_cats": []},
    "dispersed_workforce": {"strong": ["cashless", "network", "e-consult", "domiciliary", "home care"], "strong_cats": [],
                            "weak": ["ambulance"], "weak_cats": ["ambulance"]},
    "medical_inflation": {"strong": ["inflation", "cpi", "credit", "infinite", "secure benefit", "bonus", "booster",
                                     "restore", "recharge", "reload", "reassure"],
                          "strong_cats": ["bonus", "restore_recharge"], "weak": [], "weak_cats": ["sum_insured"]},
}
MATCH_QUALITY = {"strong": 1.0, "weak": 0.6}
# A waiting period is a restriction, so it only counts when the brochure shortens or removes it
SHORT_WAIT = re.compile(r"zero|day 1|no waiting|waived|reduced|modified to|1 or 2 year", re.IGNORECASE)

COVERAGE_WEIGHT = {"base": 1.0, "plan_dependent": 0.6, "optional": 0.5, "add_on": 0.3}
RELEVANCE_WEIGHT = {"high": 2, "medium": 1}
QUALIFIER = {
    "optional": "optional, extra premium",
    "add_on": "via a separate add-on",
    "plan_dependent": "on select plans",
}
QUALIFIER_WORDS = {
    "optional": ("optional", "extra premium", "additional premium"),
    "add_on": ("add-on", "add on", "rider"),
    "plan_dependent": ("select plan", "certain plan", "some plan", "plan-dependent", "on select", "variant"),
}


def usable_facts(codes: list[str]) -> list[dict]:
    """Only facts that passed verification (automatic or human) may appear in a pitch."""
    return [f for f in fact_store.load_facts(codes) if f.get("status") in ("auto_verified", "human_verified")]


def _mentions(text: str, keywords: list[str]) -> bool:
    """Keyword at the start of a word: 'parents' matches 'your parents' but not 'Parenthood'."""
    return any(re.search(r"(?<![a-z])" + re.escape(k), text) for k in keywords)


def _match_quality(risk_id: str, f: dict) -> str | None:
    """'strong', 'weak' or None: how directly this fact answers the risk."""
    need = RISK_NEEDS.get(risk_id)
    if not need:
        return None
    name = f["benefit"].lower()
    text = f"{f['benefit']} {f['value']}".lower()
    if f["category"] == "waiting_period" and not SHORT_WAIT.search(f"{text} {f.get('quote', '')}".lower()):
        return None
    if _mentions(name, need["strong"]) or f["category"] in need["strong_cats"]:
        return "strong"
    if _mentions(text, need["strong"]) or _mentions(name, need["weak"]) or f["category"] in need["weak_cats"]:
        return "weak"
    return None


def _fact_strength(risk_id: str, f: dict) -> float:
    quality = _match_quality(risk_id, f)
    return COVERAGE_WEIGHT.get(f["coverage_type"], 0) * MATCH_QUALITY[quality] if quality else 0.0


def facts_for_risk(risk_id: str, facts: list[dict]) -> list[dict]:
    """Facts that answer the risk, strongest first."""
    scored = [(f, _fact_strength(risk_id, f)) for f in facts]
    return [f for f, w in sorted(scored, key=lambda x: -x[1]) if w > 0]


def score_policies(profile: dict, codes: list[str]) -> list[dict]:
    """Rank the selected policies against the company's risks. Returns best first, with the working shown.

    Per risk: the strongest benefit counts fully and the next adds a quarter, capped at 1.0.
    So 100/100 needs an included, directly relevant benefit for every risk; weak or
    optional matches can't add up to a perfect score.
    """
    facts = usable_facts(codes)
    risks = profile.get("risks") or []
    total_weight = sum(RELEVANCE_WEIGHT.get(r["relevance"], 1) for r in risks) or 1
    ranking = []
    for code in codes:
        policy_facts = [f for f in facts if f["policy"] == code]
        breakdown, points = [], 0.0
        for r in risks:
            matched = facts_for_risk(r["id"], policy_facts)
            weights = [_fact_strength(r["id"], f) for f in matched]
            strength = min(1.0, (weights[0] + 0.25 * weights[1]) if len(weights) > 1 else (weights[0] if weights else 0.0))
            points += RELEVANCE_WEIGHT.get(r["relevance"], 1) * strength
            breakdown.append({
                "risk_id": r["id"], "risk": r["label"], "relevance": r["relevance"],
                "score": round(strength * 100),
                "facts": [{"fact_id": f["fact_id"], "benefit": f["benefit"], "value": f["value"],
                           "coverage_type": f["coverage_type"], "page": f["page"],
                           "match": _match_quality(r["id"], f)} for f in matched[:3]],
            })
        ranking.append({
            "code": code,
            "name": config.POLICIES[code]["name"],
            "insurer": config.POLICIES[code]["insurer"],
            "score": round(100 * points / total_weight),
            "included_matches": sum(1 for b in breakdown for f in b["facts"] if f["coverage_type"] == "base"),
            "gaps": [b["risk"] for b in breakdown if not b["facts"]],
            "breakdown": breakdown,
        })
    ranking.sort(key=lambda p: (-p["score"], -p["included_matches"], p["code"]))
    return ranking


# ---------------------------------------------------------------- comparison table (code-built)

# (row label, words the benefit NAME must contain, words that rule a fact out)
COMPARISON_ROWS = [
    ("Room rent", ["room rent", "room category", "room"], ["cash", "shared room"]),
    ("Restore / recharge", ["restore", "recharge", "reload", "reassure", "reinstat"], []),
    ("Sum insured growth", ["bonus", "credit", "infinite", "booster", "inflation", "cpi"], []),
    ("Air ambulance", ["air ambulance", "air"], ["road"]),
    ("Maternity", ["maternity", "parenthood"], []),
    ("Chronic conditions", ["chronic"], []),
    ("Pre-existing disease wait", ["pre-existing", "ped"], ["modification", "modify"]),
]
CELL_MAX_CHARS = 75  # the slide shows a shortened value; the full text stays in the claim for the audit


def _cell_fact(policy_facts: list[dict], include: list[str], exclude: list[str]) -> dict | None:
    """Best fact for a comparison cell, matched on the benefit NAME (categories can be wrong)."""
    matches = [f for f in policy_facts
               if _mentions(f["benefit"].lower(), include) and not _mentions(f["benefit"].lower(), exclude)]
    if not matches:
        return None
    # Included first, then the shortest (most table-friendly) value
    return sorted(matches, key=lambda f: (-COVERAGE_WEIGHT.get(f["coverage_type"], 0), len(f["value"])))[0]


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def build_comparison(ranking: list[dict], facts: list[dict]) -> dict:
    columns = [{"code": p["code"], "name": p["name"], "score": p["score"]} for p in ranking]
    rows = []
    for label, include, exclude in COMPARISON_ROWS:
        cells = []
        for p in ranking:
            f = _cell_fact([x for x in facts if x["policy"] == p["code"]], include, exclude)
            if f:
                qualifier = QUALIFIER.get(f["coverage_type"])
                suffix = f" ({qualifier})" if qualifier else ""
                claim = _claim(f"{f['value']}{suffix}", "policy", fact_ids=[f["fact_id"]], facts=facts)
                claim["short_text"] = _shorten(f["value"], CELL_MAX_CHARS) + suffix  # qualifier is never cut off
                cells.append(claim)
            else:
                cells.append({"text": "Not stated in brochure", "claim_type": "none"})
        if any(c["claim_type"] != "none" for c in cells):
            rows.append({"label": label, "cells": cells})
    return {"columns": columns, "rows": rows}


# ---------------------------------------------------------------- claims

def _claim(text: str, claim_type: str, fact_ids=None, profile_fields=None, facts=None, profile=None) -> dict:
    """One checkable statement on a slide, with where it came from."""
    by_id = {f["fact_id"]: f for f in (facts or [])}
    sources = []
    for fid in fact_ids or []:
        f = by_id.get(fid)
        if f:
            sources.append({"label": f"{config.POLICIES[f['policy']]['insurer']} brochure p.{f['page']}",
                            "fact_id": fid, "quote": f["quote"]})
    for key in profile_fields or []:
        field = (profile or {}).get("fields", {}).get(key)
        if field and field.get("basis") == "sourced" and not any(s.get("url") == field.get("source_url") for s in sources):
            sources.append({"label": field["source"], "url": field.get("source_url"), "field": key})
    return {"text": text.strip(), "claim_type": claim_type, "fact_ids": list(fact_ids or []),
            "profile_fields": list(profile_fields or []), "sources": sources}


def ensure_qualifiers(text: str, cited: list[dict]) -> str:
    """If a cited benefit costs extra or depends on the plan, the sentence must say so. Add it if missing."""
    for f in cited:
        kind = f["coverage_type"]
        if kind in QUALIFIER and not any(w in text.lower() for w in QUALIFIER_WORDS[kind]):
            text = f"{text.rstrip('.')} ({f['benefit']}: {QUALIFIER[kind]})."
    return text


# ---------------------------------------------------------------- AI writer

MARSH_POINTS = [
    ("Independent advice", "We compare plans across insurers so your people get the best fit, not one insurer's product."),
    ("Claims advocacy", "Our team supports employees through claims and cashless approvals."),
    ("Benchmarking", "We benchmark your benefits against industry peers to keep them competitive."),
    ("Verified recommendations", "Every benefit in this deck is traced to the insurer's own brochure and page."),
]
MARSH_SOURCE = "Marsh messaging (confirm wording)"

PITCH_SYSTEM = """You write concise, factual B2B insurance pitch slides for Marsh, an insurance broker in India.
Hard rules:
- Use ONLY the policy facts provided. Every policy statement must cite their fact_ids.
- Copy amounts, limits and durations exactly as given in the fact's value. Never round or convert.
- If a fact's coverage is optional, add_on or plan_dependent, the sentence must say so
  ("optional, at extra premium", "via an add-on", "on select plans").
- Company statements may only use the company facts provided, and must cite their field names.
  Workforce and risk statements are estimates: say "likely" and cite no field.
- These are individual/family retail health plans. Position them as voluntary top-up cover that
  employees can buy for themselves and their families, alongside the employer's group policy.
- Each bullet max 25 words. No marketing superlatives about the insurer."""

PITCH_PROMPT = """Company: {company}
Company facts (cite by field name):
{company_facts}
Likely workforce (estimate): {workforce}

Workforce risks, most important first:
{risks}

Recommended policy: {recommended} (fit score {score}/100). Its matching facts per risk:
{recommended_facts}

Other policies considered: {others}

Return JSON exactly in this shape:
{{
  "overview": [{{"text": "...", "profile_fields": ["industry"]}}],
  "risk_benefits": [{{"risk_id": "<risk id>", "text": "<how the recommended policy answers this risk>", "fact_ids": ["..."]}}],
  "recommendation": [{{"text": "...", "fact_ids": ["..."]}}]
}}
- overview: 3 bullets about the company and why health cover matters for its people.
- risk_benefits: one per risk listed above that has facts (max 4), for the RECOMMENDED policy only.
- recommendation: 3 bullets on why {recommended} fits best, citing its facts.
"""


def _fact_line(f: dict) -> str:
    extra = f" [{f['coverage_type']}]" if f["coverage_type"] != "base" else ""
    cond = f" (conditions: {f['conditions']})" if f.get("conditions") else ""
    return f"  - {f['fact_id']}: {f['benefit']} = {f['value']}{extra}{cond}"


def _write_with_ai(profile: dict, ranking: list[dict], facts: list[dict]) -> dict:
    best = ranking[0]
    by_id = {f["fact_id"]: f for f in facts}
    company_facts = "\n".join(f"  - {k}: {v['display']}" for k, v in profile["fields"].items()
                              if v.get("basis") == "sourced" and k != "summary") or "  - (none verified)"
    rec_lines = []
    for b in best["breakdown"]:
        if b["facts"]:
            rec_lines.append(f"  {b['risk_id']}:")
            rec_lines += [_fact_line(by_id[x["fact_id"]]) for x in b["facts"] if x["fact_id"] in by_id]
    prompt = PITCH_PROMPT.format(
        company=profile["resolved_name"],
        company_facts=company_facts,
        workforce=profile["workforce_profile"]["value"],
        risks="\n".join(f"  - {r['id']} ({r['relevance']}): {r['label']}" for r in profile["risks"]),
        recommended=best["name"], score=best["score"],
        recommended_facts="\n".join(rec_lines) or "  (no matching facts)",
        others=", ".join(f"{p['name']} ({p['score']}/100)" for p in ranking[1:]) or "none",
    )
    return llm.generate_json(prompt, strong=True, system=PITCH_SYSTEM, temperature=0.3, max_tokens=4096)


def _write_with_templates(profile: dict, ranking: list[dict], facts: list[dict]) -> dict:
    """No-AI fallback: plain sentences built directly from the facts."""
    best, fields = ranking[0], profile["fields"]
    overview = []
    if "industry" in fields:
        overview.append({"text": f"{profile['resolved_name']} operates in {fields['industry']['display']}.",
                         "profile_fields": ["industry"]})
    if "employees" in fields:
        overview.append({"text": f"Employees: {fields['employees']['display']}.", "profile_fields": ["employees"]})
    if "headquarters" in fields:
        overview.append({"text": f"Headquartered in {fields['headquarters']['display']}.", "profile_fields": ["headquarters"]})
    overview.append({"text": "A large workforce likely includes many employees who also insure spouses, children and parents.",
                     "profile_fields": []})
    risk_benefits = [{"risk_id": b["risk_id"],
                      "text": f"{b['facts'][0]['benefit']}: {b['facts'][0]['value']}.",
                      "fact_ids": [b["facts"][0]["fact_id"]]}
                     for b in best["breakdown"] if b["facts"]][:4]
    top = sorted({x["fact_id"]: x for b in best["breakdown"] for x in b["facts"]}.values(),
                 key=lambda x: -COVERAGE_WEIGHT.get(x["coverage_type"], 0))[:3]
    recommendation = [{"text": f"{x['benefit']}: {x['value']}.", "fact_ids": [x["fact_id"]]} for x in top]
    return {"overview": overview, "risk_benefits": risk_benefits, "recommendation": recommendation}


# ---------------------------------------------------------------- assembly

class PitchError(ValueError):
    """Bad request (no policies, unknown policy, no profile). Message is safe to show users."""


def _clean_bullets(items, facts, profile, allowed_fact_ids, allowed_fields, max_items):
    """Keep only valid citations; flag policy-sounding bullets that end up uncited."""
    by_id = {f["fact_id"]: f for f in facts}
    claims = []
    for item in (items or [])[:max_items]:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        cited = [str(i) for i in item.get("fact_ids", []) or []]
        fact_ids = [i for i in cited if i in allowed_fact_ids]
        dropped = [i for i in cited if i not in allowed_fact_ids]
        fields = [k for k in item.get("profile_fields", []) or [] if k in allowed_fields]
        text = ensure_qualifiers(str(item["text"]), [by_id[i] for i in fact_ids])
        if fact_ids or dropped:
            # The AI meant this as a policy statement. If none of its citations survived,
            # it stays a policy claim with no evidence, so the audit fails it rather than
            # letting it pass as a harmless "assumption".
            kind = "policy"
        elif fields:
            kind = "company"
        else:
            kind = "assumption"
        claim = _claim(text, kind, fact_ids, fields, facts, profile)
        if dropped:
            claim["dropped_citations"] = dropped  # invented IDs, or facts from a different policy
        if kind == "policy" and not fact_ids:
            claim["uncited"] = True
        claims.append(claim)
    return claims


def generate_marketing_pitch(profile: dict, policy_codes: list[str], use_ai: bool = True) -> dict:
    if not profile or not profile.get("resolved_name") or "risks" not in profile:
        raise PitchError("Research a company first.")
    codes = list(dict.fromkeys(c.strip().upper() for c in policy_codes or [] if c and c.strip()))
    if not codes:
        raise PitchError("Select at least one policy.")
    unknown = [c for c in codes if c not in config.POLICIES]
    if unknown:
        raise PitchError(f"Unknown policy: {', '.join(unknown)}")
    facts = usable_facts(codes)
    missing = [c for c in codes if not any(f["policy"] == c for f in facts)]
    if missing:
        raise PitchError(f"No verified facts for {', '.join(missing)}. Build the fact store first.")

    ranking = score_policies(profile, codes)
    best = ranking[0]
    best_fact_ids = {x["fact_id"] for b in best["breakdown"] for x in b["facts"]}
    allowed_fields = {k for k, v in profile.get("fields", {}).items() if v.get("basis") == "sourced"}

    warnings, written, generated_by = [], None, "templates"
    if use_ai and not config.DEMO_MODE:
        try:
            written = _write_with_ai(profile, ranking, facts)
            generated_by = llm.last_model_used or config.GEMINI_MODEL_STRONG
        except llm.LLMError as e:
            log.warning("AI pitch writing failed: %s", e)
            warnings.append("The AI writer was unavailable, so slide text was built from templates.")
    if not isinstance(written, dict):
        written = _write_with_templates(profile, ranking, facts)

    overview = _clean_bullets(written.get("overview"), facts, profile, set(), allowed_fields, 4)
    risk_rows = []
    risk_labels = {r["id"]: r for r in profile["risks"]}
    for item in (written.get("risk_benefits") or [])[:4]:
        rid = item.get("risk_id") if isinstance(item, dict) else None
        if rid not in risk_labels:
            continue
        claims = _clean_bullets([item], facts, profile, best_fact_ids, set(), 1)
        if claims:
            risk_rows.append({"risk_id": rid, "risk": risk_labels[rid]["label"],
                              "relevance": risk_labels[rid]["relevance"], "claim": claims[0]})
    recommendation = _clean_bullets(written.get("recommendation"), facts, profile, best_fact_ids, set(), 4)

    name = profile["resolved_name"]
    slides = [
        {"n": 1, "type": "overview", "title": f"{name}: why health cover matters",
         "facts": {k: profile["fields"][k]["display"] for k in ("industry", "employees", "headquarters", "founded")
                   if k in profile.get("fields", {})},
         "claims": overview},
        {"n": 2, "type": "why_marsh", "title": "Why Marsh",
         "claims": [{**_claim(f"{title}: {text}", "marsh"), "sources": [{"label": MARSH_SOURCE}]}
                    for title, text in MARSH_POINTS]},
        {"n": 3, "type": "risk_benefits", "title": f"Risks mapped to {best['name']} benefits",
         "rows": risk_rows, "claims": [r["claim"] for r in risk_rows]},
        {"n": 4, "type": "comparison", "title": "How the options compare",
         "table": build_comparison(ranking, facts)},
        {"n": 5, "type": "recommendation", "title": f"Our recommendation: {best['name']}",
         "score": best["score"], "claims": recommendation,
         "disclaimer": ("Positioning: individual/family health plans offered as voluntary top-up cover alongside "
                        "the employer's group policy. Benefits summarised from insurer brochures; policy wordings "
                        "prevail. Premiums and eligibility subject to underwriting.")},
    ]
    slides[3]["claims"] = [c for row in slides[3]["table"]["rows"] for c in row["cells"] if c["claim_type"] != "none"]

    # Give every claim a stable id like S3-2 (used by the audit and the advisor's approve/edit/reject)
    for s in slides:
        for i, c in enumerate(s.get("claims", []), start=1):
            c["id"] = f"S{s['n']}-{i}"

    pitch = {
        "id": uuid.uuid4().hex[:10],
        "company": name,
        "profile": profile,
        "policies": codes,
        "recommended": best["code"],
        "ranking": ranking,
        "slides": slides,
        "generated_by": generated_by,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_pitch(pitch)
    return pitch


def save_pitch(pitch: dict):
    """Keep a copy of every generated pitch (audit trail)."""
    folder = config.OUTPUT_DIR / "pitches"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", pitch["company"])[:40]
    (folder / f"{safe}_{pitch['id']}.json").write_text(json.dumps(pitch, ensure_ascii=False, indent=2), encoding="utf-8")


# Name used in the case study brief
generateMarketingPitch = generate_marketing_pitch
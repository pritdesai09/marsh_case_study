"""generateCompanyProfile: who is the client, and what health risks does their workforce face?

Design rule: facts marked "sourced" come straight from Wikidata/Wikipedia through code,
never through the AI, so a "sourced" badge can't be hallucinated. The AI only writes the
fields marked "assumption" (workforce profile and risks), and must pick risks from a
fixed list so the pitch step can map them to policy benefits.

If the company can't be found, or the AI is unavailable, the profile falls back to
clearly labelled industry-based assumptions, so this step never fails outright.
"""
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache

import requests

from src import config, llm

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
HEADERS = {"User-Agent": "MarshPitchGenerator/0.2 (case study; https://github.com/pritdesai09/marsh_case_study)"}
TIMEOUT = (5, 10)  # seconds: connect, read

# Risks the pitch can address. The AI must choose from these ids (Phase 3 maps them to benefits).
RISK_TAXONOMY = {
    "sedentary_lifestyle": "Desk-based work linked to lifestyle diseases (diabetes, hypertension, obesity)",
    "chronic_conditions": "Employees or dependants with existing chronic illness who need early cover",
    "young_families": "Young workforce starting families: maternity and newborn needs",
    "dependent_parents": "Employees who also cover older parents with pre-existing conditions",
    "occupational_injury": "Physical or industrial work with accident and hospitalisation risk",
    "shift_work_fatigue": "Shift or night work that affects long-term health",
    "mental_health_stress": "High-pressure roles with stress-related health needs",
    "frequent_travel": "Travel or overseas postings that need emergency or abroad cover",
    "dispersed_workforce": "Staff spread across many cities, including smaller towns",
    "medical_inflation": "Rising treatment costs eroding a fixed sum insured over time",
}

# Deterministic fallback when the AI is unavailable: industry keywords -> likely risks.
INDUSTRY_RULES = [
    (r"software|information technology|\bit\b|consult|internet|outsourc|technology",
     ["sedentary_lifestyle", "mental_health_stress", "young_families", "dependent_parents"],
     "Likely a young, urban, desk-based workforce, many with young families and dependent parents."),
    (r"steel|metal|mining|manufactur|automotive|chemical|cement|construction|engineering|oil|energy|power",
     ["occupational_injury", "shift_work_fatigue", "chronic_conditions", "dispersed_workforce"],
     "Likely a mix of plant/site workers and office staff, with shift work and physical risk."),
    (r"bank|financ|insurance|invest|capital",
     ["sedentary_lifestyle", "mental_health_stress", "dispersed_workforce", "dependent_parents"],
     "Likely desk-based staff across a wide branch network, under target-driven pressure."),
    (r"pharma|hospital|health|biotech",
     ["shift_work_fatigue", "mental_health_stress", "chronic_conditions", "young_families"],
     "Likely includes clinical and shift-based staff alongside corporate teams."),
    (r"retail|logistic|transport|airline|aviation|hospitality|hotel|food|e-commerce",
     ["shift_work_fatigue", "occupational_injury", "dispersed_workforce", "young_families"],
     "Likely a large frontline workforce on shifts across many locations."),
]
DEFAULT_RISKS = ["sedentary_lifestyle", "dependent_parents", "medical_inflation"]

COMPANY_WORDS = re.compile(
    r"company|corporation|conglomerate|business|enterprise|firm|manufacturer|bank|insurer|"
    r"multinational|group|limited|ltd|provider|operator|airline|retailer|producer|brand|"
    r"subsidiary|holding|startup|agency|organi[sz]ation", re.IGNORECASE)

CLAIMS_SOURCE = re.compile(r"wikidata|wikipedia|verified|according to", re.IGNORECASE)

CURRENCY_UNITS = {"Q4917": "US$", "Q80524": "₹", "Q4916": "€", "Q25224": "£"}


class ProfileError(ValueError):
    """Bad input (e.g. empty name). Message is safe to show users."""


# ---------------------------------------------------------------- Wikidata / Wikipedia

def _get_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=256)
def search_companies(query: str, limit: int = 6) -> tuple:
    """Wikidata items matching the name, companies first. Returns a tuple of dicts (cached)."""
    data = _get_json(WIKIDATA_API, {
        "action": "wbsearchentities", "search": query, "language": "en", "uselang": "en",
        "type": "item", "limit": 10, "format": "json",
    })
    results = [
        {"id": r["id"], "label": r.get("label", r["id"]), "description": r.get("description", ""),
         "is_company": bool(COMPANY_WORDS.search(r.get("description", "")))}
        for r in data.get("search", [])
    ]
    results.sort(key=lambda r: not r["is_company"])  # stable: companies first, original order kept
    return tuple(results[:limit])


def _get_entities(ids: list[str], props: str) -> dict:
    if not ids:
        return {}
    data = _get_json(WIKIDATA_API, {
        "action": "wbgetentities", "ids": "|".join(ids[:50]), "props": props,
        "languages": "en", "format": "json",
    })
    return data.get("entities", {})


def _best_statement(claims: dict, prop: str):
    """Preferred statement if any, else the most recent by 'point in time' (P585), else the first."""
    statements = [s for s in claims.get(prop, []) if s.get("rank") != "deprecated"
                  and s.get("mainsnak", {}).get("snaktype") == "value"]
    if not statements:
        return None
    preferred = [s for s in statements if s.get("rank") == "preferred"]
    pool = preferred or statements

    def when(s):
        quals = s.get("qualifiers", {}).get("P585", [])
        try:
            return quals[0]["datavalue"]["value"]["time"]
        except (IndexError, KeyError):
            return ""
    return max(pool, key=when)


def _statement_year(statement) -> str:
    try:
        return statement["qualifiers"]["P585"][0]["datavalue"]["value"]["time"][1:5]
    except (KeyError, IndexError, TypeError):
        return ""


def _value(statement):
    return statement["mainsnak"]["datavalue"]["value"] if statement else None


def _human_amount(amount: float) -> str:
    for size, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if amount >= size:
            return f"{amount / size:.1f} {word}"
    return f"{amount:,.0f}"


def fetch_wikidata_facts(qid: str) -> dict:
    """Sourced fields for one company, read directly from Wikidata (no AI involved)."""
    entity = _get_entities([qid], "labels|descriptions|claims|sitelinks").get(qid)
    if not entity or "missing" in entity:
        raise LookupError(f"Wikidata item {qid} not found")

    claims = entity.get("claims", {})
    url = f"https://www.wikidata.org/wiki/{qid}"
    fields, label_ids = {}, set()

    industry = [_value(s) for s in claims.get("P452", []) if s.get("mainsnak", {}).get("snaktype") == "value"]
    hq, country = _value(_best_statement(claims, "P159")), _value(_best_statement(claims, "P17"))
    for item in industry[:3] + [hq, country]:
        if isinstance(item, dict) and "id" in item:
            label_ids.add(item["id"])
    labels = {k: v.get("labels", {}).get("en", {}).get("value", k)
              for k, v in _get_entities(sorted(label_ids), "labels").items()}

    def sourced(value, display=None):
        return {"value": value, "display": display or str(value), "basis": "sourced",
                "source": "Wikidata", "source_url": url}

    if industry:
        names = [labels.get(i["id"], i["id"]) for i in industry[:3] if isinstance(i, dict)]
        fields["industry"] = sourced(names, ", ".join(names))

    employees = _best_statement(claims, "P1128")
    if employees:
        count = int(float(_value(employees)["amount"]))
        year = _statement_year(employees)
        fields["employees"] = sourced(count, f"{count:,}" + (f" (as of {year})" if year else ""))

    if hq:
        fields["headquarters"] = sourced(labels.get(hq["id"], hq["id"]))
    if country:
        fields["country"] = sourced(labels.get(country["id"], country["id"]))

    inception = _value(_best_statement(claims, "P571"))
    if inception:
        fields["founded"] = sourced(inception["time"][1:5])

    revenue = _best_statement(claims, "P2139")
    if revenue:
        amount = float(_value(revenue)["amount"])
        unit_id = _value(revenue).get("unit", "").rsplit("/", 1)[-1]
        currency = CURRENCY_UNITS.get(unit_id, "")
        year = _statement_year(revenue)
        fields["revenue"] = sourced(amount, f"{currency}{_human_amount(amount)}" + (f" ({year})" if year else ""))

    website = _value(_best_statement(claims, "P856"))
    if website:
        fields["website"] = sourced(website)

    return {
        "label": entity.get("labels", {}).get("en", {}).get("value", qid),
        "description": entity.get("descriptions", {}).get("en", {}).get("value", ""),
        "wikidata_url": url,
        "enwiki_title": entity.get("sitelinks", {}).get("enwiki", {}).get("title"),
        "fields": fields,
    }


def fetch_wikipedia_summary(title: str) -> dict | None:
    """First two sentences of the Wikipedia article, verbatim."""
    data = _get_json(WIKIPEDIA_SUMMARY.format(title=requests.utils.quote(title.replace(" ", "_"))))
    extract = (data.get("extract") or "").strip()
    if not extract:
        return None
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", extract)
    return {
        "value": " ".join(sentences[:2]),
        "display": " ".join(sentences[:2]),
        "basis": "sourced",
        "source": "Wikipedia",
        "source_url": data.get("content_urls", {}).get("desktop", {}).get("page",
                      f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"),
    }


# ---------------------------------------------------------------- assumptions (AI or rules)

PROFILE_SYSTEM = """You are an employee-benefits analyst at an insurance broker. You infer the likely
workforce profile and health-insurance risks of a company from verified facts. You never invent facts:
anything you write is an assumption and must say what it is based on."""

PROFILE_PROMPT = """Company: {name}
Verified facts (from Wikidata/Wikipedia):
{facts}

Choose the 3 to 5 most relevant health-insurance risks for this company's employees, ONLY from this list:
{taxonomy}

Return JSON:
{{"industry_guess": "<only if industry is not in the verified facts, else empty string>",
  "workforce_profile": "<2 sentences: likely age mix, roles, locations. Say it is an estimate.>",
  "workforce_reason": "<which verified facts this is based on>",
  "risks": [{{"id": "<risk id from the list>", "relevance": "high" or "medium",
             "rationale": "<1 sentence, naming the verified fact it rests on, or 'industry norm'>"}}]}}
"""


def _rule_based(industry_text: str) -> tuple[list[dict], str]:
    for pattern, risk_ids, workforce in INDUSTRY_RULES:
        if re.search(pattern, industry_text, re.IGNORECASE):
            return ([{"id": r, "label": RISK_TAXONOMY[r], "relevance": "high" if i < 2 else "medium",
                      "rationale": "Typical for this industry (rule-based estimate)", "basis": "assumption"}
                     for i, r in enumerate(risk_ids)], workforce)
    return ([{"id": r, "label": RISK_TAXONOMY[r], "relevance": "medium",
              "rationale": "General corporate workforce assumption (industry unknown)", "basis": "assumption"}
             for r in DEFAULT_RISKS],
            "Industry unknown: assumed a general corporate workforce with office staff and dependants.")


def _ai_assumptions(name: str, fields: dict) -> dict:
    facts = "\n".join(f"- {k}: {v['display']}" for k, v in fields.items()) or "- (none found)"
    taxonomy = "\n".join(f"- {k}: {v}" for k, v in RISK_TAXONOMY.items())
    data = llm.generate_json(PROFILE_PROMPT.format(name=name, facts=facts, taxonomy=taxonomy),
                             system=PROFILE_SYSTEM, temperature=0.2, max_tokens=2048)
    if not isinstance(data, dict):
        raise llm.LLMError("Profile response was not an object")

    risks, seen = [], set()
    for r in data.get("risks", []):
        rid = str(r.get("id", "")).strip()
        if rid in RISK_TAXONOMY and rid not in seen:  # drop anything outside the fixed list
            seen.add(rid)
            risks.append({"id": rid, "label": RISK_TAXONOMY[rid],
                          "relevance": "high" if r.get("relevance") == "high" else "medium",
                          "rationale": str(r.get("rationale", ""))[:300], "basis": "assumption"})
    if not risks:
        raise llm.LLMError("No valid risks returned")
    return {"industry_guess": str(data.get("industry_guess", "")).strip(),
            "workforce_profile": str(data.get("workforce_profile", "")).strip(),
            "workforce_reason": str(data.get("workforce_reason", "")).strip(),
            "risks": risks[:5]}


# ---------------------------------------------------------------- main entry point

def validate_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "")).strip()
    if not name:
        raise ProfileError("Please enter a company name.")
    if len(name) > 100:
        raise ProfileError("Company name is too long (max 100 characters).")
    if not re.search(r"[A-Za-z0-9]", name):
        raise ProfileError("Company name must contain letters or numbers.")
    return name


def generate_company_profile(name: str, wikidata_id: str | None = None, use_ai: bool = True) -> dict:
    """Build a client profile. Never raises for lookup/AI failures; those become warnings."""
    name = validate_name(name)
    if wikidata_id and not re.fullmatch(r"Q\d+", wikidata_id):
        raise ProfileError("Invalid company selection.")
    warnings, candidates, found = [], [], None

    # 1. Find the company on Wikidata (or use the one the advisor picked)
    try:
        candidates = [dict(c) for c in search_companies(name)]
        qid = wikidata_id or next((c["id"] for c in candidates if c["is_company"]), None)
        if qid:
            found = fetch_wikidata_facts(qid)
        else:
            warnings.append(f"'{name}' was not found as a company on Wikidata. The profile below is based on assumptions.")
    except ProfileError:
        raise
    except (requests.RequestException, LookupError, KeyError, ValueError) as e:
        log.warning("Wikidata lookup failed for %s: %s", name, e)
        warnings.append("Company databases could not be reached, so the profile below is based on assumptions.")

    fields = dict(found["fields"]) if found else {}

    # 2. Summary from Wikipedia (verbatim)
    if found and found.get("enwiki_title"):
        try:
            summary = fetch_wikipedia_summary(found["enwiki_title"])
            if summary:
                fields["summary"] = summary
        except (requests.RequestException, ValueError) as e:
            log.warning("Wikipedia summary failed for %s: %s", found["enwiki_title"], e)

    # 3. Assumptions: AI first, rules if the AI is unavailable
    generated_by = "rules"
    industry_text = " ".join(fields.get("industry", {}).get("value", [])) + " " + (found or {}).get("description", "")
    assumptions = None
    if use_ai and not config.DEMO_MODE:
        try:
            assumptions = _ai_assumptions(found["label"] if found else name, fields)
            generated_by = llm.last_model_used or config.GEMINI_MODEL
        except llm.LLMError as e:
            log.warning("AI profile assumptions failed: %s", e)
            warnings.append("The AI was unavailable, so risks were estimated with industry rules.")

    if assumptions and not found:
        # Nothing was verified, so the AI must not claim a source. Strip any such claim in code.
        for r in assumptions["risks"]:
            if CLAIMS_SOURCE.search(r["rationale"]):
                r["rationale"] = "Industry norm (company details could not be verified)"
        assumptions["workforce_reason"] = "Guessed from the company name only; nothing was verified"
        sentences = re.split(r"(?<=[.!?])\s+", assumptions["workforce_profile"])
        kept = " ".join(x for x in sentences if x and not CLAIMS_SOURCE.search(x))
        assumptions["workforce_profile"] = kept or _rule_based(assumptions["industry_guess"])[1]

    if assumptions:
        risks = assumptions["risks"]
        workforce = {"value": assumptions["workforce_profile"], "basis": "assumption",
                     "reason": assumptions["workforce_reason"] or "Inferred from the verified facts above"}
        if "industry" not in fields and assumptions["industry_guess"]:
            fields["industry"] = {"value": [assumptions["industry_guess"]], "display": assumptions["industry_guess"],
                                  "basis": "assumption", "reason": "Guessed from the company name; not verified"}
    else:
        risks, workforce_text = _rule_based(industry_text)
        workforce = {"value": workforce_text, "basis": "assumption",
                     "reason": "Industry-based estimate" if industry_text.strip() else "No industry data found"}

    return {
        "company": name,
        "resolved_name": found["label"] if found else name,
        "description": found["description"] if found else "",
        "wikidata_id": (wikidata_id or (found and found["wikidata_url"].rsplit("/", 1)[-1])) or None,
        "wikidata_url": found["wikidata_url"] if found else None,
        "matched": bool(found),
        "candidates": candidates,
        "fields": fields,
        "workforce_profile": workforce,
        "risks": risks,
        "generated_by": generated_by,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# Name used in the case study brief
generateCompanyProfile = generate_company_profile
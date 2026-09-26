"""Policy fact store: turns brochure pages into verified, citable facts.

Each fact records what a benefit is worth, under what conditions, whether it is
included in the base cover or costs extra, and the exact quote + page it came from.
Every fact is then checked in code against the PDF text (quote present? numbers
present?) so only the doubtful ones need a human look. Human decisions live in
data/processed/fact_overrides.json and are re-applied on every build.
"""
import csv
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src import config, llm
from src.ingest import render_page_images

log = logging.getLogger(__name__)

CoverageType = Literal["base", "optional", "add_on", "plan_dependent"]
Category = Literal[
    "sum_insured", "hospitalisation", "pre_post_hospitalisation", "room_icu", "ambulance",
    "restore_recharge", "bonus", "waiting_period", "day_care_modern", "maternity", "chronic_care",
    "opd_consultation", "wellness_checkup", "daily_cash", "personal_accident", "international",
    "co_pay_deductible", "discount", "eligibility", "other",
]


class Fact(BaseModel):
    """One checkable benefit statement from a brochure."""
    policy: str
    benefit: str = Field(description="Short benefit name, e.g. 'Air ambulance'")
    category: Category = "other"
    value: str = Field(description="The limit/amount/duration exactly as stated, e.g. 'Up to ₹5,00,000'")
    conditions: str = ""
    coverage_type: CoverageType
    coverage_note: str = Field("", description="Why this coverage_type, e.g. footnote text")
    page: int
    quote: str = Field(description="Verbatim text copied from the page supporting this fact")


EXTRACTION_SYSTEM = """You extract insurance benefit facts from Indian health insurance brochures.
You are building a compliance fact store: every fact you output will be shown to clients, so it must be
exactly what the brochure says. Never infer, round, generalise or add information."""

EXTRACTION_PROMPT = """Policy: {name} (code {code})

Below are the page images and the extracted text of this brochure. Use the images to understand tables,
columns, footnote markers (superscript numbers, *, #, ^, ~) and which plan/variant a row belongs to.
Use the extracted text for exact wording.

Extract EVERY distinct benefit, limit, waiting period, sub-limit, bonus, add-on, discount and eligibility
rule. One fact per benefit per variant (if a limit differs by sum insured or plan, output one fact per
tier, or one fact whose value lists all tiers exactly).

coverage_type rules (the most important field):
- "base": included in the policy at no extra cost.
- "optional": an optional benefit of THIS policy, available on payment of additional premium.
- "add_on": provided through a separate add-on policy/rider (e.g. named add-on, separate UIN).
- "plan_dependent": only in some plans/variants/sum-insured options of this policy.
Check footnotes carefully: a marker such as "Air Ambulance5" pointing to "5 Optional benefit ... on payment
of additional premium" makes the benefit "optional", even if the row looks like a normal benefit.
Put the footnote wording that decided it in coverage_note.

Field rules:
- value: amounts, durations and percentages exactly as printed (keep ₹/INR, lakh/lacs, %, days).
- quote: copy a short verbatim span (10-40 words) from the extracted text below that contains the value.
  Do not paraphrase. If the text layer is garbled, copy the closest readable span.
- page: the page number the quote comes from.
- Skip contact details, legal disclaimers, UIN lists and pure marketing slogans with no checkable content.

Return JSON: {{"facts": [{{"policy": "{code}", "benefit": "...", "category": "...", "value": "...",
"conditions": "...", "coverage_type": "...", "coverage_note": "...", "page": 1, "quote": "..."}}]}}

Allowed categories: {categories}

EXTRACTED TEXT:
{pages_text}
"""


# ---------------------------------------------------------------- extraction

def extract_policy_facts(code: str, pages: list[dict], fallback: bool = True) -> list[dict]:
    """One strong-model call per policy, with every page image and page text."""
    policy = config.POLICIES[code]
    pages_text = "\n\n".join(f"=== PAGE {p['page']} ===\n{p['text']}" for p in pages)
    prompt = EXTRACTION_PROMPT.format(
        name=policy["name"], code=code, pages_text=pages_text,
        categories=", ".join(Category.__args__),
    )
    data = llm.generate_json(
        prompt, strong=True, images=render_page_images(code),
        system=EXTRACTION_SYSTEM, temperature=0.0, max_tokens=32000, fallback=fallback,
    )
    extracted_by = llm.last_model_used
    raw_facts = data.get("facts", []) if isinstance(data, dict) else data

    facts = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue
        raw["policy"] = code
        if raw.get("category") not in Category.__args__:
            raw["category"] = "other"  # an unknown category shouldn't cost us the fact
        try:
            facts.append({**Fact(**raw).model_dump(), "extracted_by": extracted_by})
        except ValidationError as e:
            err = e.errors()[0]
            log.warning("Skipped malformed fact from %s (%s: %s)", code, err["loc"][0], err["msg"][:80])
    return facts


# ---------------------------------------------------------------- validation

def normalise(text: str) -> str:
    """Lowercase, unify rupee signs, drop punctuation (but keep commas inside numbers like 2,50,000)."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("`", "₹").replace("inr", "₹").replace("rs.", "₹")
    text = re.sub(r"[^\w₹%.,]+", " ", text)
    text = re.sub(r",(?!\d)|(?<!\d),", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# (?<![A-Za-z]) skips footnote markers glued to words, e.g. "Wellness Benefit1", "Air Ambulance5"
_NUMBER = re.compile(r"(?<![A-Za-z])(\d[\d,]*(?:\.\d+)?)\s*(crores?|cr\b|lacs?\b|lakhs?\b|lac\b|l\b|k\b)?", re.IGNORECASE)
_MULTIPLIER = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "lac": 1e5, "lacs": 1e5, "lakh": 1e5,
               "lakhs": 1e5, "l": 1e5, "k": 1e3}
_NUMBER_WORDS = {"zero": 0, "nil": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twice": 2, "double": 2}


def extract_numbers(text: str) -> set[float]:
    """All numbers in the text, with lakh/crore expanded (5 lacs -> 500000, also keeps 5)."""
    numbers = {float(n) for word, n in _NUMBER_WORDS.items() if re.search(rf"\b{word}\b", text, re.I)}
    for digits, unit in _NUMBER.findall(text):
        try:
            n = float(digits.replace(",", ""))
        except ValueError:
            continue
        numbers.add(n)
        if unit:
            numbers.add(n * _MULTIPLIER[unit.lower()])
    return numbers


CONTEXT_CHARS = 150  # text either side of a matched quote that counts as its evidence


def find_quote(quote: str, page_text: str) -> tuple[float, str]:
    """Verbatim or fuzzy match. Returns (ratio, evidence) where evidence is the matched text plus context."""
    q, t = normalise(quote), normalise(page_text)
    if not q or not t:
        return 0.0, ""
    at = t.find(q)
    if at != -1:
        return 1.0, t[max(0, at - CONTEXT_CHARS):at + len(q) + CONTEXT_CHARS]
    window, step = len(q), max(5, len(q) // 10)
    best, best_start = 0.0, 0
    for start in range(0, max(1, len(t) - window + 1), step):
        ratio = SequenceMatcher(None, q, t[start:start + window]).ratio()
        if ratio > best:
            best, best_start = ratio, start
            if best > 0.97:
                break
    evidence = t[max(0, best_start - CONTEXT_CHARS):best_start + window + CONTEXT_CHARS]
    return round(best, 3), evidence


def nearby_words_match(quote: str, page_text: str) -> tuple[float, str]:
    """Share of the quote's words found close together on the page, and that stretch of text.

    Tables and multi-column layouts split a row's label and value apart in the text layer,
    so a verbatim match fails even when the quote is genuine. This looks for a window of
    ~3x the quote's length that contains the quote's words.
    """
    q_words, t_words = normalise(quote).split(), normalise(page_text).split()
    if len(q_words) < 3 or not t_words:
        return 0.0, ""
    wanted = set(q_words)
    window = max(len(q_words) * 3, len(q_words) + 15)
    step = max(1, len(q_words) // 4)
    best, best_start = 0.0, 0
    for start in range(0, max(1, len(t_words) - window + 1), step):
        coverage = len(wanted & set(t_words[start:start + window])) / len(wanted)
        if coverage > best:
            best, best_start = coverage, start
            if best == 1.0:
                break
    return round(best, 3), " ".join(t_words[best_start:best_start + window])


def locate_quote(quote: str, page_text: str) -> tuple[float, str, str]:
    """Best evidence that the quote is on the page: (score, method, evidence text)."""
    score, evidence = find_quote(quote, page_text)
    if score >= 0.85:
        return score, ("exact" if score == 1.0 else "fuzzy"), evidence
    nearby, nearby_evidence = nearby_words_match(quote, page_text)
    if nearby >= 0.9:
        return nearby, "nearby_words", nearby_evidence
    return max(score, round(nearby * 0.8, 3)), "not_found", ""


EXTRA_COST_WORDS = ("optional", "add-on", "add on", "additional premium", "rider")


def validate_fact(fact: dict, pages_by_key: dict) -> dict:
    """Check a fact against the PDF text in code. Adds match scores, flags and a status."""
    flags, notes = [], []
    page_text = pages_by_key.get((fact["policy"], fact["page"]), "")

    score, method, evidence = locate_quote(fact["quote"], page_text)
    if method == "not_found":
        # The model may have cited the wrong page: look on the other pages of the same policy
        for (policy, page), text in pages_by_key.items():
            if policy == fact["policy"] and page != fact["page"]:
                other = locate_quote(fact["quote"], text)
                if other[1] != "not_found":
                    notes.append(f"page corrected from {fact['page']} to {page}")
                    fact["page"] = page
                    score, method, evidence = other
                    break
    fact["quote_match"] = score
    fact["match_method"] = method
    if method == "nearby_words":
        notes.append("quote matched as nearby words (table/column layout)")
    if len(normalise(fact["quote"]).split()) < 3:
        flags.append("quote too short to prove anything")
    elif method == "not_found":
        flags.append("quote not found in PDF text")

    # Numbers must appear where the quote matched, not just anywhere on the page:
    # "₹5,00,000" must not pass because "5 Lacs" appears in some other row.
    if evidence:
        evidence_numbers = extract_numbers(evidence)
        wrong_in_quote = sorted(n for n in extract_numbers(fact["quote"]) if n not in evidence_numbers)
        if wrong_in_quote:
            flags.append("quote has numbers not in the PDF at that spot: "
                         + ", ".join(f"{n:g}" for n in wrong_in_quote))
        missing = sorted(n for n in extract_numbers(fact["value"]) if n not in evidence_numbers)
        if missing:
            flags.append("value has numbers not in the source: " + ", ".join(f"{n:g}" for n in missing))

    context = " ".join([fact["quote"], fact["conditions"], fact["coverage_note"]]).lower()
    if fact["coverage_type"] == "base" and any(w in context for w in EXTRA_COST_WORDS):
        flags.append("marked base, but wording suggests optional/add-on")

    fact["flags"] = flags
    earlier = [n for n in fact.get("notes", []) if n.startswith("page corrected")]
    fact["notes"] = sorted(set(earlier + notes))  # informational, not a problem
    fact.setdefault("reviewed", False)
    if fact["reviewed"]:
        fact["status"] = "human_verified"   # a person checked it against the PDF
    else:
        fact["status"] = "auto_verified" if not flags else "needs_review"
    return fact


# ---------------------------------------------------------------- golden checks

# Known traps in these brochures, confirmed by hand. The build fails loudly if extraction gets them wrong.
GOLDEN_CHECKS = [
    {"policy": "CARE", "keywords": ["air ambulance"], "coverage_type": "optional", "number": 500000,
     "why": "Care air ambulance ₹5L is an optional benefit (extra premium)"},
    {"policy": "CARE", "keywords": ["bonus booster"], "coverage_type": "add_on", "number": None,
     "why": "Unlimited bonus comes from the Care Advanced add-on"},
    {"policy": "NIVA", "keywords": ["air ambulance"], "coverage_type": "base", "number": 250000,
     "why": "Niva air ambulance is capped at ₹2,50,000 per hospitalisation"},
    {"policy": "NIVA", "keywords": ["safeguard+"], "coverage_type": "optional", "number": None,
     "why": "Safeguard+ is an optional benefit"},
    {"policy": "HDFC", "keywords": ["air ambulance"], "coverage_type": "base", "number": 500000,
     "why": "HDFC air ambulance is up to ₹5,00,000"},
    {"policy": "HDFC", "keywords": ["chronic care"], "coverage_type": "add_on", "number": None,
     "why": "ABCD Chronic Care is a separate add-on"},
]


def run_golden_checks(facts: list[dict], codes: list[str] | None = None) -> list[dict]:
    results = []
    for check in GOLDEN_CHECKS:
        if codes and check["policy"] not in codes:
            continue
        candidates = [
            f for f in facts
            if f["policy"] == check["policy"]
            and all(k in (f["benefit"] + " " + f["quote"]).lower() for k in check["keywords"])
        ]
        passed = any(
            f["coverage_type"] == check["coverage_type"]
            and (check["number"] is None or check["number"] in extract_numbers(f["value"]))
            for f in candidates
        )
        results.append({**check, "result": "PASS" if passed else ("WRONG" if candidates else "MISSING"),
                        "found": [f"{f['fact_id']} [{f['coverage_type']}] {f['value']}" for f in candidates]})
    return results


# ---------------------------------------------------------------- storage

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "fact"


def assign_ids(facts: list[dict]) -> list[dict]:
    seen = {}
    for fact in facts:
        base = f"{fact['policy']}-{_slug(fact['benefit'])}"
        seen[base] = seen.get(base, 0) + 1
        fact["fact_id"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    return facts


def load_store() -> dict:
    if config.FACTS_FILE.exists():
        return json.loads(config.FACTS_FILE.read_text(encoding="utf-8"))
    return {"generated_at": None, "facts": []}


def load_facts(codes: list[str] | None = None) -> list[dict]:
    facts = load_store()["facts"]
    return [f for f in facts if not codes or f["policy"] in codes]


def save_store(facts: list[dict], models: dict):
    store = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": models,
        "facts": sorted(facts, key=lambda f: (f["policy"], f["page"], f["fact_id"])),
    }
    config.FACTS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_review_csv(store["facts"])


def _write_review_csv(facts: list[dict]):
    """Spreadsheet view for hand-checking (open in Excel). Put fixes in fact_overrides.json."""
    columns = ["fact_id", "status", "reviewed", "flags", "notes", "policy", "page", "benefit", "value",
               "coverage_type", "conditions", "coverage_note", "quote", "quote_match", "match_method",
               "extracted_by"]
    with open(config.FACTS_REVIEW_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for fact in facts:
            writer.writerow({**fact, "flags": "; ".join(fact.get("flags", [])), "notes": "; ".join(fact.get("notes", []))})


# ---------------------------------------------------------------- human overrides

EDITABLE_FIELDS = {"benefit", "category", "value", "conditions", "coverage_type", "coverage_note",
                   "page", "quote", "reviewed", "review_note"}


def load_overrides() -> dict:
    """Human review decisions. Kept separate so re-extraction never loses them.

    Keys are either an exact fact_id ("HDFC-secure-benefit") or a keyword rule
    ("match:ABHI:super credit" = every ABHI fact whose benefit contains "super credit").
    Keys starting with "_" are comments.
    """
    if not config.FACT_OVERRIDES_FILE.exists():
        return {}
    data = json.loads(config.FACT_OVERRIDES_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _override_targets(key: str, facts: list[dict]) -> list[str]:
    """fact_ids an override key applies to."""
    if key.startswith("match:"):
        try:
            _, policy, keyword = key.split(":", 2)
        except ValueError:
            raise ValueError(f"'{key}' should look like match:POLICY:keyword")
        keyword = keyword.strip().lower()
        return [f["fact_id"] for f in facts
                if f["policy"] == policy.strip().upper() and keyword in f["benefit"].lower()]
    return [key] if any(f["fact_id"] == key for f in facts) else []


def apply_overrides(facts: list[dict]) -> tuple[list[dict], list[str]]:
    """Apply overrides. {"delete": true} removes a fact. Returns (facts, keys that matched nothing)."""
    changes, unmatched = {}, []
    for key, change in load_overrides().items():
        bad = set(change) - EDITABLE_FIELDS - {"delete"}
        if bad:
            raise ValueError(f"{key} has unknown fields {sorted(bad)}")
        targets = _override_targets(key, facts)
        if not targets:
            unmatched.append(key)
        for fact_id in targets:
            changes.setdefault(fact_id, {}).update(change)

    result = []
    for fact in facts:
        change = changes.get(fact["fact_id"])
        if change and change.get("delete"):
            continue
        if change:
            fact = {**fact, **change, "overridden": True}
        result.append(fact)
    return result, unmatched


def pages_lookup(pages: list[dict]) -> dict:
    return {(p["policy"], p["page"]): p["text"] for p in pages}


def build(codes: list[str], pages: list[dict], pause_seconds: int = 15,
          fallback: bool = True) -> tuple[list[dict], dict]:
    """Extract + validate facts for the given policies, keeping other policies' facts unchanged.

    Returns (all facts, errors by policy). A policy that fails keeps its previous facts.
    """
    lookup = pages_lookup(pages)
    existing = load_facts()
    new_facts, errors, rebuilt = [], {}, []
    for i, code in enumerate(codes):
        if i:
            time.sleep(pause_seconds)  # stay under the free tier's requests-per-minute limit
        policy_pages = [p for p in pages if p["policy"] == code]
        log.info("Extracting facts for %s (%d pages)", code, len(policy_pages))
        try:
            extracted = extract_policy_facts(code, policy_pages, fallback=fallback)
        except llm.LLMError as e:
            errors[code] = str(e)
            continue
        if not extracted:
            errors[code] = "No facts were extracted"
            continue
        new_facts.extend(extracted)
        rebuilt.append(code)

    kept = [f for f in existing if f["policy"] not in rebuilt]
    for f in new_facts:
        f.pop("fact_id", None)
    facts = assign_ids(kept + new_facts) if new_facts else kept
    facts, unmatched = apply_overrides(facts)
    if unmatched:
        errors["overrides"] = "matched no facts: " + ", ".join(unmatched)
    facts = [validate_fact(f, lookup) for f in facts]
    save_store(facts, {"extraction": config.GEMINI_MODEL_STRONG, "fallback": config.GEMINI_MODEL})
    return facts, errors


def revalidate(pages: list[dict]) -> tuple[list[dict], dict]:
    """Apply fact_overrides.json and re-run the code checks (no AI calls)."""
    store = load_store()
    lookup = pages_lookup(pages)
    facts = assign_ids(store["facts"]) if any("fact_id" not in f for f in store["facts"]) else store["facts"]
    facts, unmatched = apply_overrides(facts)
    errors = {"overrides": "matched no facts: " + ", ".join(unmatched)} if unmatched else {}
    facts = [validate_fact(f, lookup) for f in facts]
    save_store(facts, store.get("models", {}))
    return facts, errors
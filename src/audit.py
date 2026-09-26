"""auditPitchContent: check every claim in a pitch against the policy documents.

Each claim is traced claim -> fact -> verbatim brochure quote -> page, then checked:

  Code checks (deterministic, can't be talked round):
    citation   - the claim cites facts that exist and passed fact-store verification
    numbers    - every amount / limit / duration in the claim appears in the cited evidence
    qualifier  - optional / add-on / plan-dependent benefits are not presented as included
    attribution- the claim doesn't name a different insurer than the one it cites
  AI check (batched calls):
    meaning    - does the evidence actually support what the sentence says?

Uncited claims are searched against the brochures (BM25) so they can still be traced;
if nothing supports them they fail. Statuses: verified / review / fail, plus info for
content outside the policy documents (Marsh messaging). Any fail makes the pitch FAIL;
any review means an advisor must approve before sending.
"""
import csv
import html
import io
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from src import config, fact_store, llm, retrieval
from src.fact_store import extract_numbers
from src.pitch import QUALIFIER, QUALIFIER_WORDS

log = logging.getLogger(__name__)

STATUS_ORDER = {"fail": 0, "review": 1, "verified": 2, "info": 3}

# Names that identify each policy in free text (used for the attribution check)
POLICY_NAMES = {
    "NIVA": ["niva", "reassure"],
    "HDFC": ["hdfc", "optima"],
    "CARE": ["care supreme", "care health"],
    "ABHI": ["aditya birla", "activ one", "abhi"],
}
# A sentence with no citation that still states coverage terms is really a policy claim
POLICY_LANGUAGE = re.compile(
    r"₹|\binr\b|\blakhs?\b|\blacs?\b|\bcrores?\b|sum insured|room rent|waiting period|co-?pay|deductible|"
    r"ambulance|restore|recharge|reload|reassure|cashless|\bcovered (up|from|under|at|for)|\bcovers? (up to|from day)",
    re.IGNORECASE)

ADVISOR_GUIDANCE = [
    "FAIL items must be fixed (edit the text so it matches the cited clause) or rejected (removed from the deck). "
    "Edited claims are re-audited automatically.",
    "REVIEW items are traceable but need a human judgement: check the quoted clause, then approve, edit or reject.",
    "INFO items are outside the policy documents (e.g. Marsh messaging): confirm the wording with Marsh marketing.",
    "The deck can be downloaded for a client only when no FAIL remains and every REVIEW item has been approved.",
]


# ---------------------------------------------------------------- evidence

def _fact_evidence(f: dict) -> dict:
    return {
        "kind": "fact", "fact_id": f["fact_id"], "policy": f["policy"],
        "source": f"{config.POLICIES[f['policy']]['insurer']} brochure p.{f['page']}",
        "file": config.POLICIES[f["policy"]]["file"], "page": f["page"],
        "benefit": f["benefit"], "value": f["value"], "conditions": f.get("conditions", ""),
        "coverage_type": f["coverage_type"], "quote": f["quote"], "fact_status": f.get("status"),
    }


def _chunk_evidence(c: dict) -> dict:
    return {
        "kind": "retrieved", "chunk_id": c["chunk_id"], "policy": c["policy"],
        "source": f"{config.POLICIES[c['policy']]['insurer']} brochure p.{c['page']}",
        "file": config.POLICIES[c["policy"]]["file"], "page": c["page"], "quote": c["text"][:400],
        "score": c.get("score"),
    }


def _numbers_in(text: str) -> set[float]:
    """Numbers stated in a sentence (ignores slide/claim ids like S3-2)."""
    return extract_numbers(re.sub(r"\bS\d+-\d+\b", " ", text))


# ---------------------------------------------------------------- code checks

def _check(name: str, result: str, detail: str) -> dict:
    return {"name": name, "result": result, "detail": detail}


def _policy_checks(text: str, evidence: list[dict]) -> list[dict]:
    checks = []
    facts = [e for e in evidence if e["kind"] == "fact"]
    unusable = [e["fact_id"] for e in facts if e.get("fact_status") not in ("auto_verified", "human_verified")]
    if not facts:
        checks.append(_check("citation", "fail", "No valid policy fact is cited"))
    elif unusable:
        checks.append(_check("citation", "fail", "Cited fact is not verified: " + ", ".join(unusable)))
    else:
        checks.append(_check("citation", "pass", "Cites " + ", ".join(f"{e['fact_id']} ({e['source']})" for e in facts)))

    if facts:
        pool = set()
        for e in facts:
            pool |= extract_numbers(" ".join([e["benefit"], e["value"], e["conditions"], e["quote"]]))
        missing = sorted(n for n in _numbers_in(text) if n not in pool)
        checks.append(_check("numbers", "fail" if missing else "pass",
                             ("Not in the cited clause: " + ", ".join(f"{n:g}" for n in missing)) if missing
                             else "Every number matches the cited clause"))

        lowered = text.lower()
        unqualified = [e for e in facts if e["coverage_type"] in QUALIFIER
                       and not any(w in lowered for w in QUALIFIER_WORDS[e["coverage_type"]])]
        checks.append(_check("qualifier", "fail" if unqualified else "pass",
                             ("Presented as included, but the brochure says: " +
                              "; ".join(f"{e['benefit']} is {QUALIFIER[e['coverage_type']]}" for e in unqualified))
                             if unqualified else "Coverage type is stated correctly"))

        cited_policies = {e["policy"] for e in facts}
        named = {code for code, words in POLICY_NAMES.items() if any(w in lowered for w in words)}
        wrong = named - cited_policies
        checks.append(_check("attribution", "fail" if wrong else "pass",
                             (f"Names {', '.join(config.POLICIES[c]['name'] for c in wrong)} but cites "
                              f"{', '.join(config.POLICIES[c]['name'] for c in cited_policies)}") if wrong
                             else "Insurer named matches the cited source"))
    return checks


def _company_checks(text: str, claim: dict, profile: dict) -> tuple[list[dict], list[dict]]:
    fields = (profile or {}).get("fields", {})
    cited = [k for k in claim.get("profile_fields", []) if k in fields]
    evidence = [{"kind": "company", "field": k, "source": fields[k].get("source", "profile"),
                 "url": fields[k].get("source_url"), "quote": fields[k].get("display", ""),
                 "basis": fields[k].get("basis")} for k in cited]
    checks = []
    if not cited:
        checks.append(_check("citation", "fail", "No company field is cited"))
        return checks, evidence
    not_sourced = [k for k in cited if fields[k].get("basis") != "sourced"]
    checks.append(_check("citation", "review" if not_sourced else "pass",
                         ("Based on an assumption, not a verified source: " + ", ".join(not_sourced)) if not_sourced
                         else "Cites " + ", ".join(f"{k} ({fields[k]['source']})" for k in cited)))
    pool = set()
    for k in cited:
        pool |= extract_numbers(str(fields[k].get("display", "")))
    missing = sorted(n for n in _numbers_in(text) if n not in pool)
    checks.append(_check("numbers", "fail" if missing else "pass",
                         ("Not in the cited company data: " + ", ".join(f"{n:g}" for n in missing)) if missing
                         else "Every number matches the company data"))
    return checks, evidence


# ---------------------------------------------------------------- AI meaning check

JUDGE_SYSTEM = """You are a compliance auditor for an insurance broker. For each claim, decide whether the
evidence supports it. Be strict about facts, relaxed about tone:
- "supported": every factual element (benefit, amount, limit, duration, eligibility, whether it is included
  or costs extra) is stated in the evidence. Paraphrase and persuasive framing ("helps", "protects") are fine.
- "partially_supported": the core fact is in the evidence, but the claim adds a detail, generalisation or
  implication the evidence does not state.
- "unsupported": the evidence does not establish the claim.
- "contradicted": the evidence says something different (other amount, other scope, optional vs included).
Judge only against the evidence given. Do not use outside knowledge."""

JUDGE_PROMPT = """Audit these claims. Return JSON exactly in this shape, one result per claim, using the claim ids as given
(for example "S3-2"):
{{"results": [{{"id": "S3-2", "verdict": "supported", "issue": ""}}]}}
verdict must be one of: supported, partially_supported, unsupported, contradicted.

{items}"""

JUDGE_BATCH = 12  # claims per AI call: small batches keep answers complete and well-formed
VERDICTS = {"supported", "partially_supported", "unsupported", "contradicted"}
_CLAIM_ID = re.compile(r"S\d+-\d+", re.IGNORECASE)


def _normalise_verdict(value) -> str | None:
    v = re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())
    if v in VERDICTS:
        return v
    if v.startswith("partial"):
        return "partially_supported"
    if v.startswith("contradict"):
        return "contradicted"
    if v.startswith("unsupport") or v in ("not_supported", "no_support"):
        return "unsupported"
    if v.startswith("support") or v in ("yes", "true", "pass"):
        return "supported"
    return None


def _parse_judgements(data) -> dict:
    """Accepts the shapes models actually return: {"results": [...]}, a bare list, or {"S1-1": {...}}."""
    if isinstance(data, dict):
        rows = next((data[k] for k in ("results", "claims", "audit", "verdicts") if isinstance(data.get(k), list)), None)
        if rows is None:  # keyed by claim id
            rows = [{**v, "id": k} if isinstance(v, dict) else {"id": k, "verdict": v} for k, v in data.items()]
    elif isinstance(data, list):
        rows = data
    else:
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        raw_id = str(r.get("id") or r.get("claim_id") or r.get("claim") or "")
        m = _CLAIM_ID.search(raw_id)
        verdict = _normalise_verdict(r.get("verdict") or r.get("status") or r.get("result"))
        if m and verdict:
            out[m.group(0).upper()] = {"verdict": verdict,
                                       "issue": str(r.get("issue") or r.get("reason") or "")[:300]}
    return out


def _format_item(it: dict) -> str:
    lines = [f"CLAIM {it['id']}: {it['text']}", "EVIDENCE:"]
    for e in it["evidence"]:
        if e["kind"] == "company":
            lines.append(f"  - [{e['source']}] {e['field']}: {e['quote']}")
        elif e["kind"] == "fact":
            lines.append(f"  - [{e['source']}] {e['benefit']} = {e['value']}"
                         f"{' | conditions: ' + e['conditions'] if e['conditions'] else ''}"
                         f" | coverage: {e['coverage_type']} | brochure text: \"{e['quote']}\"")
        else:
            lines.append(f"  - [{e['source']}] brochure text: \"{e['quote']}\"")
    return "\n".join(lines)


def _judge(items: list[dict]) -> dict:
    """AI meaning check in small batches. Returns {claim_id: {"verdict", "issue"}}; missing ids = no verdict."""
    if not items or config.DEMO_MODE:
        return {}
    out = {}
    for i in range(0, len(items), JUDGE_BATCH):
        batch = items[i:i + JUDGE_BATCH]
        try:
            data = llm.generate_json(JUDGE_PROMPT.format(items="\n\n".join(_format_item(it) for it in batch)),
                                     system=JUDGE_SYSTEM, temperature=0.0, max_tokens=8192)
        except llm.LLMError as e:
            log.warning("AI meaning check unavailable for claims %s..%s: %s", batch[0]["id"], batch[-1]["id"], e)
            continue
        parsed = _parse_judgements(data)
        wanted = {it["id"].upper() for it in batch}
        got = {k: v for k, v in parsed.items() if k in wanted}
        if len(got) < len(batch):
            log.warning("AI returned verdicts for %d of %d claims. Start of its answer: %s",
                        len(got), len(batch), str(data)[:300])
        out.update(got)
    return out


# ---------------------------------------------------------------- per-claim decision

def _decide(entry: dict, judgement: dict | None, ai_attempted: bool):
    checks = entry["checks"]
    if judgement:
        verdict, issue = judgement["verdict"], judgement["issue"]
        result = {"supported": "pass", "partially_supported": "review"}.get(verdict, "fail")
        checks.append(_check("meaning", result, f"AI auditor: {verdict.replace('_', ' ')}" + (f". {issue}" if issue else "")))
    elif entry.pop("_needs_ai", False):
        if entry.get("_retrieved"):
            # No citation AND nothing could confirm the passage we found: treat as unsupported
            checks.append(_check("meaning", "fail", "No citation, and the AI check that could trace it did not run"))
        else:
            checks.append(_check("meaning", "review", "The AI check gave no verdict for this claim; needs a human read"
                                 if ai_attempted else "AI meaning check was not run; needs a human read"))

    results = [c["result"] for c in checks]
    if "fail" in results:
        status = "fail"
    elif "review" in results:
        status = "review"
    else:
        status = "verified"

    # Uncited but traceable: a supporting clause exists, so a human can confirm and add the citation
    if entry.get("_retrieved") and status == "verified":
        status = "review"
        checks.append(_check("citation", "review", "Supported by a brochure passage the pitch did not cite; confirm the source"))
    entry.pop("_retrieved", None)

    entry["status"] = status
    entry["confidence"] = {"verified": 1.0, "review": 0.5, "fail": 0.0, "info": None}[status]
    failing = [c for c in checks if c["result"] == status] if status in ("fail", "review") else []
    entry["reason"] = failing[0]["detail"] if failing else "All checks passed"


RECOMMENDED_SLIDES = ("risk_benefits", "recommendation")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("`", "₹")).strip().lower()


def _is_copy(text: str, value: str) -> bool:
    """A comparison cell is the fact's value (maybe shortened with '…') plus a '(qualifier)' suffix."""
    body = str(text)
    for q in QUALIFIER.values():
        body = body.replace(f"({q})", "")
    body = _squash(body).rstrip("…").strip()
    return bool(body) and _squash(value).startswith(body)


def _prepare(claim: dict, slide: dict, facts_by_id: dict, profile: dict, policies: list[str], ranking: list[dict],
             recommended: str | None = None) -> dict:
    """Run the code checks for one claim and gather its evidence (AI check happens later, batched)."""
    text = claim.get("text", "")
    entry = {"id": claim.get("id"), "slide": slide["n"], "slide_title": slide["title"], "text": text,
             "claim_type": claim.get("claim_type"), "checks": [], "evidence": []}
    kind = claim.get("claim_type")

    if kind == "marsh":
        entry.update(status="info", confidence=None, reason="Marsh messaging: outside the policy documents; confirm wording",
                     evidence=[{"kind": "firm", "source": s.get("label", "")} for s in claim.get("sources", [])])
        return entry

    if kind == "assumption" and not POLICY_LANGUAGE.search(text):
        scores = {p["score"] for p in ranking or []}
        numbers = _numbers_in(text)
        if numbers and "score" in text.lower() and numbers <= scores | {100.0}:
            entry["checks"].append(_check("numbers", "pass", "Fit score matches the Marsh scoring table"))
            entry["evidence"].append({"kind": "computed", "source": "Marsh fit scoring (code)", "quote": ""})
        elif numbers:
            entry["checks"].append(_check("numbers", "fail", "States numbers without a source"))
        else:
            entry["checks"].append(_check("assumption", "review", "Estimate about the workforce; no document can prove it"))
        return entry

    if kind == "company":
        entry["checks"], entry["evidence"] = _company_checks(text, claim, profile)
        if entry["evidence"]:
            entry["_needs_ai"] = True  # catches added details, e.g. "with teams across India"
        return entry

    # Policy claims (and "assumptions" that actually state coverage)
    cited = [facts_by_id[i] for i in claim.get("fact_ids", []) if i in facts_by_id]
    entry["evidence"] = [_fact_evidence(f) for f in cited]
    if cited:
        entry["checks"] = _policy_checks(text, entry["evidence"])
        # Code-built comparison cells are copied from the fact: exact copy needs no AI reading
        if slide.get("type") == "comparison" and _is_copy(text, cited[0]["value"]):
            entry["checks"].append(_check("meaning", "pass", "Copied directly from the cited fact"))
        else:
            entry["_needs_ai"] = True
    else:
        # On slides about the recommended policy, only its own brochure can support the claim:
        # "room rent at actuals" being true for HDFC doesn't make it true for the policy we recommend.
        scope = [recommended] if recommended and slide.get("type") in RECOMMENDED_SLIDES else policies
        hits = retrieval.search(text, scope, k=3)
        entry["evidence"] = [_chunk_evidence(h) for h in hits]
        why = "The AI cited facts that don't exist or belong to another policy" if claim.get("dropped_citations") \
            else "States policy details without citing a policy fact"
        entry["checks"].append(_check("citation", "pass" if hits else "fail",
                                      f"{why}; searched the brochures for support" if hits else f"{why}; no matching brochure passage"))
        if hits:
            entry["_needs_ai"] = True
            entry["_retrieved"] = True
    return entry


# ---------------------------------------------------------------- main entry point

def audit_pitch_content(pitch_slides: list[dict], policy_docs: list[str], profile: dict | None = None,
                        ranking: list[dict] | None = None, pitch_meta: dict | None = None,
                        use_ai: bool = True) -> dict:
    """Audit every claim on the slides against the given policy documents. Returns a structured report."""
    if not isinstance(pitch_slides, list) or not pitch_slides:
        raise ValueError("There are no slides to audit.")
    policies = [p for p in (policy_docs or []) if p in config.POLICIES] or list(config.POLICIES)
    facts_by_id = {f["fact_id"]: f for f in fact_store.load_facts(policies)}

    entries = []
    for slide in pitch_slides:
        for claim in slide.get("claims", []) or []:
            entries.append(_prepare(claim, slide, facts_by_id, profile or {}, policies, ranking or [],
                                    (pitch_meta or {}).get("recommended")))

    to_judge = [{"id": e["id"], "text": e["text"], "evidence": e["evidence"]} for e in entries if e.get("_needs_ai")]
    judgements = _judge(to_judge) if use_ai else {}
    auditor = llm.last_model_used if judgements else ("rules only" if not to_judge else "rules only (AI unavailable)")
    for e in entries:
        if e.get("status") == "info":
            continue
        _decide(e, judgements.get(str(e["id"]).upper()), ai_attempted=use_ai and not config.DEMO_MODE)

    counts = {s: sum(1 for e in entries if e["status"] == s) for s in ("verified", "review", "fail", "info")}
    checkable = counts["verified"] + counts["review"] + counts["fail"]
    score = round(100 * (counts["verified"] + 0.5 * counts["review"]) / checkable) if checkable else 0
    overall = "FAIL" if counts["fail"] else "REVIEW" if counts["review"] else "PASS"
    entries.sort(key=lambda e: (STATUS_ORDER[e["status"]], e["slide"], e["id"] or ""))

    meta = pitch_meta or {}
    return {
        "audit_id": uuid.uuid4().hex[:10],
        "pitch_id": meta.get("id"),
        "company": meta.get("company"),
        "policies_audited": [{"code": p, "name": config.POLICIES[p]["name"], "file": config.POLICIES[p]["file"]}
                             for p in policies],
        "auditor": auditor,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "overall": overall,
            "grounding_score": score,
            "counts": counts,
            "claims_total": len(entries),
            "headline": {
                "PASS": "Every claim is traced to a verified clause. Ready for advisor sign-off.",
                "REVIEW": f"No errors found; {counts['review']} claim(s) need an advisor's approval.",
                "FAIL": f"{counts['fail']} claim(s) are not supported by the policy documents and must be fixed or removed.",
            }[overall],
        },
        "claims": entries,
        "advisor_guidance": ADVISOR_GUIDANCE,
    }


def audit_pitch(pitch: dict, use_ai: bool = True) -> dict:
    """Convenience wrapper: audit a whole pitch object from generate_marketing_pitch()."""
    report = audit_pitch_content(pitch.get("slides"), pitch.get("policies"), profile=pitch.get("profile"),
                                 ranking=pitch.get("ranking"), pitch_meta=pitch, use_ai=use_ai)
    save_report(report)
    return report


def audit_single_claim(pitch: dict, claim_id: str, new_text: str, use_ai: bool = True) -> dict:
    """Re-audit one claim after the advisor edits it (keeps its original citations)."""
    new_text = (new_text or "").strip()
    if not new_text:
        raise ValueError("The edited text is empty.")
    for slide in pitch.get("slides", []):
        for claim in slide.get("claims", []) or []:
            if claim.get("id") == claim_id:
                edited = {**claim, "text": new_text}
                edited.pop("short_text", None)  # an edited cell is no longer an exact copy of the fact
                one = {**slide, "claims": [edited]}
                report = audit_pitch_content([one], pitch.get("policies"), profile=pitch.get("profile"),
                                             ranking=pitch.get("ranking"), pitch_meta=pitch, use_ai=use_ai)
                return report["claims"][0]
    raise ValueError(f"No claim with id {claim_id}.")


# Name used in the case study brief
auditPitchContent = audit_pitch_content


# ---------------------------------------------------------------- report files

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name or "pitch").strip("_")[:40]


def report_csv(report: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["claim_id", "slide", "status", "confidence", "claim", "reason", "sources", "evidence_quote", "checks"])
    for c in report["claims"]:
        writer.writerow([
            c["id"], c["slide"], c["status"].upper(), "" if c["confidence"] is None else c["confidence"], c["text"], c["reason"],
            "; ".join(e.get("source", "") for e in c["evidence"]),
            " | ".join(e.get("quote", "") for e in c["evidence"] if e.get("quote"))[:500],
            "; ".join(f"{k['name']}={k['result']}" for k in c["checks"]),
        ])
    return buffer.getvalue()


def report_html(report: dict) -> str:
    """Standalone, printable audit report (the 'Audit results' deliverable)."""
    s = report["summary"]
    colour = {"PASS": "#1E8E3E", "REVIEW": "#B26A00", "FAIL": "#C5221F"}[s["overall"]]
    badge = {"verified": ("#E6F4EA", "#1E8E3E"), "review": ("#FEF3E0", "#B26A00"),
             "fail": ("#FCE8E6", "#C5221F"), "info": ("#E8F0FE", "#1A73E8")}
    rows = []
    for c in report["claims"]:
        bg, fg = badge[c["status"]]
        evidence = "".join(
            f"<div class='ev'><b>{html.escape(e.get('source', ''))}</b>"
            f"{' · ' + html.escape(e['fact_id']) if e.get('fact_id') else ''}"
            f"{'<br>“' + html.escape(e['quote']) + '”' if e.get('quote') else ''}</div>" for e in c["evidence"])
        checks = "".join(f"<li class='{k['result']}'>{html.escape(k['name'])}: {html.escape(k['detail'])}</li>" for k in c["checks"])
        rows.append(f"<tr><td>{html.escape(str(c['id']))}<br><small>Slide {c['slide']}</small></td>"
                    f"<td><span class='st' style='background:{bg};color:{fg}'>{c['status'].upper()}</span></td>"
                    f"<td>{html.escape(c['text'])}<ul>{checks}</ul></td><td>{evidence or '<i>No evidence</i>'}</td></tr>")
    counts = s["counts"]
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Audit report: {html.escape(report.get('company') or '')}</title>
<style>body{{font-family:Segoe UI,system-ui,sans-serif;color:#1A1F2B;margin:32px;max-width:1200px}}
h1{{color:#002C77}} .sum{{display:flex;gap:24px;align-items:center;border:1px solid #DDE3EC;border-radius:10px;padding:16px;margin:16px 0}}
.big{{font-size:40px;font-weight:700;color:{colour}}} table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border-bottom:1px solid #DDE3EC;padding:8px;vertical-align:top;text-align:left}} th{{background:#002C77;color:#fff}}
.st{{font-weight:700;font-size:11px;padding:2px 8px;border-radius:999px}} .ev{{margin-bottom:6px;color:#5F6B7A}}
ul{{margin:6px 0 0 16px;color:#5F6B7A}} li.fail{{color:#C5221F}} li.review{{color:#B26A00}} small{{color:#5F6B7A}}</style></head><body>
<h1>Audit report: {html.escape(report.get('company') or 'pitch')}</h1>
<p>Audit {report['audit_id']} of pitch {report.get('pitch_id')} · {report['generated_at']} · Auditor: {html.escape(str(report['auditor']))}<br>
Policy documents: {', '.join(html.escape(p['name'] + ' (' + p['file'] + ')') for p in report['policies_audited'])}</p>
<div class="sum"><div class="big">{s['overall']}</div><div><b>Grounding score {s['grounding_score']}%</b><br>{html.escape(s['headline'])}<br>
Verified {counts['verified']} · Review {counts['review']} · Fail {counts['fail']} · Info {counts['info']}</div></div>
<table><tr><th>Claim</th><th>Status</th><th>Statement and checks</th><th>Traced to</th></tr>{''.join(rows)}</table>
<h3>How the advisor uses this report</h3><ul>{''.join('<li>' + html.escape(g) + '</li>' for g in report['advisor_guidance'])}</ul>
</body></html>"""


def save_report(report: dict) -> dict:
    """Write JSON, CSV and HTML copies to outputs/audits/. Returns the paths."""
    folder = config.OUTPUT_DIR / "audits"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{_safe(report.get('company'))}_{report['audit_id']}"
    paths = {"json": folder / f"{stem}.json", "csv": folder / f"{stem}.csv", "html": folder / f"{stem}.html"}
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["csv"].write_text(report_csv(report), encoding="utf-8-sig")
    paths["html"].write_text(report_html(report), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
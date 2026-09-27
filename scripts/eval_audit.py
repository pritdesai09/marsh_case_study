"""Measure the audit layer: plant known errors in a real pitch and count how many the audit catches.

How it works
  1. Take a generated pitch (default: the newest one in outputs/pitches/).
  2. Audit it unchanged (the baseline).
  3. Plant 20 errors of 12 kinds into copies of the pitch, one error per claim. Each error is built
     from the real claim and checked to be genuinely wrong (e.g. a changed amount is never one
     the brochure clause contains).
  4. Also plant 4 harmless rewordings (same facts, different words) that should NOT fail.
  5. Audit the copies twice: code checks only, and code checks + AI meaning check.

Results
  caught    = the planted error was marked FAIL (the deck can't be sent until it's fixed or removed)
  flagged   = marked FAIL or REVIEW (a human must look at it before the deck can be sent)
  missed    = marked VERIFIED: the error would reach the client
  false alarm = a harmless rewording, or an untouched claim, that the audit marked FAIL

Usage (from the project folder, venv active):
  python scripts/eval_audit.py                          # newest pitch; about 6-10 Flash Lite calls
  python scripts/eval_audit.py outputs/pitches/X.json   # a specific pitch
  python scripts/eval_audit.py --no-ai                  # code checks only; no AI calls
Writes outputs/eval/audit_eval_<time>.md (for the write-up) and .json (full detail).
"""
import argparse
import copy
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import audit, config, fact_store  # noqa: E402
from src.fact_store import extract_numbers  # noqa: E402
from src.pitch import QUALIFIER, QUALIFIER_WORDS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
for noisy in ("httpx", "google_genai", "google", "src.audit"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

MAX_ROUNDS = 3


# ---------------------------------------------------------------- helpers

def indian(n: int) -> str:
    """12345678 -> 1,23,45,678 (the format Indian brochures use)."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{tail}"


_NUM = re.compile(r"(?<![A-Za-z\d-])(\d[\d,]*)(?![\d])(\s*(?:lacs?|lakhs?|lac|crores?|cr)\b)?", re.IGNORECASE)


class Context:
    def __init__(self, pitch: dict):
        self.pitch = pitch
        self.facts = {f["fact_id"]: f for f in fact_store.load_facts(pitch["policies"])}
        self.fields = (pitch.get("profile") or {}).get("fields", {})
        self.scores = {p["score"] for p in pitch.get("ranking", [])}

    def cited(self, claim) -> list[dict]:
        return [self.facts[i] for i in claim.get("fact_ids", []) if i in self.facts]

    def pool(self, claim) -> set[float]:
        """Numbers the audit would accept for this claim (same rule as audit.py)."""
        nums = set()
        for f in self.cited(claim):
            nums |= extract_numbers(" ".join([f["benefit"], f["value"], f.get("conditions", ""), f["quote"]]))
        for k in claim.get("profile_fields", []):
            if k in self.fields:
                nums |= extract_numbers(str(self.fields[k].get("display", "")))
        return nums


def is_policy(claim, ctx) -> bool:
    return claim.get("claim_type") == "policy" and bool(ctx.cited(claim))


def change_number(text: str, pool: set[float]) -> str | None:
    """Replace the first amount/duration with a different one that is NOT in the cited clause."""
    for m in _NUM.finditer(text):
        raw, unit = m.group(1), m.group(2) or ""
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        if n <= 0:
            continue
        mult = 1e5 if re.search(r"la", unit, re.I) else 1e7 if re.search(r"cr", unit, re.I) else 1
        for factor in (2, 3, 4, 1.5, 6):
            new = n * factor
            if new != int(new):
                continue
            new = int(new)
            if new in pool or new * mult in pool:
                continue
            new_raw = indian(new) if "," in raw else str(new)
            return text[:m.start(1)] + new_raw + text[m.end(1):]
    return None


QUAL_PAREN = re.compile(r"\s*\((?:[^()]*?:\s*)?(?:optional|via a separate add-on|on select plans|add-on)[^()]*\)", re.IGNORECASE)


def drop_qualifier(text: str) -> str | None:
    new = QUAL_PAREN.sub("", text).strip()
    lowered = new.lower()
    words = [w for ws in QUALIFIER_WORDS.values() for w in ws]
    if new == text or any(w in lowered for w in words):
        return None
    return new


def lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


# ---------------------------------------------------------------- the planted errors
# Each returns the changed claim, or None if this claim isn't a suitable target.

def m_number_changed(claim, slide, ctx):
    if not is_policy(claim, ctx):
        return None
    new = change_number(claim["text"], ctx.pool(claim))
    return {**claim, "text": new} if new else None


def m_number_added(claim, slide, ctx):
    if not is_policy(claim, ctx) or slide["type"] == "comparison":
        return None
    pool = ctx.pool(claim)
    amount = next(a for a in (25, 35, 45, 55, 65) if a not in pool and a * 1e5 not in pool)
    return {**claim, "text": f"{claim['text'].rstrip('.')}, up to ₹{amount} lakh per year."}


def m_qualifier_dropped(claim, slide, ctx):
    if not is_policy(claim, ctx) or not any(f["coverage_type"] in QUALIFIER for f in ctx.cited(claim)):
        return None
    new = drop_qualifier(claim["text"])
    return {**claim, "text": new} if new else None


def m_wrong_insurer(claim, slide, ctx):
    if not is_policy(claim, ctx):
        return None
    cited = {f["policy"] for f in ctx.cited(claim)}
    others = [c for c in list(ctx.pitch["policies"]) + list(config.POLICIES) if c not in cited]
    if not others:
        return None
    name = config.POLICIES[others[0]]["name"]
    return {**claim, "text": f"Under {name}, {lower_first(claim['text'])}"}


def m_company_number(claim, slide, ctx):
    if claim.get("claim_type") != "company":
        return None
    new = change_number(claim["text"], ctx.pool(claim))
    if new:
        return {**claim, "text": new}
    return {**claim, "text": f"{claim['text'].rstrip('.')}, with over 5,00,000 employees."}


def m_company_year(claim, slide, ctx):
    if claim.get("claim_type") != "company":
        return None
    m = re.search(r"\b(19|20)\d\d\b", claim["text"])
    if m:
        year = int(m.group(0)) - 6
        return {**claim, "text": claim["text"][:m.start()] + str(year) + claim["text"][m.end():]}
    return {**claim, "text": f"{claim['text'].rstrip('.')}. It was founded in 1968."}


def m_score_changed(claim, slide, ctx):
    if claim.get("claim_type") != "assumption" or audit.POLICY_LANGUAGE.search(claim["text"]):
        return None
    fake = next(s for s in (97, 93, 91, 99, 88) if s not in ctx.scores)
    m = re.search(r"\bscore of (\d+)", claim["text"])
    if m:
        return {**claim, "text": claim["text"][:m.start(1)] + str(fake) + claim["text"][m.end(1):]}
    return {**claim, "text": f"{claim['text'].rstrip('.')}. It has a fit score of {fake}/100 for this workforce."}


def m_fabricated_detail(claim, slide, ctx):
    if not is_policy(claim, ctx) or slide["type"] == "comparison":
        return None
    return {**claim, "text": f"{claim['text'].rstrip('.')}, and also covers dental and cosmetic treatment."}


def m_negation(claim, slide, ctx):
    if not is_policy(claim, ctx):
        return None
    f = ctx.cited(claim)[0]
    return {**claim, "text": f"{f['benefit']} is not covered by this policy.", "short_text": None}


def m_swapped_citation(claim, slide, ctx):
    """Keep the citation, but state a different benefit of the same policy (the source doesn't back it)."""
    if not is_policy(claim, ctx) or slide["type"] == "comparison":
        return None
    f = ctx.cited(claim)[0]
    others = [o for o in ctx.facts.values() if o["policy"] == f["policy"] and o["fact_id"] != f["fact_id"]
              and o["category"] != f.get("category") and o["status"] in ("auto_verified", "human_verified")]
    if not others:
        return None
    o = others[0]
    qual = f" ({QUALIFIER[o['coverage_type']]})" if o["coverage_type"] in QUALIFIER else ""
    return {**claim, "text": f"{o['benefit']}: {o['value']}{qual}."}


INVENTIONS = [
    "Includes free international travel insurance for the whole family.",
    "Pays a wellness allowance of ₹20,000 a year for gym memberships.",
]


def m_uncited_invention(claim, slide, ctx):
    if slide["type"] not in ("risk_benefits", "recommendation") or claim.get("claim_type") != "policy":
        return None
    ctx.invention = getattr(ctx, "invention", -1) + 1
    return {**claim, "text": INVENTIONS[ctx.invention % len(INVENTIONS)], "fact_ids": [], "uncited": True, "sources": []}


def m_exaggeration(claim, slide, ctx):
    if not is_policy(claim, ctx) or slide["type"] == "comparison":
        return None
    return {**claim, "text": f"{claim['text'].rstrip('.')}, for every employee with no waiting periods or limits."}


# ---------------------------------------------------------------- harmless rewordings (should NOT fail)

def b_number_reformat(claim, slide, ctx):
    """5,00,000 -> 5 lakh: same amount, different format."""
    if not is_policy(claim, ctx):
        return None
    for m in _NUM.finditer(claim["text"]):
        raw, unit = m.group(1), m.group(2)
        if unit or "," not in raw:
            continue
        n = int(raw.replace(",", ""))
        if n >= 100000 and n % 100000 == 0:
            text = claim["text"][:m.start(1)] + f"{n // 100000} lakh" + claim["text"][m.end(1):]
            return {**claim, "text": text}
    return None


SYNONYMS = [("up to", "a maximum of"), ("provides", "offers"), ("covers", "pays for"), ("includes", "comes with"),
            ("offers", "provides"), ("gives", "provides"), ("protects", "safeguards")]


def b_synonym(claim, slide, ctx):
    if not is_policy(claim, ctx):
        return None
    for old, new in SYNONYMS:
        m = re.search(rf"\b{old}\b", claim["text"], re.IGNORECASE)
        if m:
            replacement = new[:1].upper() + new[1:] if m.group(0)[:1].isupper() else new
            return {**claim, "text": claim["text"][:m.start()] + replacement + claim["text"][m.end():]}
    return None


# (category, description, how it should be caught, mutation, how many)
ERRORS = [
    ("number_changed", "An amount, limit or duration changed", "code", m_number_changed, 3),
    ("number_added", "An invented limit added to a true statement", "code", m_number_added, 2),
    ("qualifier_dropped", "Optional / add-on / plan-dependent benefit shown as included", "code", m_qualifier_dropped, 2),
    ("wrong_insurer", "Benefit attributed to a different insurer", "code", m_wrong_insurer, 2),
    ("company_number", "Wrong company figure (e.g. headcount)", "code", m_company_number, 1),
    ("company_year", "Wrong company fact (founding year)", "code", m_company_year, 1),
    ("score_changed", "Fit score that doesn't match the scoring table", "code", m_score_changed, 1),
    ("fabricated_detail", "True statement plus an invented extra benefit", "ai", m_fabricated_detail, 2),
    ("negation", "Says a benefit is NOT covered when the brochure says it is", "ai", m_negation, 2),
    ("swapped_citation", "Statement that its cited clause doesn't support", "ai", m_swapped_citation, 1),
    ("uncited_invention", "Invented benefit with no citation", "ai", m_uncited_invention, 2),
    ("exaggeration", "Overstated scope ('no waiting periods or limits')", "ai", m_exaggeration, 1),
]
BENIGN = [
    ("number_reformat", "Same amount written differently (5,00,000 -> 5 lakh)", b_number_reformat, 2),
    ("synonym", "Same meaning, different verb", b_synonym, 2),
]


# ---------------------------------------------------------------- building the test pitches

def all_claims(pitch):
    for slide in pitch["slides"]:
        for claim in slide.get("claims", []) or []:
            yield slide, claim


def replace_claim(pitch, claim_id, new_claim):
    """Swap the claim everywhere it appears (slide claims, risk rows and comparison cells share it)."""
    for slide in pitch["slides"]:
        slide["claims"] = [new_claim if c.get("id") == claim_id else c for c in slide.get("claims", []) or []]
        for row in slide.get("rows", []) or []:
            if row["claim"].get("id") == claim_id:
                row["claim"] = new_claim
        for row in (slide.get("table") or {}).get("rows", []):
            row["cells"] = [new_claim if c.get("id") == claim_id else c for c in row["cells"]]


def plan_cases(pitch, ctx):
    """Assign every planted error / rewording to a different claim; spill into extra rounds if needed."""
    wanted = [(cat, desc, expect, fn, "error") for cat, desc, expect, fn, n in ERRORS for _ in range(n)]
    wanted += [(cat, desc, "none", fn, "benign") for cat, desc, fn, n in BENIGN for _ in range(n)]
    rounds, cases, skipped = [set() for _ in range(MAX_ROUNDS)], [], []
    used_per_category = Counter()
    for cat, desc, expect, fn, kind in wanted:
        placed = False
        for r in range(MAX_ROUNDS):
            # prefer claims no earlier case of this category used, so the same claim isn't tested twice
            for slide, claim in all_claims(pitch):
                cid = claim.get("id")
                if cid in rounds[r] or any(c["claim_id"] == cid and c["category"] == cat for c in cases):
                    continue
                new = fn(copy.deepcopy(claim), slide, ctx)
                if not new or new["text"] == claim["text"]:
                    continue
                new.pop("short_text", None)
                rounds[r].add(cid)
                cases.append({"round": r, "category": cat, "description": desc, "kind": kind, "expected": expect,
                              "claim_id": cid, "slide": slide["n"], "original": claim["text"], "planted": new["text"],
                              "_claim": new})
                used_per_category[cat] += 1
                placed = True
                break
            if placed:
                break
        if not placed:
            skipped.append(cat)
    return cases, skipped


def build_round(pitch, cases, r):
    p = copy.deepcopy(pitch)
    for case in cases:
        if case["round"] == r:
            replace_claim(p, case["claim_id"], copy.deepcopy(case["_claim"]))
    return p


def run_audit(pitch, use_ai):
    return audit.audit_pitch_content(pitch["slides"], pitch["policies"], profile=pitch.get("profile"),
                                     ranking=pitch.get("ranking"), pitch_meta=pitch, use_ai=use_ai)


def caught_by(entry) -> str:
    failing = [c["name"] for c in entry["checks"] if c["result"] == entry["status"]]
    if not failing:
        return ""
    return "AI meaning check" if failing == ["meaning"] else "code: " + ", ".join(n for n in failing if n != "meaning")


# ---------------------------------------------------------------- evaluation

def evaluate(pitch, modes):
    ctx = Context(pitch)
    cases, skipped = plan_cases(pitch, ctx)
    n_rounds = max((c["round"] for c in cases), default=-1) + 1
    results = {}
    for mode in modes:
        use_ai = mode == "code+AI"
        print(f"\n[{mode}] auditing the unchanged pitch (baseline)…")
        baseline = {c["id"]: c for c in run_audit(pitch, use_ai)["claims"]}
        by_case, untouched_alarms, untouched_total = [], [], 0
        for r in range(n_rounds):
            print(f"[{mode}] auditing test pitch {r + 1}/{n_rounds} "
                  f"({sum(1 for c in cases if c['round'] == r)} planted changes)…")
            report = {c["id"]: c for c in run_audit(build_round(pitch, cases, r), use_ai)["claims"]}
            planted_ids = {c["claim_id"] for c in cases if c["round"] == r}
            for case in (c for c in cases if c["round"] == r):
                entry = report[case["claim_id"]]
                by_case.append({**{k: v for k, v in case.items() if k != "_claim"}, "status": entry["status"],
                                "reason": entry["reason"], "caught_by": caught_by(entry)})
            for cid, entry in report.items():
                if cid in planted_ids:
                    continue
                untouched_total += 1
                if entry["status"] == "fail" and baseline[cid]["status"] != "fail":
                    untouched_alarms.append({"claim_id": cid, "text": entry["text"], "reason": entry["reason"]})
        results[mode] = {"cases": by_case, "untouched_total": untouched_total, "untouched_alarms": untouched_alarms,
                         "baseline_counts": dict(Counter(c["status"] for c in baseline.values()))}
    return cases, skipped, results


def summarise(mode_result):
    errors = [c for c in mode_result["cases"] if c["kind"] == "error"]
    benign = [c for c in mode_result["cases"] if c["kind"] == "benign"]
    return {
        "planted": len(errors),
        "caught": sum(c["status"] == "fail" for c in errors),
        "flagged": sum(c["status"] in ("fail", "review") for c in errors),
        "missed": [c for c in errors if c["status"] in ("verified", "info")],
        "benign": len(benign),
        "benign_alarms": [c for c in benign if c["status"] == "fail"],
        "benign_review": sum(c["status"] == "review" for c in benign),
        "untouched": mode_result["untouched_total"],
        "untouched_alarms": mode_result["untouched_alarms"],
    }


def skipped_text(skipped):
    wanted = {cat: n for cat, *_, n in ERRORS} | {cat: n for cat, _, _, n in BENIGN}
    return ", ".join(f"{cat} ({n} of {wanted[cat]})" for cat, n in sorted(Counter(skipped).items()))


def pct(a, b):
    return f"{round(100 * a / b)}%" if b else "n/a"


def markdown(pitch, skipped, results) -> str:
    lines = [f"# Audit evaluation: {pitch['company']}", "",
             f"Pitch `{pitch['id']}` (recommended {pitch['recommended']}), policies {', '.join(pitch['policies'])}. "
             f"Run {datetime.now().strftime('%Y-%m-%d %H:%M')}.", "",
             "Known errors were planted into copies of a real generated pitch, one per claim, and the copies were audited. "
             "**Caught** = FAIL (the deck can't be sent until the claim is fixed or removed). "
             "**Flagged** = FAIL or REVIEW (a human must look before the deck can be sent). "
             "**Missed** = VERIFIED (the error would reach the client). "
             "Harmless rewordings measure false alarms.", "",
             "## Summary", "",
             "| Mode | Errors caught (FAIL) | Errors flagged (FAIL or REVIEW) | Missed | False alarms on harmless rewordings | False alarms on untouched claims |",
             "|---|---|---|---|---|---|"]
    for mode, res in results.items():
        s = summarise(res)
        lines.append(f"| {mode} | {s['caught']}/{s['planted']} ({pct(s['caught'], s['planted'])}) | "
                     f"{s['flagged']}/{s['planted']} ({pct(s['flagged'], s['planted'])}) | {len(s['missed'])} | "
                     f"{len(s['benign_alarms'])}/{s['benign']} | {len(s['untouched_alarms'])}/{s['untouched']} |")
    lines += ["", "## By error type", ""]
    modes = list(results)
    header = "| Error type | Expected to be caught by | " + " | ".join(modes) + " |"
    lines += [header, "|---|---|" + "---|" * len(modes)]
    categories = list(dict.fromkeys(c["category"] for c in results[modes[0]]["cases"] if c["kind"] == "error"))
    for cat in categories:
        row = [cat.replace("_", " "), next(e[2] for e in ERRORS if e[0] == cat).replace("ai", "AI meaning check")]
        for mode in modes:
            cs = [c for c in results[mode]["cases"] if c["category"] == cat]
            row.append(f"{sum(c['status'] == 'fail' for c in cs)}/{len(cs)} caught"
                       + (f", {sum(c['status'] == 'review' for c in cs)} to review" if any(c['status'] == 'review' for c in cs) else ""))
        lines.append("| " + " | ".join(row) + " |")

    last = modes[-1]
    lines += ["", f"## Every planted change ({last})", "",
              "| # | Type | Claim | Planted text | Result | Caught by / reason |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(results[last]["cases"], 1):
        planted = c["planted"].replace("|", "/")
        why = (c["caught_by"] or c["reason"]).replace("|", "/")
        lines.append(f"| {i} | {c['category'].replace('_', ' ')}{' (harmless)' if c['kind'] == 'benign' else ''} | "
                     f"{c['claim_id']} | {planted[:110]} | **{c['status'].upper()}** | {why[:120]} |")
    s = summarise(results[last])
    if s["missed"]:
        lines += ["", "## Missed", ""] + [f"- {c['claim_id']} ({c['category']}): {c['planted']}" for c in s["missed"]]
    if s["benign_alarms"] or s["untouched_alarms"]:
        lines += ["", "## False alarms", ""]
        lines += [f"- {c['claim_id']} (harmless {c['category']}): {c['planted']} -> {c['reason']}" for c in s["benign_alarms"]]
        lines += [f"- {a['claim_id']} (untouched): {a['text']} -> {a['reason']}" for a in s["untouched_alarms"]]
    if skipped:
        lines += ["", f"Not tested (no suitable claim in this pitch): {skipped_text(skipped)}."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pitch", nargs="?", help="Pitch JSON (default: newest in outputs/pitches/)")
    parser.add_argument("--no-ai", action="store_true", help="Code checks only (no AI calls)")
    args = parser.parse_args()

    if args.pitch:
        path = Path(args.pitch)
    else:
        pitches = sorted((config.OUTPUT_DIR / "pitches").glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not pitches:
            sys.exit("No saved pitches in outputs/pitches/. Generate one in the app first.")
        path = pitches[-1]
    pitch = json.loads(path.read_text(encoding="utf-8"))
    print(f"Evaluating the audit on {path.name} ({pitch['company']}, {len(list(all_claims(pitch)))} claims)")

    modes = ["code only"] + ([] if args.no_ai or config.DEMO_MODE else ["code+AI"])
    cases, skipped, results = evaluate(pitch, modes)

    print("\n" + "=" * 78)
    for mode, res in results.items():
        s = summarise(res)
        print(f"{mode:10}  caught {s['caught']}/{s['planted']}  flagged {s['flagged']}/{s['planted']}  "
              f"missed {len(s['missed'])}  |  false alarms: rewordings {len(s['benign_alarms'])}/{s['benign']}, "
              f"untouched {len(s['untouched_alarms'])}/{s['untouched']}")
    last = summarise(results[modes[-1]])
    for c in last["missed"]:
        print(f"  MISSED  {c['claim_id']:6} {c['category']:18} {c['planted'][:70]}")
    for c in last["benign_alarms"]:
        print(f"  FALSE ALARM {c['claim_id']:6} {c['category']:14} {c['planted'][:60]} -> {c['reason'][:60]}")
    if skipped:
        print(f"  Not tested (no suitable claim): {skipped_text(skipped)}")

    folder = config.OUTPUT_DIR / "eval"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md, js = folder / f"audit_eval_{stamp}.md", folder / f"audit_eval_{stamp}.json"
    md.write_text(markdown(pitch, skipped, results), encoding="utf-8")
    js.write_text(json.dumps({"pitch": path.name, "skipped": skipped, "results": results,
                              "summary": {m: {k: (len(v) if isinstance(v, list) else v) for k, v in summarise(r).items()}
                                          for m, r in results.items()}},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {md}\nDetail: {js}")


if __name__ == "__main__":
    main()
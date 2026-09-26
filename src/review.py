"""Advisor review: approve, edit or reject each audited claim, then export the approved deck.

Everything is stored on the server in the audit report file (outputs/audits/...json), so:
  - the browser can't bypass the audit by sending its own version of the pitch or decisions,
  - every approval, edit and rejection is recorded with a timestamp (audit trail),
  - the final HTML report shows both the automatic audit and the human sign-off.

Rules for export (enforced here, not just in the browser):
  FAIL    -> must be edited until it passes, or rejected (removed from the deck)
  REVIEW  -> must be approved, edited until verified, or rejected
  VERIFIED / INFO -> no action needed (can still be edited or rejected)
"""
import json
import re
from datetime import datetime, timezone

from src import audit, config, pptx_builder

ACTIONS = ("approve", "reject", "reset", "edit", "revert")


class ReviewError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _find(folder: str, item_id: str, kind: str):
    if not re.fullmatch(r"[0-9a-f]{10}", item_id or ""):
        raise ReviewError(f"Unknown {kind}.")
    matches = sorted((config.OUTPUT_DIR / folder).glob(f"*_{item_id}.json"))
    if not matches:
        raise ReviewError(f"Unknown {kind}. Generate the pitch again.")
    return matches[0]


def load_pitch(pitch_id: str) -> dict:
    return json.loads(_find("pitches", pitch_id, "pitch").read_text(encoding="utf-8"))


def load_report(audit_id: str) -> dict:
    report = json.loads(_find("audits", audit_id, "audit").read_text(encoding="utf-8"))
    report.setdefault("review", {"decisions": {}, "edits": {}, "exported_at": None, "history": []})
    return report


def start(pitch_id: str) -> dict:
    """Audit a saved pitch and open a review for it."""
    pitch = load_pitch(pitch_id)
    report = audit.audit_pitch_content(pitch["slides"], pitch["policies"], profile=pitch.get("profile"),
                                       ranking=pitch.get("ranking"), pitch_meta=pitch)
    report["review"] = {"decisions": {}, "edits": {}, "exported_at": None, "history": []}
    audit.save_report(report)
    return view(report)


# ---------------------------------------------------------------- state

def claim_state(claim: dict, review: dict) -> str:
    """What the advisor has done with a claim, or still has to do."""
    decision = review["decisions"].get(claim["id"], {}).get("action")
    if decision == "reject":
        return "rejected"
    if decision == "approve":
        return "approved"
    if claim["status"] == "fail":
        return "must_fix"
    if claim["status"] == "review":
        return "needs_approval"
    return "ok"


def review_summary(report: dict) -> dict:
    review = report["review"]
    states = {c["id"]: claim_state(c, review) for c in report["claims"]}
    blockers = [cid for cid, s in states.items() if s in ("must_fix", "needs_approval")]
    must_fix = sum(1 for s in states.values() if s == "must_fix")
    needs = sum(1 for s in states.values() if s == "needs_approval")
    if blockers:
        parts = ([f"{must_fix} to fix or remove"] if must_fix else []) + ([f"{needs} to approve"] if needs else [])
        message = "Resolve " + " and ".join(parts) + " before downloading."
    else:
        message = "All claims resolved. The deck can be downloaded."
    return {
        "states": states,
        "blockers": blockers,
        "ready": not blockers,
        "message": message,
        "counts": {k: sum(1 for s in states.values() if s == k)
                   for k in ("ok", "approved", "rejected", "must_fix", "needs_approval")},
        "edited": sorted(review["edits"]),
        "exported_at": review.get("exported_at"),
    }


def _refresh_summary(report: dict):
    """Recalculate the audit summary for the deck as it now stands: edits applied, rejected claims left out.

    The first audit's summary is kept in initial_summary, so the report shows before and after.
    """
    report.setdefault("initial_summary", dict(report["summary"]))
    rejected = {cid for cid, d in report["review"]["decisions"].items() if d.get("action") == "reject"}
    claims = [c for c in report["claims"] if c["id"] not in rejected]
    counts = {s: sum(1 for c in claims if c["status"] == s) for s in ("verified", "review", "fail", "info")}
    checkable = counts["verified"] + counts["review"] + counts["fail"]
    overall = "FAIL" if counts["fail"] else "REVIEW" if counts["review"] else "PASS"
    report["summary"].update(
        counts=counts,
        claims_total=len(claims),
        grounding_score=round(100 * (counts["verified"] + 0.5 * counts["review"]) / checkable) if checkable else 0,
        overall=overall,
        rejected=len(rejected),
        headline={
            "PASS": "Every claim in the deck is traced to a verified clause.",
            "REVIEW": f"No errors in the deck; {counts['review']} claim(s) rely on the advisor's approval.",
            "FAIL": f"{counts['fail']} claim(s) in the deck are not supported by the policy documents.",
        }[overall],
    )


def view(report: dict) -> dict:
    """The report plus the advisor's progress (what the browser renders)."""
    return {**report, "review_summary": review_summary(report)}


# ---------------------------------------------------------------- actions

def act(audit_id: str, claim_id: str, action: str, text: str | None = None) -> dict:
    if action not in ACTIONS:
        raise ReviewError(f"Unknown action '{action}'.")
    report = load_report(audit_id)
    review = report["review"]
    index = next((i for i, c in enumerate(report["claims"]) if c["id"] == claim_id), None)
    if index is None:
        raise ReviewError(f"No claim with id {claim_id}.")
    claim = report["claims"][index]

    if action == "approve":
        if claim["status"] == "fail":
            raise ReviewError("A failed claim can't be approved. Edit it so it matches the source, or reject it.")
        review["decisions"][claim_id] = {"action": "approve", "at": _now()}
    elif action == "reject":
        review["decisions"][claim_id] = {"action": "reject", "at": _now()}
    elif action == "reset":
        review["decisions"].pop(claim_id, None)
    elif action == "edit":
        text = (text or "").strip()
        if not text:
            raise ReviewError("The edited text is empty.")
        if text == claim["text"]:
            raise ReviewError("The text hasn't changed.")
        pitch = load_pitch(report["pitch_id"])
        new_entry = audit.audit_single_claim(pitch, claim_id, text)
        original = review["edits"].get(claim_id, {}).get("original_entry") or claim
        review["edits"][claim_id] = {"text": text, "at": _now(), "original_entry": original}
        review["decisions"].pop(claim_id, None)  # the old approval was for the old wording
        report["claims"][index] = {**new_entry, "edited": True, "original_text": original["text"]}
    elif action == "revert":
        edit = review["edits"].pop(claim_id, None)
        if not edit:
            raise ReviewError("This claim hasn't been edited.")
        review["decisions"].pop(claim_id, None)
        report["claims"][index] = edit["original_entry"]

    _refresh_summary(report)
    review["history"].append({"at": _now(), "claim_id": claim_id, "action": action,
                              **({"text": text} if action == "edit" else {})})
    review["exported_at"] = None  # any change means the last export is out of date
    audit.save_report(report)
    return view(report)


# ---------------------------------------------------------------- export

def _deck_decisions(report: dict) -> dict:
    """Translate the review into what the PowerPoint builder needs."""
    review, out = report["review"], {}
    for c in report["claims"]:
        d = {}
        state = claim_state(c, review)
        if state == "rejected":
            d["status"] = "rejected"
        if c["id"] in review["edits"]:
            d["text"] = review["edits"][c["id"]]["text"]
        if state == "approved" or c["status"] == "verified":
            d["approved"] = True
            # Uncited claims the advisor approved: cite the brochure passage the audit traced them to
            traced = [e for e in c.get("evidence", []) if e.get("kind") == "retrieved"]
            if traced and not any(e.get("kind") == "fact" for e in c.get("evidence", [])):
                d["sources"] = [{"label": traced[0]["source"]}]
        if d:
            out[c["id"]] = d
    return out


def export(audit_id: str) -> tuple[bytes, str]:
    report = load_report(audit_id)
    summary = review_summary(report)
    if not summary["ready"]:
        raise ReviewError(summary["message"])
    pitch = load_pitch(report["pitch_id"])
    note = f"Audited and approved by advisor | Audit {audit_id}"
    data = pptx_builder.build_pptx(pitch, _deck_decisions(report), footer_note=note)
    report["review"]["exported_at"] = _now()
    report["review"]["history"].append({"at": report["review"]["exported_at"], "action": "export"})
    audit.save_report(report)
    name = pptx_builder.filename_for(pitch)
    (config.OUTPUT_DIR / "pitches").mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "pitches" / name).write_bytes(data)
    return data, name
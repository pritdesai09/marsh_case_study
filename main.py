import logging
import re
from collections import Counter

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from src import audit, config, fact_store, pitch, pptx_builder, profile
from src.schemas import AuditRequest, ClaimAuditRequest, ExportRequest, PitchRequest, ProfileRequest

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
for noisy in ("httpx", "google_genai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

app = FastAPI(title="Marsh Pitch Generator", version="0.5.0")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": app.version,
        "mode": "demo" if config.DEMO_MODE else "live",
        "provider": config.LLM_PROVIDER,
        "model": config.GEMINI_MODEL or "not set",
        "fact_store_built_at": fact_store.load_store().get("generated_at"),
    }


@app.get("/api/policies")
def list_policies():
    facts = fact_store.load_facts()
    result = []
    for code, p in config.POLICIES.items():
        counts = Counter(f["status"] for f in facts if f["policy"] == code)
        result.append({
            "code": code,
            "name": p["name"],
            "insurer": p["insurer"],
            "available": (config.POLICY_DIR / p["file"]).exists(),
            "facts": sum(counts.values()),
            "verified": counts["auto_verified"] + counts["human_verified"],
            "needs_review": counts["needs_review"],
        })
    return result


@app.get("/api/facts")
def list_facts(policy: str | None = None):
    if policy and policy not in config.POLICIES:
        raise HTTPException(status_code=404, detail=f"Unknown policy code '{policy}'")
    store = fact_store.load_store()
    facts = [f for f in store["facts"] if not policy or f["policy"] == policy]
    return {"generated_at": store.get("generated_at"), "count": len(facts), "facts": facts}


@app.get("/api/company-search")
def company_search(q: str = ""):
    """Suggestions for the company search box. Never errors: returns [] if Wikidata is unreachable."""
    q = q.strip()
    if len(q) < 2 or len(q) > 100:
        return []
    try:
        return list(profile.search_companies(q))
    except (requests.RequestException, ValueError, KeyError):
        return []


@app.post("/api/profile")
def create_profile(req: ProfileRequest):
    """generateCompanyProfile: sourced facts from Wikidata/Wikipedia + labelled assumptions."""
    try:
        return profile.generate_company_profile(req.company, wikidata_id=req.wikidata_id)
    except profile.ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pitch")
def create_pitch(req: PitchRequest):
    """generateMarketingPitch: score the selected policies and write the 5 slides."""
    try:
        return pitch.generate_marketing_pitch(req.profile, req.policies)
    except pitch.PitchError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/export")
def export_pptx(req: ExportRequest):
    """Render the pitch as a PowerPoint file."""
    if not isinstance(req.pitch.get("slides"), list) or not req.pitch.get("ranking"):
        raise HTTPException(status_code=400, detail="Generate a pitch before downloading.")
    try:
        data = pptx_builder.build_pptx(req.pitch, req.decisions)
    except (KeyError, TypeError, ValueError, StopIteration) as e:
        logging.getLogger(__name__).exception("PPTX export failed")
        raise HTTPException(status_code=400, detail=f"This pitch could not be turned into slides ({type(e).__name__}).")
    name = pptx_builder.filename_for(req.pitch)
    (config.OUTPUT_DIR / "pitches").mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "pitches" / name).write_bytes(data)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/audit")
def audit_pitch(req: AuditRequest):
    """auditPitchContent: trace every claim to a policy clause; returns the structured audit report."""
    try:
        return audit.audit_pitch(req.pitch)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"This pitch could not be audited: {e}")


@app.post("/api/audit/claim")
def audit_one_claim(req: ClaimAuditRequest):
    """Re-audit a single claim after the advisor edits its wording."""
    try:
        return audit.audit_single_claim(req.pitch, req.claim_id, req.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


AUDIT_TYPES = {"html": "text/html", "csv": "text/csv", "json": "application/json"}


@app.get("/api/audit/{audit_id}/report.{fmt}")
def download_audit(audit_id: str, fmt: str):
    """Download a saved audit report (the 'audit results' deliverable) as HTML, CSV or JSON."""
    if not re.fullmatch(r"[0-9a-f]{10}", audit_id) or fmt not in AUDIT_TYPES:
        raise HTTPException(status_code=404, detail="Report not found")
    matches = sorted((config.OUTPUT_DIR / "audits").glob(f"*_{audit_id}.{fmt}"))
    if not matches:
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(matches[0], media_type=AUDIT_TYPES[fmt], filename=matches[0].name)


# Website: "/" serves index.html; CSS/JS are served from /static
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
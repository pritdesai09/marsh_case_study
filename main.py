import logging
import re
from collections import Counter

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from src import config, fact_store, pitch, profile, review, uploads
from src.schemas import AuditRequest, ClaimActionRequest, ExportRequest, PitchRequest, ProfileRequest

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
for noisy in ("httpx", "google_genai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

app = FastAPI(title="Marsh Pitch Generator", version="0.6.0")


@app.middleware("http")
async def refresh_policies(request: Request, call_next):
    """Pick up policies uploaded through another server worker."""
    if request.url.path.startswith("/api/"):
        config.refresh_policies()
    return await call_next(request)


# ---------------------------------------------------------------- status & policy library

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
    uploads.mark_stale()
    facts = fact_store.load_facts()
    result = []
    for code, p in config.POLICIES.items():
        counts = Counter(f["status"] for f in facts if f["policy"] == code)
        result.append({
            "code": code,
            "name": p["name"],
            "insurer": p["insurer"],
            "available": (config.POLICY_DIR / p["file"]).exists(),
            "uploaded": bool(p.get("uploaded")),
            "status": p.get("status", "ready"),
            "job_id": p.get("job_id"),
            "error": p.get("error"),
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


@app.post("/api/policies/upload")
async def upload_policy(file: UploadFile = File(...), name: str = Form(...), insurer: str = Form(...)):
    """Add a policy brochure: facts are extracted and verified in the background (poll /api/jobs/{id})."""
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    try:
        return uploads.start_upload(data, name, insurer)
    except uploads.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/jobs/{job_id}")
def upload_job(job_id: str):
    try:
        return uploads.job_status(job_id)
    except uploads.UploadError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/policies/{code}")
def remove_policy(code: str):
    try:
        uploads.delete_policy(code)
    except uploads.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"removed": code}


# ---------------------------------------------------------------- generate: profile -> pitch -> audit

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
    not_ready = [c for c in req.policies if config.POLICIES.get(c, {}).get("status", "ready") != "ready"]
    if not_ready:
        raise HTTPException(status_code=400, detail=f"Still processing: {', '.join(not_ready)}")
    try:
        return pitch.generate_marketing_pitch(req.profile, req.policies)
    except pitch.PitchError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/pitch/{pitch_id}")
def get_pitch(pitch_id: str):
    try:
        return review.load_pitch(pitch_id)
    except review.ReviewError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/audit")
def audit_pitch(req: AuditRequest):
    """auditPitchContent: trace every claim to a policy clause; returns the report and review status."""
    try:
        return review.start(req.pitch_id)
    except review.ReviewError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"This pitch could not be audited: {e}")


# ---------------------------------------------------------------- advisor review & export

@app.get("/api/audit/{audit_id}")
def get_audit(audit_id: str):
    try:
        return review.view(review.load_report(audit_id))
    except review.ReviewError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/audit/{audit_id}/claims/{claim_id}")
def claim_action(audit_id: str, claim_id: str, req: ClaimActionRequest):
    """Advisor approves, edits (re-audited immediately), rejects, resets or reverts one claim."""
    try:
        return review.act(audit_id, claim_id, req.action, req.text)
    except review.ReviewError as e:
        raise HTTPException(status_code=400, detail=str(e))


AUDIT_TYPES = {"html": "text/html", "csv": "text/csv", "json": "application/json"}


@app.get("/api/audit/{audit_id}/report.{fmt}")
def download_audit(audit_id: str, fmt: str):
    """Download the audit report (the 'audit results' deliverable) as HTML, CSV or JSON."""
    if not re.fullmatch(r"[0-9a-f]{10}", audit_id) or fmt not in AUDIT_TYPES:
        raise HTTPException(status_code=404, detail="Report not found")
    matches = sorted((config.OUTPUT_DIR / "audits").glob(f"*_{audit_id}.{fmt}"))
    if not matches:
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(matches[0], media_type=AUDIT_TYPES[fmt], filename=matches[0].name)


@app.post("/api/export")
def export_pptx(req: ExportRequest):
    """The approved deck. Refused (409) while any claim still fails or awaits approval."""
    try:
        data, name = review.export(req.audit_id)
    except review.ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (KeyError, TypeError, ValueError, StopIteration) as e:
        logging.getLogger(__name__).exception("PPTX export failed")
        raise HTTPException(status_code=400, detail=f"This pitch could not be turned into slides ({type(e).__name__}).")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# Website: "/" serves index.html; CSS/JS are served from /static
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
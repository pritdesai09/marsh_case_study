import logging
from collections import Counter

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import config, fact_store, profile
from src.schemas import ProfileRequest

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
for noisy in ("httpx", "google_genai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

app = FastAPI(title="Marsh Pitch Generator", version="0.3.0")


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


# Website: "/" serves index.html; CSS/JS are served from /static
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
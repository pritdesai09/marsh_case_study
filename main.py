from collections import Counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import config, fact_store

app = FastAPI(title="Marsh Pitch Generator", version="0.2.0")


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


# Website: "/" serves index.html; CSS/JS are served from /static
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import config

app = FastAPI(title="Marsh Pitch Generator", version="0.1.0")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": app.version,
        "mode": "demo" if config.DEMO_MODE else "live",
        "provider": config.LLM_PROVIDER,
        "model": config.GEMINI_MODEL or "not set",
    }


@app.get("/api/policies")
def list_policies():
    return [
        {
            "code": code,
            "name": p["name"],
            "insurer": p["insurer"],
            "available": (config.POLICY_DIR / p["file"]).exists(),
        }
        for code, p in config.POLICIES.items()
    ]


# Website: "/" serves index.html; CSS/JS are served from /static
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")
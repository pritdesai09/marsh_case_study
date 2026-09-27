import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
POLICY_DIR = ROOT / "data" / "policies"
PROCESSED_DIR = ROOT / "data" / "processed"
DEMO_DIR = ROOT / "demo"
# Generated pitches, audit reports and upload jobs. On Azure, MARSH_DATA_DIR points at /home/data,
# which survives restarts and redeploys (the code folder may not).
OUTPUT_DIR = Path(os.environ["MARSH_DATA_DIR"]) / "outputs" if os.getenv("MARSH_DATA_DIR") else ROOT / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# Processed data files (built by scripts/build_fact_store.py)
PAGES_FILE = PROCESSED_DIR / "pages.json"
CHUNKS_FILE = PROCESSED_DIR / "chunks.json"
FACTS_FILE = PROCESSED_DIR / "policy_facts.json"
FACTS_REVIEW_CSV = PROCESSED_DIR / "facts_review.csv"
FACT_OVERRIDES_FILE = PROCESSED_DIR / "fact_overrides.json"  # human review decisions

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
GEMINI_MODEL_STRONG = os.getenv("GEMINI_MODEL_STRONG", "") or GEMINI_MODEL

# Demo mode runs automatically when no API key is set
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true" or not GEMINI_API_KEY

# Built-in brochures. Policies uploaded through the website are added from UPLOADED_POLICIES_FILE.
BUILTIN_POLICIES = {
    "NIVA": {"name": "Niva Bupa ReAssure 2.0", "insurer": "Niva Bupa", "file": "niva_bupa.pdf"},
    "ABHI": {"name": "Aditya Birla Activ One", "insurer": "Aditya Birla Health", "file": "abhi.pdf"},
    "CARE": {"name": "Care Supreme", "insurer": "Care Health", "file": "care.pdf"},
    "HDFC": {"name": "HDFC ERGO Optima Secure", "insurer": "HDFC ERGO", "file": "hdfc.pdf"},
}

UPLOADED_POLICIES_FILE = PROCESSED_DIR / "uploaded_policies.json"
JOBS_DIR = OUTPUT_DIR / "jobs"
MAX_UPLOAD_MB = 15
MAX_UPLOAD_PAGES = 40
MAX_UPLOADED_POLICIES = 6

# One shared dict: every module reads config.POLICIES, so uploads appear everywhere once refreshed
POLICIES = dict(BUILTIN_POLICIES)
_uploads_stamp = None


def refresh_policies() -> dict:
    """Merge uploaded policies into POLICIES (cheap: re-reads only when the registry file changed).

    Called on every API request, so all server workers see an upload made through any of them.
    """
    global _uploads_stamp
    stamp = UPLOADED_POLICIES_FILE.stat().st_mtime if UPLOADED_POLICIES_FILE.exists() else None
    if stamp == _uploads_stamp:
        return POLICIES
    _uploads_stamp = stamp
    uploaded = {}
    if stamp is not None:
        import json
        try:
            uploaded = json.loads(UPLOADED_POLICIES_FILE.read_text(encoding="utf-8"))
        except ValueError:
            uploaded = {}
    POLICIES.clear()
    POLICIES.update(BUILTIN_POLICIES)
    POLICIES.update({code: {**p, "uploaded": True} for code, p in uploaded.items()})
    return POLICIES


refresh_policies()
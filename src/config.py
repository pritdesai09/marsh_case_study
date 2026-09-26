import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
POLICY_DIR = ROOT / "data" / "policies"
PROCESSED_DIR = ROOT / "data" / "processed"
DEMO_DIR = ROOT / "demo"
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")

# Demo mode runs automatically when no API key is set
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true" or not GEMINI_API_KEY

POLICIES = {
    "NIVA": {"name": "Niva Bupa ReAssure 2.0", "insurer": "Niva Bupa", "file": "niva_bupa.pdf"},
    "ABHI": {"name": "Aditya Birla Activ One", "insurer": "Aditya Birla Health", "file": "abhi.pdf"},
    "CARE": {"name": "Care Supreme", "insurer": "Care Health", "file": "care.pdf"},
    "HDFC": {"name": "HDFC ERGO Optima Secure", "insurer": "HDFC ERGO", "file": "hdfc.pdf"},
}
"""Add a new policy brochure through the website.

Flow: validate the PDF -> save it -> register the policy -> in a background thread:
read pages and chunks (ingest) -> AI fact extraction (one strong-model call) -> code validation
of every fact against the PDF text. Progress is written to a small job file so any server
worker can report it, and the new policy appears in every worker via config.refresh_policies().
"""
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone

import pymupdf

from src import config, fact_store, ingest, llm

log = logging.getLogger(__name__)
_build_lock = threading.Lock()  # fact store and chunks are single files: one build at a time


class UploadError(ValueError):
    pass


# ---------------------------------------------------------------- registry

def _load_registry() -> dict:
    if config.UPLOADED_POLICIES_FILE.exists():
        try:
            return json.loads(config.UPLOADED_POLICIES_FILE.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _save_registry(registry: dict):
    config.UPLOADED_POLICIES_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    config.refresh_policies()


def _set_status(code: str, **fields):
    registry = _load_registry()
    if code in registry:
        registry[code].update(fields)
        _save_registry(registry)


def _new_code(insurer: str, taken: set[str]) -> str:
    """Short code from the insurer name, e.g. 'Star Health' -> 'STAR' (STAR2 if taken)."""
    base = (re.sub(r"[^A-Z]", "", insurer.upper().split()[0]) if insurer.split() else "")[:6] or "POL"
    code, n = base, 1
    while code in taken:
        n += 1
        code = f"{base}{n}"
    return code


# ---------------------------------------------------------------- jobs

def _job_path(job_id: str):
    return config.JOBS_DIR / f"{job_id}.json"


def _write_job(job_id: str, **fields):
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = _job_path(job_id)
    job = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"job_id": job_id}
    job.update(fields, updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def job_status(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{10}", job_id or "") or not _job_path(job_id).exists():
        raise UploadError("Unknown upload job.")
    return json.loads(_job_path(job_id).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- upload

def start_upload(data: bytes, name: str, insurer: str) -> dict:
    """Validate and save the PDF, then process it in the background. Returns the job."""
    name, insurer = (name or "").strip(), (insurer or "").strip()
    if not name or not insurer:
        raise UploadError("Enter the policy name and the insurer.")
    if len(name) > 80 or len(insurer) > 60:
        raise UploadError("The policy name or insurer is too long.")
    if config.DEMO_MODE:
        raise UploadError("Reading a new brochure needs the Gemini API key (the app is in demo mode).")
    if not data or not data.startswith(b"%PDF"):
        raise UploadError("That file is not a PDF.")
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise UploadError(f"The PDF is larger than {config.MAX_UPLOAD_MB} MB.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
            words = sum(len(page.get_text("text").split()) for page in doc)
    except Exception as e:  # corrupt or encrypted PDFs raise various errors
        raise UploadError(f"The PDF could not be opened ({type(e).__name__}).") from e
    if pages > config.MAX_UPLOAD_PAGES:
        raise UploadError(f"The PDF has {pages} pages; the limit is {config.MAX_UPLOAD_PAGES}. Upload the brochure, not the full policy wording.")
    if words < 100:
        raise UploadError("The PDF has almost no selectable text (is it a scanned image?). Facts can't be verified against it.")

    registry = _load_registry()
    active = [c for c, p in registry.items() if p.get("status") != "failed"]
    if len(active) >= config.MAX_UPLOADED_POLICIES:
        raise UploadError(f"Up to {config.MAX_UPLOADED_POLICIES} uploaded policies are allowed. Remove one first.")
    if any(p["name"].lower() == name.lower() for p in config.POLICIES.values()):
        raise UploadError(f"A policy called '{name}' already exists.")

    code = _new_code(insurer, set(config.POLICIES))
    filename = f"upload_{code.lower()}.pdf"
    (config.POLICY_DIR / filename).write_bytes(data)
    job_id = uuid.uuid4().hex[:10]
    registry[code] = {"name": name, "insurer": insurer, "file": filename, "status": "processing",
                      "job_id": job_id, "pages": pages,
                      "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _save_registry(registry)
    job = _write_job(job_id, code=code, name=name, state="running", step="Reading the PDF", error=None)
    threading.Thread(target=_process, args=(code, job_id), daemon=True).start()
    return job


def _process(code: str, job_id: str):
    try:
        with _build_lock:
            _write_job(job_id, step="Reading pages and clauses")
            ingest.ingest_all([code])
            pages = ingest.load_pages()
            _write_job(job_id, step="AI is extracting benefits (about 1-2 minutes)")
            facts, errors = fact_store.build([code], pages)
        if code in errors:
            raise UploadError(errors[code])
        mine = [f for f in facts if f["policy"] == code]
        verified = sum(1 for f in mine if f["status"] in ("auto_verified", "human_verified"))
        if not verified:
            raise UploadError("No benefit could be verified against the PDF text, so this policy can't be pitched.")
        _set_status(code, status="ready", facts=len(mine), verified=verified)
        _write_job(job_id, state="done", step="Ready", facts=len(mine), verified=verified,
                   needs_review=len(mine) - verified, model=llm.last_model_used)
    except (UploadError, llm.LLMError, ValueError, OSError) as e:
        log.warning("Upload of %s failed: %s", code, e)
        _set_status(code, status="failed", error=str(e))
        _write_job(job_id, state="failed", step="Failed", error=str(e))
    except Exception as e:  # never leave a job 'running' forever
        log.exception("Upload of %s crashed", code)
        _set_status(code, status="failed", error=type(e).__name__)
        _write_job(job_id, state="failed", step="Failed", error=f"Unexpected error ({type(e).__name__})")


def mark_stale(max_minutes: int = 10):
    """A server restart kills the background thread: don't leave that policy 'processing' forever."""
    now = datetime.now(timezone.utc)
    for code, p in _load_registry().items():
        if p.get("status") != "processing":
            continue
        try:
            updated = datetime.fromisoformat(job_status(p["job_id"])["updated_at"])
        except (UploadError, KeyError, ValueError):
            updated = None
        if updated is None or (now - updated).total_seconds() > max_minutes * 60:
            message = "Processing was interrupted (the server restarted). Remove it and upload again."
            _set_status(code, status="failed", error=message)
            if p.get("job_id"):
                try:
                    _write_job(p["job_id"], state="failed", step="Failed", error=message)
                except OSError:
                    pass


# ---------------------------------------------------------------- remove

def delete_policy(code: str):
    """Remove an uploaded policy: its PDF, pages, chunks and facts. Built-in policies can't be removed."""
    registry = _load_registry()
    if code not in registry:
        raise UploadError("Only uploaded policies can be removed.")
    if registry[code].get("status") == "processing":
        raise UploadError("This policy is still being processed.")
    with _build_lock:
        pdf = config.POLICY_DIR / registry[code]["file"]
        if pdf.exists():
            pdf.unlink()
        for path in (config.PAGES_FILE, config.CHUNKS_FILE):
            if path.exists():
                rows = json.loads(path.read_text(encoding="utf-8"))
                path.write_text(json.dumps([r for r in rows if r["policy"] != code], ensure_ascii=False, indent=2),
                                encoding="utf-8")
        store = fact_store.load_store()
        fact_store.save_store([f for f in store["facts"] if f["policy"] != code], store.get("models", {}))
        del registry[code]
        _save_registry(registry)
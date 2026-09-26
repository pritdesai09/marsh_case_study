"""Gemini wrapper used by every AI step in the app.

- Retries busy/overloaded errors (429, 5xx) with increasing waits.
- Falls back from the strong model to the everyday model if the strong one stays busy.
- generate_json() always returns parsed JSON, repairing malformed output once.
- Raises LLMError with a message that is safe to show in the UI.
"""
import json
import logging
import re
import time

from google import genai
from google.genai import errors, types

from src import config

log = logging.getLogger(__name__)

RETRYABLE_CODES = {429, 500, 502, 503, 504}
RETRY_WAITS = [4, 12, 30]  # seconds between attempts on the same model


class LLMError(Exception):
    """The AI step failed. str(err) is written for end users."""


_client = None
last_model_used = None  # which model answered the most recent call (recorded in outputs)


def _get_client():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise LLMError("No Gemini API key is set. Add GEMINI_API_KEY to .env, or turn on DEMO_MODE.")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _model_chain(strong: bool, fallback: bool = True) -> list[str]:
    """Models to try, in order. Strong requests fall back to the everyday model unless fallback=False."""
    primary = config.GEMINI_MODEL_STRONG if strong else config.GEMINI_MODEL
    chain = [m for m in ([primary, config.GEMINI_MODEL] if fallback else [primary]) if m]
    if not chain:
        raise LLMError("No Gemini model is set. Add GEMINI_MODEL to .env.")
    return list(dict.fromkeys(chain))  # remove duplicates, keep order


def _build_contents(prompt: str, images: list[bytes] | None):
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in (images or [])]
    return parts + [prompt]


def _call_once(model: str, contents, cfg) -> str:
    """One model, with retries on temporary errors. Returns the response text."""
    last_error = None
    for wait in [0] + RETRY_WAITS:
        if wait:
            log.warning("Gemini busy on %s, retrying in %ss", model, wait)
            time.sleep(wait)
        try:
            response = _get_client().models.generate_content(model=model, contents=contents, config=cfg)
        except errors.APIError as e:
            if e.code in RETRYABLE_CODES:
                last_error = e
                continue
            if e.code == 404:
                raise LLMError(f"Model '{model}' was not found. Check the model names in .env.") from e
            if e.code in (400, 401, 403):
                raise LLMError(f"Gemini rejected the request ({e.code}): {e.message}") from e
            raise LLMError(f"Gemini error ({e.code}): {e.message}") from e

        text = response.text or ""
        finish = ""
        if response.candidates and response.candidates[0].finish_reason:
            finish = str(response.candidates[0].finish_reason)
        if "MAX_TOKENS" in finish:
            raise LLMError("The AI response was cut off because it was too long. Try fewer documents at once.")
        if not text.strip():
            raise LLMError(f"The AI returned an empty response (reason: {finish or 'unknown'}).")
        return text

    raise _Busy(model, last_error)


class _Busy(Exception):
    def __init__(self, model, error):
        super().__init__(f"{model} stayed busy: {error}")
        self.model = model


def generate_text(prompt: str, *, strong: bool = False, images: list[bytes] | None = None,
                  system: str | None = None, temperature: float = 0.2, max_tokens: int = 8192,
                  json_mode: bool = False, fallback: bool = True) -> str:
    global last_model_used
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_tokens,
        response_mime_type="application/json" if json_mode else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = _build_contents(prompt, images)
    for model in _model_chain(strong, fallback):
        try:
            text = _call_once(model, contents, cfg)
            log.info("Gemini call succeeded on %s", model)
            last_model_used = model
            return text
        except _Busy as busy:
            log.warning("%s — trying next model", busy)
    raise LLMError("The AI service is busy right now. Please try again in a minute.")


def parse_json(text: str):
    """Parse JSON from model output, tolerating code fences and stray text around it."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if starts:
        start = min(starts)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end > start:
            return json.loads(cleaned[start:end + 1])
    raise json.JSONDecodeError("No JSON found", cleaned, 0)


def generate_json(prompt: str, *, strong: bool = False, images: list[bytes] | None = None,
                  system: str | None = None, temperature: float = 0.1, max_tokens: int = 8192,
                  fallback: bool = True):
    """Like generate_text, but returns parsed JSON. Repairs malformed JSON once."""
    global last_model_used
    text = generate_text(prompt, strong=strong, images=images, system=system,
                         temperature=temperature, max_tokens=max_tokens, json_mode=True,
                         fallback=fallback)
    answered_by = last_model_used
    try:
        return parse_json(text)
    except json.JSONDecodeError:
        log.warning("Malformed JSON from Gemini, asking it to repair")

    repair_prompt = (
        "The text below was meant to be valid JSON but is malformed. "
        "Return ONLY the corrected JSON, with no explanation.\n\n" + text
    )
    repaired = generate_text(repair_prompt, strong=False, temperature=0.0,
                             max_tokens=max_tokens, json_mode=True)
    last_model_used = answered_by  # the content came from the first model, the repair only fixed syntax
    try:
        return parse_json(repaired)
    except json.JSONDecodeError as e:
        raise LLMError("The AI returned data in an unexpected format. Please try again.") from e
# Marsh AI Pitch Generator with Audit Layer

Internship case study (Marsh India Knowledge Services, Data Science). An advisor enters a company name, chooses policy brochures, and gets a 5-slide PowerPoint pitch in which every claim is traced to a brochure clause, audited, and signed off by the advisor before it can be downloaded.

**Live app:** https://marsh-pitch-prit-f3gdgbd2hpbegtak.uaenorth-01.azurewebsites.net/

## What it does

1. **Company profile** (`generateCompanyProfile`): facts from Wikidata and Wikipedia, marked *Sourced*, plus workforce and health risks, marked *Assumption*.
2. **Policy scoring** (code): each client risk is matched to benefits verified against the four brochures (127 facts, each with a verbatim quote and page).
3. **Pitch** (`generateMarketingPitch`): 5 slides (overview, why Marsh, risks mapped to benefits, comparison, recommendation). The AI may only cite verified facts.
4. **Audit** (`auditPitchContent(pitch_slides, policy_docs)`): per-claim citation, numbers, qualifier and insurer checks in code, plus an AI meaning check. The result is VERIFIED / REVIEW / FAIL per claim, a grounding score, and a PASS / REVIEW / FAIL verdict, as HTML, CSV and JSON reports.
5. **Advisor sign-off:** approve, edit (re-audited instantly) or reject each claim. The server refuses the download until nothing fails and every review item is approved.

**Evaluation** (`scripts/eval_audit.py`): of 20 planted errors, 20 were blocked from export (17 FAIL, 3 REVIEW), with 0 false alarms. Code checks alone caught 15 of 20.

## Deliverables

| Deliverable | File |
|---|---|
| Working application | this repository, and the live app above |
| Sample pitch deck | `deliverables/Marsh_pitch_Infosys.pptx` |
| Audit results | `deliverables/Audit_report_Infosys.html` (and `.csv`) |
| Audit evaluation | `deliverables/Audit_evaluation.md` |
| Write-up | `deliverables/Marsh_Case_Study_Writeup.docx` |

## Run locally

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env      # then put your free Gemini API key in .env
python -m uvicorn main:app --reload
```

Open http://127.0.0.1:8000. Without an API key the app runs in demo mode, with template text and code checks only.

## Scripts

| Command | Purpose |
|---|---|
| `python scripts/build_fact_store.py` | Extract facts from the brochures and verify every quote (`--validate` re-checks without AI) |
| `python scripts/run_audit.py` | Audit the most recent saved pitch and write HTML, CSV and JSON reports |
| `python scripts/eval_audit.py` | Plant 20 known errors and measure what the audit catches (`--no-ai` for code checks only) |

## Project layout

```
main.py                 FastAPI app: API + serves the website
src/
  config.py             settings, paths, policy list
  llm.py                Gemini client: retries, model fallback, JSON repair
  ingest.py             PDF text, page images, clause chunks
  fact_store.py         fact extraction + code verification + human overrides
  profile.py            generateCompanyProfile (Wikidata/Wikipedia + assumptions)
  pitch.py              policy scoring + generateMarketingPitch
  pptx_builder.py       PowerPoint rendering
  retrieval.py          BM25 search over clauses
  audit.py              auditPitchContent + reports
  review.py             approve / edit / reject, export gate
  uploads.py            add a new brochure through the UI
static/                 HTML, CSS, JS frontend
data/policies/          the four brochures
data/processed/         pages, chunks, verified facts, human overrides
scripts/                fact store build, audit, evaluation
```

## Tech

Python 3.11, FastAPI, Pydantic, PyMuPDF, python-pptx, Google Gemini (free tier: `gemini-3.8-flash`, `gemini-3.5-flash-lite`), Wikidata and Wikipedia APIs, and vanilla HTML/CSS/JS, on Azure App Service deployed by GitHub Actions.
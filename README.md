# Marsh Pitch Generator

AI-generated, audit-verified insurance pitch decks for corporate clients.

## Run locally
```
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env    # then add your GEMINI_API_KEY and GEMINI_MODEL
uvicorn main:app --reload
```
Open http://127.0.0.1:8000

Get a free Gemini API key at https://aistudio.google.com
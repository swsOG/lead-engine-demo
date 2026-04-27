# Cursor Setup And Tooling

## Python Version

The existing local virtual environment was created with:

```text
Python 3.14.3
```

This is recorded in `.venv/pyvenv.cfg`.

In this shell, plain `python` was not available on PATH during inspection. Use one of:

```powershell
.\.venv\Scripts\python.exe --version
py -3.14 --version
```

## Dependencies

`requirements.txt` currently contains:

```text
Flask==3.0.3
google-genai==1.48.0
```

The app otherwise relies heavily on Python standard library modules, SQLite, vanilla JavaScript, Tailwind CDN, and Flask templates.

## Create Venv, Install, Run

From the project folder:

```powershell
cd "<project-folder>"
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If `python` is not on PATH:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Required Environment Variables

None for seed mode, cached demo mode, cached live mode, or live fallback behavior.

## Optional Environment Variables

Copy `.env.example` to `.env` for local development only:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`.

Optional variables:

- `SERPAPI_API_KEY`
- `TAVILY_API_KEY`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `HUNTER_API_KEY`
- `INSTANTLY_API_KEY`
- `INSTANTLY_CAMPAIGN_ID`
- `INSTANTLY_LIST_ID`
- `GEMINI_MODEL`
- `OPENAI_MODEL`
- `ANTHROPIC_MODEL`

## Optional API Inventory

- SerpAPI is optional. Required only for real local business sourcing in live mode.
- Tavily is optional. Required only for live web research.
- Gemini is optional. Used as the primary LLM provider when configured.
- OpenAI is optional. Used as an LLM fallback when configured.
- Anthropic is optional. Used as another LLM fallback when configured.
- Hunter is optional. Used only for email verification.
- Instantly is optional. Used only for pushing approved/ready leads into Instantly, not direct sending.

## What Works Without Keys

- `/` loads.
- `seed_demo` lead generation.
- `cached_demo`.
- `Load cached live demo`, if `data/cached_live_results.json` exists.
- `live_if_available` safe fallback to seed behavior.
- `/results/<job_id>` progress/results display.
- `/approvals` queue.
- Approve, reject, do-not-contact, restore, edit draft email.
- Approved-only CSV export.

## What Requires Keys

- Real live local sourcing requires `SERPAPI_API_KEY`.
- Live research requires `TAVILY_API_KEY`.
- Gemini scoring/drafting requires `GEMINI_API_KEY`.
- OpenAI fallback scoring/drafting requires `OPENAI_API_KEY`.
- Anthropic fallback scoring/drafting requires `ANTHROPIC_API_KEY`.
- Email verification requires `HUNTER_API_KEY`.
- Instantly push requires `INSTANTLY_API_KEY` and either `INSTANTLY_CAMPAIGN_ID` or `INSTANTLY_LIST_ID`.

## Known Setup Risks

- Do not commit `.env`; a real `.env` exists locally.
- Do not hardcode keys in docs, tests, or app code.
- Do not make optional APIs required for app startup.
- Do not show red page-load errors for missing Instantly setup.
- The project may be inside a broader parent-folder git context. Avoid staging unrelated files.
- The local `.venv` executable may need to be run outside restrictive sandbox contexts.

## Verify After Moving To Cursor

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Manual verification:

1. Start the app with `.\.venv\Scripts\python.exe app.py`.
2. Open `http://127.0.0.1:5000`.
3. Run `seed_demo` with no keys.
4. Run `cached_demo`.
5. Click `Load cached live demo`.
6. Run `live_if_available` with missing keys and confirm it completes with fallback.
7. Open `/approvals`.
8. Approve, reject, do-not-contact, restore.
9. Edit email copy and refresh to confirm persistence.
10. Export approved CSV and confirm rejected/do-not-contact leads are excluded.
11. Confirm no direct email sending exists.
12. Confirm missing Instantly config does not create red page-load errors.


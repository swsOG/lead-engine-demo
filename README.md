# Local Hospitality Lead Engine Demo

A safe interview demo for a local hospitality lead engine. It runs with Flask, SQLite, vanilla JavaScript, Tailwind CDN, and seed JSON data. Optional live mode can use external APIs when keys are available.

No API keys are needed for seed mode. This app does not use email sending, auth, Docker, or React.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

## Modes

- `seed_demo`: uses `data/seed_leads.json`; works with zero API keys.
- `live_if_available`: tries SerpAPI for local business sourcing, Tavily for research, and Gemini/OpenAI/Anthropic for scoring/email drafting. Any failure falls back safely.
- `cached_demo`: loads the cached seed demo result.
- `Load cached live demo`: loads `data/cached_live_results.json` instantly after a successful live run.

Live mode is capped at 5 leads by default to control speed and cost.

## Optional Live API Keys

Create a local `.env` file from `.env.example` and add keys there. Do not commit `.env`.

```powershell
Copy-Item .env.example .env
```

Variables:

- `SERPAPI_API_KEY`: sources real local businesses using SerpAPI Google Maps results.
- `TAVILY_API_KEY`: researches each business and returns source URLs.
- `GEMINI_API_KEY`: primary LLM scoring and email drafting using Gemini 2.5 Flash.
- `OPENAI_API_KEY`: optional fallback LLM scoring and email drafting.
- `ANTHROPIC_API_KEY`: optional second fallback LLM scoring and email drafting.

If a key is missing or an API call fails, the app logs a warning and falls back instead of crashing.

## What It Does

1. Shows a lead-generation form at `/`.
2. Accepts brand, location, offer, ICP, category, and number of leads.
3. Starts a background job on submit.
4. Redirects to `/results/<job_id>`.
5. Polls `/api/status/<job_id>`.
6. Shows progress.
7. Returns ranked leads from seed, cached, or live mode.
8. Displays personalised demo outreach with a copy email button.
9. Saves generated leads into an approval workflow.
10. Lets users approve, reject, suppress, edit, and export approved leads.

## Approval Workflow

Open `/approvals` after generating leads.

Lead statuses:

- `generated`
- `reviewed`
- `approved`
- `rejected`
- `do_not_contact`
- `exported`

On each lead you can edit the email, approve it, reject it, or mark it do-not-contact. Approved leads can be exported from:

```text
/export/approved
```

The same export is also available at `/export/approved.csv`.

The CSV export marks exported leads as `exported`. The app does not send emails directly. Actual sending should be handled later through a controlled outbound or CRM tool such as Instantly, Smartlead, HubSpot, or a similar approved workflow.

## Demo Data

Lead data is stored in:

- `data/seed_leads.json`
- `data/cached_demo_results.json`
- `data/cached_live_results.json`

Sources are demo-labelled where they are not live source URLs.

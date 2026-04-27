# Agent Instructions

## Project Overview

This repo is a local hospitality lead engine demo for Humble Grape / Vivat Bacchus. It ranks nearby businesses for private dining, wine tastings, corporate events, team socials, client entertainment, Christmas parties, and similar hospitality offers.

The current priority is a real workflow portal, not sending. The app should help review, approve, reject, suppress, edit, and export leads before any outbound sending exists.

## Current Architecture

- `app.py`: single-file Flask app containing routes, background job handling, SQLite access, lead generation modes, workflow APIs, contact discovery, email verification, CSV export, and optional Instantly push logic.
- `lead_engine_demo.sqlite3`: local SQLite database.
- `templates/index.html`: lead generation form.
- `templates/results.html`: job progress and generated lead results.
- `templates/queue.html`: approval/workflow queue.
- `static/app.js`: results page polling, result rendering, and basic status/email edits.
- `static/queue.js`: approval queue, filters, contact workflow, verification, CSV export, and optional Instantly actions.
- `static/styles.css`: small shared styling on top of Tailwind CDN.
- `data/seed_leads.json`: seed leads for zero-key demo mode.
- `data/cached_demo_results.json`: cached seed/demo-style results.
- `data/cached_live_results.json`: cached live result fixture after live runs.
- `tests/`: unittest coverage for live modes, output quality, approval workflow, export, contact workflow, and Instantly safety behavior.

## How To Run The App

Use the project folder as the working directory:

```powershell
cd "<project-folder>"
```

Create and activate a virtual environment if needed:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If `python` is not on PATH, use the local interpreter directly:

```powershell
.\.venv\Scripts\python.exe app.py
```

Open:

```text
http://127.0.0.1:5000
```

## How To Test Seed, Live, And Cached Modes

- Seed mode: choose `seed_demo` on `/`. This must work with no API keys.
- Live fallback mode: choose `live_if_available` with no keys. It must complete by falling back safely to seed results.
- Live mode with keys: choose `live_if_available` only when optional keys are configured. It may use SerpAPI, Tavily, Gemini, OpenAI, or Anthropic, and must still fall back instead of crashing.
- Cached demo mode: choose `cached_demo` on `/`.
- Cached live mode: click `Load cached live demo`; the form maps this to `cached_live`.
- Approval workflow: open `/approvals` after generating leads.

Run tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

## Environment Variables

No environment variables are required for seed or cached demo modes.

Optional live/source/research/LLM variables:

- `SERPAPI_API_KEY`: real local business sourcing through SerpAPI Google Maps.
- `TAVILY_API_KEY`: live web research.
- `GEMINI_API_KEY`: primary structured scoring and email drafting.
- `OPENAI_API_KEY`: fallback scoring and email drafting.
- `ANTHROPIC_API_KEY`: second fallback scoring and email drafting.

Optional workflow variables:

- `HUNTER_API_KEY`: email verification.
- `INSTANTLY_API_KEY`: optional push to Instantly only, not direct sending.
- `INSTANTLY_CAMPAIGN_ID`: optional Instantly campaign target.
- `INSTANTLY_LIST_ID`: optional Instantly list target.

Optional model overrides:

- `GEMINI_MODEL`
- `OPENAI_MODEL`
- `ANTHROPIC_MODEL`

Secrets belong only in local `.env`. Do not commit `.env`, paste real keys into docs, or hardcode API keys.

## Do-Not-Touch Rules

- Do not break live lead generation.
- Do not break `seed_demo`.
- Do not break `cached_demo` or cached live loading.
- Do not add real email sending yet.
- Do not add Gmail or SMTP sending.
- Do not add Instantly or Smartlead sending unless explicitly requested.
- Instantly must stay optional and must not show red errors on page load when unconfigured.
- Do not hardcode API keys.
- Do not commit `.env`.
- Do not casually edit `lead_engine_demo.sqlite3`.
- Do not casually edit seed/cache fixtures unless the task explicitly requires data changes.
- Do not rewrite the app into React or another framework.

## Coding Conventions

- Keep changes small and test after each change.
- Prefer the existing Flask, SQLite, vanilla JavaScript, Tailwind CDN, and unittest patterns.
- Use standard library code where it is already sufficient.
- Preserve fallback-first behavior for missing keys and failed APIs.
- Keep workflow functionality before visual polish.
- Avoid broad refactors unless the user explicitly asks for them.
- If unsure, inspect and explain before editing.

## Safety Rules

- Treat generated emails as drafts only.
- CSV export is allowed; real sending is not.
- Contact discovery and verification are workflow helpers, not sending features.
- Instantly integration, where present, is optional push/setup functionality and must not become automatic sending.
- Do not expose secrets in logs, UI, tests, docs, or commits.
- Be careful with git: this project may live inside a wider parent-folder git context. Do not stage or commit unrelated parent-directory files.

## Definition Of Done

- Requested behavior or documentation is complete.
- Seed mode still works.
- Cached modes still work.
- Live mode still falls back safely when keys are missing or APIs fail.
- Workflow statuses persist correctly for the changed area.
- No direct sending has been added.
- No secrets are committed or printed.
- Relevant tests pass, or any inability to run them is reported clearly.
- Final report lists changed files and exact test steps.

## Manual Acceptance Tests

1. Start the app.
2. Open `/`.
3. Run `seed_demo` with no API keys.
4. Run `cached_demo`.
5. Use `Load cached live demo`.
6. Run `live_if_available` with missing keys and confirm safe fallback.
7. Open `/approvals`.
8. Approve, reject, do-not-contact, and restore leads.
9. Edit email copy and confirm it persists.
10. Export approved CSV and confirm only approved leads are exported.
11. Confirm no real email sending occurs.
12. Confirm Instantly absence does not create red page-load errors.


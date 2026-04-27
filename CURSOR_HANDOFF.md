# Cursor Handoff

## What Has Been Built

This is a Flask + SQLite local lead engine demo for hospitality sales workflow. It can generate ranked local business leads from seed data, cached fixtures, or optional live APIs, then move those leads into an approval workflow.

The app currently supports:

- Lead generation form at `/`.
- Background jobs with progress polling.
- Results page at `/results/<job_id>`.
- Seed, cached, live-if-available, and cached-live modes.
- Approval queue at `/approvals`.
- Workflow states for generated/in-review/approved/rejected/do-not-contact/exported leads.
- Draft email editing.
- Approved-only CSV export.
- Suppression list behavior for do-not-contact.
- Contact discovery from websites.
- Email verification through optional Hunter API.
- Optional Instantly push APIs and UI, but no direct email sending.

## What Works

- `seed_demo` works with no keys.
- `live_if_available` falls back safely when live keys are missing or APIs fail.
- `cached_demo` loads cached demo results.
- `Load cached live demo` maps to `cached_live`.
- Jobs persist generated leads into SQLite.
- `/approvals` loads queue data from `/api/leads`.
- Status actions exist for approve, reject, do-not-contact, restore, and direct status updates.
- CSV export from `/export/approved` and `/export/approved.csv` includes approved leads only, then marks exported leads as `exported`.
- Tests cover output quality, live fallback, cached live mode, approval workflow, contact workflow, export, and optional Instantly behavior.

## What Is Partially Working

- The workflow portal exists but needs hardening before visual polish.
- Contact discovery and Hunter verification exist, but they are helper workflow steps and should not be treated as a complete outbound system.
- Instantly push code exists but must remain optional. It is not a reason to add sending.
- The UI has queue filters, approved pipeline sections, and action buttons, but the next priority is to inspect/fix workflow buttons and persistent statuses.

## What Is Broken Or Risky

- The current known priority is workflow reliability, especially buttons and persistent statuses.
- Instantly must not show scary red setup errors on page load when unconfigured.
- Real sending is intentionally absent.
- Git status may be noisy because the project appears to live inside a broader parent-folder git context. Do not stage unrelated files outside this folder.
- In this shell, plain `python` was not available on PATH. Prefer `.venv\Scripts\python.exe`, `py -3.14`, or the full Python 3.14 path.

## Current Known Bugs

- Workflow buttons and persistent statuses need the next inspection/fix pass.
- Verify that queue filters consistently show Review, Approved, Rejected, Do Not Contact, and Exported after status changes and reloads.
- Verify that edited email copy persists and remains visible in the relevant workflow views.
- Verify that Instantly-unconfigured state stays optional and quiet unless the user explicitly clicks an Instantly action.

## Main Files And Routes

Main files:

- `app.py`
- `templates/index.html`
- `templates/results.html`
- `templates/queue.html`
- `static/app.js`
- `static/queue.js`
- `static/styles.css`
- `data/seed_leads.json`
- `data/cached_demo_results.json`
- `data/cached_live_results.json`
- `tests/test_live_modes.py`
- `tests/test_output_quality.py`
- `tests/test_approval_workflow.py`

Main routes:

- `GET/POST /`: form and job creation.
- `GET /results/<job_id>`: results page.
- `GET /approvals`: workflow queue.
- `GET /queue`: redirects to `/approvals`.
- `GET /api/status/<job_id>`: job polling.
- `GET /api/leads`: lead list by queue/status/job.
- `GET /api/leads/counts`: status counts.
- `POST /api/leads/<lead_id>/approve`
- `POST /api/leads/<lead_id>/reject`
- `POST /api/leads/<lead_id>/do-not-contact`
- `POST /api/leads/<lead_id>/restore`
- `POST /api/leads/<lead_id>/status`
- `POST /api/leads/<lead_id>/email`
- `POST /api/leads/<lead_id>/edit-email`
- `GET /api/outreach/readiness`
- `POST /api/leads/<lead_id>/discover-contact`
- `POST /api/leads/discover-approved`
- `POST /api/leads/<lead_id>/save-contact`
- `POST /api/leads/<lead_id>/verify-email`
- `POST /api/leads/verify-approved`
- `GET /api/instantly/config`
- `POST /api/instantly/push-approved`
- `GET /export/approved`
- `GET /export/approved.csv`
- `GET /api/demo-results`

## Database And Tables

Database: `lead_engine_demo.sqlite3`

Tables:

- `jobs`: background job status, progress, params, results, and errors.
- `leads`: persisted generated leads, workflow status, draft copy, contact fields, verification state, Instantly state, and export timestamp.
- `suppression_list`: do-not-contact records.
- `lead_events`: per-lead event history.
- `audit_log`: status/export/Instantly audit records.
- `exports`: CSV export records.

## External APIs Used

All external APIs are optional:

- SerpAPI: Google Maps/local business sourcing.
- Tavily: lead research.
- Gemini: primary LLM scoring and draft generation.
- OpenAI: fallback LLM scoring and draft generation.
- Anthropic: second fallback LLM scoring and draft generation.
- Hunter: email verification.
- Instantly: optional push of approved/ready leads into a campaign or list.

No Gmail, SMTP, Smartlead, or direct sending is currently implemented.

## Current Next Priority

Build a reliable real workflow portal before any sending. The next work should focus on workflow buttons and persistent statuses, not visual polish and not outbound email.

## Exact First Task Cursor Should Do

Inspect and fix workflow buttons plus persistent lead statuses without changing generation modes or adding sending.

Exact prompt:

```text
Read CURSOR_HANDOFF.md, AGENTS.md, .cursor/rules/project.mdc, and CURRENT_STATUS.md. Do not implement sending. Inspect the current workflow buttons and persistent lead statuses, then make the smallest safe fix so approval/rejection/do-not-contact/restore/edit/export states work reliably across seed, cached, and live-fallback modes. Preserve live lead generation, seed mode, cached mode, and secrets safety. Report files changed and exact test steps.
```

## Risks Cursor Must Avoid

- Do not break live lead generation.
- Do not break seed or cached modes.
- Do not add Gmail, SMTP, Smartlead, or direct sending.
- Do not turn optional Instantly setup into required setup.
- Do not show red errors on page load just because Instantly is unconfigured.
- Do not hardcode or expose API keys.
- Do not commit `.env`.
- Do not stage unrelated parent-folder files.
- Do not rewrite the app into React or another framework.


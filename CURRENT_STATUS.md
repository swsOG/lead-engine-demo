# Current Status

## Current Product State

The project is a local Flask demo for hospitality lead generation and lead-review workflow. It can generate leads, show results, persist them in SQLite, and move them through an approval/export process.

Current priority: real workflow portal, not sending.

## Working Modes

- `seed_demo`: works without API keys using `data/seed_leads.json`.
- `live_if_available`: attempts optional live APIs and falls back safely.
- `cached_demo`: loads cached demo results.
- `cached_live`: loaded by the `Load cached live demo` button when cached live data exists.

## Known Problems

- Workflow buttons and persistent statuses need the next inspection/fix pass.
- Queue filters should be verified for Review, Approved, Rejected, Do Not Contact, and Exported.
- Edited email copy persistence should be verified after refresh and across queue views.
- Instantly must stay optional and should not create red page-load errors when unconfigured.
- Plain `python` may not be available on PATH; prefer `.venv\Scripts\python.exe`.
- Git may show unrelated parent-folder files; avoid staging anything outside this project.

## Next Task

Inspect and fix workflow buttons plus persistent statuses. Preserve seed, cached, live fallback, and live generation behavior. Do not add sending.

## Last Safe Checkpoint

Docs-only alignment package created for Cursor. App behavior should remain unchanged. Treat the current working generation pipeline as the baseline to preserve.

## Exact Manual Test Checklist

1. Start the app:

   ```powershell
   .\.venv\Scripts\python.exe app.py
   ```

2. Open:

   ```text
   http://127.0.0.1:5000
   ```

3. Run `seed_demo` with no API keys.
4. Run `cached_demo`.
5. Click `Load cached live demo`.
6. Run `live_if_available` with missing keys and confirm safe fallback.
7. Open `/approvals`.
8. Approve a lead and confirm it appears under Approved.
9. Reject a lead and confirm it appears under Rejected.
10. Mark a lead Do Not Contact and confirm it appears under Do Not Contact and is excluded from export.
11. Restore a rejected or suppressed lead and confirm it returns to review.
12. Edit email copy and refresh to confirm persistence.
13. Export approved CSV and confirm only approved leads export.
14. Confirm exported leads move to Exported.
15. Confirm no direct email sending occurs.
16. Confirm missing Instantly config does not show red errors on page load.


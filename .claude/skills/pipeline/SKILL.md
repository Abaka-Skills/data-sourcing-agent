---
name: pipeline
description: Show a read-only status dashboard of the sourcing pipeline — vendor counts by status/fit per project, drafts awaiting review, and follow-ups that are due. Use when the user asks "where are we", "what's due", "pipeline status", or "what needs follow-up".
---

# Pipeline dashboard (read-only)

Summarize the whole tracker. Nothing is modified. Commands run from the project root.

**Tracker:** one tracker (Google Sheet) at a time. Default is `medical`; for another, prefix every command with `ABAKA_TRACKER=<tracker>` (see `python3 tools/config.py trackers`). If the user wants "everything", run the dashboard once per tracker.

## 1. Gather
- Projects: `python3 tools/sheets.py get-projects`
- Vendors: `python3 tools/sheets.py get-vendors` (all), or `--project <P-ID>` to scope
- Follow-ups due: `python3 tools/followups.py suggestions` (add `--project <P-ID>` to scope)

## 2. Present
For each project (and an "all" total), show:
- **By status** — counts of Identified / Outreached / In Contact / Collaborating / Rejected.
- **By fit** — strong / partial / potential.
- **Awaiting review** — rows still `Identified` with a non-empty `Draft/email link` (drafted, ready for `/send-outreach`).
- **Due for follow-up** — from the suggestions call (vendor · days since contact · follow-up count). These are *suggestions only* — the user triggers sending with **/follow-up**.
- **Missing contact email** — identified/drafted rows with a blank `Contact email` that need a manual add.

Lead with a one-line headline (e.g. "P-06: 12 vendors — 3 collaborating, 4 outreached, 2 due for follow-up"). Then the per-project breakdown as a compact table. Point the user to the sheet URL (`python3 tools/config.py show` → `config.sheet_url`) for the live view.

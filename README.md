# Abaka Medical-Data Vendor Sourcing Workflow

An agentic workflow (Claude Code skills + a small local Python toolkit) that runs
the vendor-sourcing SOP end to end, with a single **Google Sheet as the source of
truth**. Everything talks to Google directly over the official APIs — no
third-party SaaS sits in the data path for HIPAA-adjacent vendor correspondence.

## What it does (SOP → skills)

| SOP step | Skill | What happens |
|---|---|---|
| — (once) | `/setup` | Install deps, connect Google (OAuth), capture your identity, create the tracker sheet. |
| 1. Scope | `/add-project` | Turn a free-text data requirement into a structured **Projects** row. |
| 2. Source | `/source-vendors <P-ID>` | Re-check existing vendors first, then web-search new ones; score fit (strong/partial/potential); write **Vendor Tracker** rows. |
| 3. Draft | `/draft-outreach <P-ID>` | Per-vendor personalized email (requirement + gating questions + 30-min call ask) → **Gmail drafts only**. |
| 4a. Send | `/send-outreach <P-ID>` | Manual-review gate → send approved drafts; record the Gmail thread. |
| 4b. Follow up | `/follow-up <P-ID\|vendor>` | **You trigger it** after checking your inbox; sends a templated nudge in-thread. Skips anyone who replied; warns at 3. |
| 5. Post-meeting | `/log-meeting` | Paste call notes → append + update status/pricing. |
| — | `/pipeline` | Read-only dashboard: counts by status/fit, drafts awaiting review, follow-ups due. |

Follow-ups are **manual-trigger, not scheduled** — you decide who to nudge; the
agent then sends automatically. There is no background job.

## One-time setup

Run **/setup** and it walks you through everything. In short:

1. `python3 -m pip install -r requirements.txt`
2. In Google Cloud Console: create a project → enable **Google Sheets API** + **Gmail API** → OAuth consent screen **Internal** (for the `abaka.ai` Workspace) → create an **OAuth Desktop client** → download the JSON to `~/.abaka/credentials.json`.
3. `python3 tools/config.py set-profile name="..." role="..." company="Abaka AI" email="you@abaka.ai"`
4. `python3 tools/auth.py --login` (one browser consent; token cached at `~/.abaka/token.json`)
5. `python3 scripts/setup_sheet.py --create "Abaka Medical Data Sourcing"`
6. `python3 tools/sheets.py --selftest` and `python3 tools/gmail.py --selftest`

## The Google Sheet

- **Projects** — one row per sourcing project (scope fields + compliance requirements).
- **Vendor Tracker** — **one row per vendor × project** (a vendor serving two projects = two rows sharing a `Vendor ID`), so status/fit/follow-up state are per-project. Key enums: `Fit` (strong/partial/potential) and `Status` (identified → drafted → approved → outreached → in contact → collaborating/rejected/no response). Dropdowns are non-strict, so you can always hand-edit.
- **Config** (hidden) — mirrors identity/settings for other machines.

The sheet is safe to edit by hand at any time: the toolkit merge-updates only the
cells it touches and stamps `Last updated` — it never blanks columns you edited.

## Daily follow-up flag (medical)

A `launchd` job (`~/Library/LaunchAgents/com.abaka.medical-followup-flag.plist`, template in [scripts/](scripts/com.abaka.medical-followup-flag.plist)) runs `python3 tools/followups.py flag` daily at 8am. It marks every `outreached` medical vendor that is **due** (no contact ≥ `followup_days`, `< followup_max` sends) with **"Needs Follow-up"** in a **red** cell in the `Follow-up Action` column, and clears the flag once a vendor is contacted or replies. It **only labels — never sends**. You then review the red rows and run `/follow-up` to send. Run it any time with `python3 tools/followups.py flag`; logs go to `~/.abaka/followup-flag.log`.

Follow-ups no longer store a Thread ID in the sheet — `/follow-up` resolves the vendor's Gmail thread from their Contact email at send time.

## Multiple domains (trackers)

The workflow is domain-agnostic plumbing + per-domain **profiles** ([tools/domains.py](tools/domains.py)). Each domain gets its own Google Sheet ("tracker"); skills pick one via the `ABAKA_TRACKER` env var (default `medical`).

- **medical** — EHR / imaging / WSI / outcomes. **One row per vendor** (keyed on Vendor ID); a vendor serving several projects lists them in `Project ID` (e.g. `P-04, P-06`). A daily job flags due vendors (see below).
- **robotics_egocentric** — human egocentric video for robot learning (commercial+AI-license, bystander-consent, license-class, origin gates; modality/sensor/volume columns; Supply type = dataset-license vs bespoke-collection).

```bash
python3 tools/config.py trackers                    # list trackers
python3 scripts/setup_sheet.py --create --tracker robotics "Abaka Robotics Egocentric Sourcing"
ABAKA_TRACKER=robotics python3 tools/sheets.py get-projects   # operate on a specific tracker
```
Add a new domain by adding a profile to `tools/domains.py` and creating its tracker — no other code changes.

## Files

```
tools/       config.py auth.py sheets.py gmail.py sourcing.py followups.py
scripts/     setup_sheet.py
.claude/skills/  setup add-project source-vendors draft-outreach send-outreach follow-up log-meeting pipeline
~/.abaka/    credentials.json token.json profile.json config.json   (secrets, outside the repo)
```

Each `tools/*.py` is also a standalone CLI (`python3 tools/<name>.py --help`).

## Security & data governance

- OAuth scopes are minimal: `spreadsheets`, `gmail.compose`, `gmail.modify`.
- Credentials and token live only in `~/.abaka/` (outside this repo). Do not commit them.
- **No email is ever sent without a human action**: initial emails require explicit approval in `/send-outreach`; follow-ups only send when you invoke `/follow-up`.

## Optional: fewer permission prompts

Read-only commands prompt every run by default. To auto-allow the safe ones (reads,
draft creation, config) while still prompting for anything that **sends email**,
add this to `.claude/settings.json` yourself (or run `/fewer-permission-prompts`):

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 -m pip install:*)",
      "Bash(python3 tools/config.py:*)",
      "Bash(python3 tools/auth.py:*)",
      "Bash(python3 tools/sheets.py:*)",
      "Bash(python3 tools/sourcing.py:*)",
      "Bash(python3 scripts/setup_sheet.py:*)",
      "Bash(python3 tools/gmail.py thread:*)",
      "Bash(python3 tools/gmail.py list-drafts:*)",
      "Bash(python3 tools/gmail.py search:*)",
      "Bash(python3 tools/followups.py suggestions:*)"
    ]
  }
}
```
The `gmail.py send*` and `followups.py send` commands are intentionally left out so
outbound sends always ask first.

## Troubleshooting

- **`credentials.json not found`** → finish step 2 above (download the OAuth client).
- **`no sheet configured`** → run `scripts/setup_sheet.py --create "..."` (or `--ensure <id>`).
- **Token expired / re-consent** → `python3 tools/auth.py --login`. If you used an External (non-Workspace) consent screen, the token expires every 7 days — switch to Internal to avoid it.
- **Reply not detected** → follow-up reply detection reads the Gmail thread; make sure the row's `Thread ID` was captured at send time.

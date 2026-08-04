# Onboarding — Abaka Vendor Sourcing Workflow

Welcome! This is an agentic workflow (Claude Code skills + a small Python toolkit) for sourcing
data vendors into shared, live Google Sheet trackers. It runs the full SOP: **scope a data
requirement → source & score vendors → draft outreach → send (after your review) → follow up →
log meetings**, all recorded in one Google Sheet per domain (currently **medical** and **robotics**).

This gets you running in ~15 minutes. Talk to the agent in plain language, or use the `/`-skills.

---

## Prerequisites
- macOS with **Python 3.9+** (`python3 --version`).
- **Claude Code / Cowork** (you're reading this in it).
- A **Google Workspace account on abaka.ai**.

## 0. Open this folder as your project
Save the `Data Sourcing` folder anywhere on your Mac (e.g. `~/Documents/` — the exact location doesn't matter), then open **that folder** as your working directory:
- **Cowork / desktop app:** start a session and pick the `Data Sourcing` folder as the project directory.
- **CLI:** `cd "/path/to/Data Sourcing"`, then run `claude`.

The `/`-skills below only appear when this folder is the active project (they live in `.claude/skills/`), and every command is run from the folder root.

## 1. Install dependencies
```bash
python3 -m pip install -r requirements.txt
```
*(If pip says "externally-managed-environment", run `python3 -m pip install --user -r requirements.txt` instead.)*

## 2. Connect YOUR Google account  (one-time, ~10 min)
First create the local config folder:
```bash
mkdir -p ~/.abaka
```
Then, in the **Google Cloud Console** (console.cloud.google.com):
1. Create/select a project → **APIs & Services → Library** → enable **Google Sheets API** + **Gmail API**.
2. **OAuth consent screen** → **User type: Internal** (abaka.ai).
3. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app** → **Download JSON** → save it as **`~/.abaka/credentials.json`**.
   *(Or ask your workspace admin for the org's `credentials.json` — it's an Internal app, and you still log in as yourself.)*

Then log in and set your identity (run these from inside the `Data Sourcing` folder):
```bash
python3 tools/auth.py --login    # opens a browser once — approve as YOU
python3 tools/config.py set-profile name="Your Name" role="Your Role" company="Abaka AI" email="you@abaka.ai"
```
Everything lands in `~/.abaka/` **on your machine only** — never shared. Outreach emails send from **your** Gmail.

## 3. Connect to the SHARED trackers  (don't create new ones)
Ask the tracker owner to **Share** both Google Sheets with your abaka.ai email as **Editor**. Then register them in your local config — this only *points* your toolkit at the existing sheets; it does **not** modify them:
```bash
python3 tools/config.py set-tracker name=medical sheet_id=<MEDICAL_SHEET_ID> domain=medical
python3 tools/config.py set-tracker name=robotics sheet_id=<ROBOTICS_SHEET_ID> domain=robotics_egocentric
```
Verify your connection:
```bash
python3 tools/config.py trackers            # both trackers listed
python3 tools/sheets.py --selftest          # reads/writes a scratch cell in the shared sheet
python3 tools/gmail.py --selftest           # lists a Gmail label + makes/deletes a test draft
```

> `/setup` and `scripts/setup_sheet.py --create` are only for starting a **brand-new** tracker/domain from scratch. As a teammate joining existing trackers, you don't need them.

## 4. Use it
Say what you want, or invoke a skill:
| Skill | Does |
|---|---|
| `/add-project` | Scope a new data requirement into the tracker |
| `/source-vendors P-06` | Find, score (strong/partial/potential), and record fitting vendors |
| `/draft-outreach P-06` | Personalized Gmail **drafts** (never sends) |
| `/send-outreach P-06` | Manual-review gate → send from your Gmail |
| `/follow-up P-06` | Nudge non-responders (after you check your inbox) |
| `/log-meeting` | Record a vendor call + update status |
| `/pipeline` | Status dashboard |

**Tracker selection:** default is `medical`; for the robotics tracker, prefix commands with `ABAKA_TRACKER=robotics` (the agent does this automatically when you mention robotics).

## Good to know
- **Nothing sends without you**: initial emails require your approval; follow-ups only send when you trigger `/follow-up`.
- **One shared sheet, many people**: everyone edits the same live tracker; rows are keyed by Vendor ID so concurrent edits/sorts are safe.
- **Daily follow-up flag**: a `launchd` job marks vendors "Needs Follow-up" (red) daily. Only **one** teammate needs it running (if someone on the team already does) — it updates the shared sheet for everyone, so you can skip it. To run it yourself instead, see the *Daily follow-up flag* section in `README.md`.
- Full technical details are in **`README.md`**.

---
name: setup
description: One-time setup for the medical-data sourcing workflow — install dependencies, connect Google (Sheets + Gmail) via OAuth, capture the user's outreach identity, and create the tracker spreadsheet. Idempotent. Use the first time, or when the user says "set up the sourcing workflow" or hits an auth/credentials error.
---

# Setup (one-time, idempotent)

Get the workflow ready end-to-end. Run steps in order; each is safe to re-run. Commands run from the project root. Where the user must click in a browser, walk them through it and wait for confirmation before continuing.

## 1. Install Python dependencies
```bash
python3 -m pip install -r requirements.txt
```

## 2. Create a Google Cloud OAuth client (user does the clicks)
Tell the user to do this once (it lets the toolkit read/write their Sheet and send Gmail as themselves; nothing is routed through a third party):

1. Open **https://console.cloud.google.com** → create or pick a project.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Gmail API**.
3. **APIs & Services → OAuth consent screen** → **User type: Internal** (this is an `abaka.ai` Workspace account, so Internal avoids app-verification and the 7-day token expiry that hits external test apps). Fill app name + support email; save.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → Application type **Desktop app** → Create → **Download JSON**.
5. Save that file as **`~/.abaka/credentials.json`** (create the `~/.abaka` folder if needed).

Confirm the file exists before moving on:
```bash
ls -l ~/.abaka/credentials.json
```
(If the account is *not* a Workspace domain, they can use External + add themselves as a Test user, but the cached token then expires every 7 days and they'll re-run step 4's login periodically. Flag this trade-off.)

## 3. Capture the outreach identity
Ask for name, role, company, the Gmail address to send from, an optional signature block, and an optional scheduling link. Then:
```bash
python3 tools/config.py set-profile name="Your Name" role="..." company="Abaka AI" email="you@abaka.ai" signature="..." scheduling_link="..."
```

## 4. Authenticate (one browser consent)
```bash
python3 tools/auth.py --login
```
This opens a browser once; approve the Sheets + Gmail scopes. The token is cached at `~/.abaka/token.json` and refreshed automatically after. Then verify:
```bash
python3 tools/auth.py --check
```

## 5. Create the tracker spreadsheet
```bash
python3 scripts/setup_sheet.py --create "Abaka Medical Data Sourcing"
```
This creates the **Projects**, **Vendor Tracker**, and hidden **Config** tabs with headers + dropdowns, registers the tracker in `~/.abaka/config.json`, and prints the URL. (If the user already has a sheet, use `--ensure <sheet_id>` instead.)

**Additional domains (optional):** each domain is its own tracker/sheet. To add robotics egocentric-video sourcing later:
```bash
python3 scripts/setup_sheet.py --create --tracker robotics "Abaka Robotics Egocentric Sourcing"
```
Skills then target a tracker via `ABAKA_TRACKER=<name>` (default `medical`); see `python3 tools/config.py trackers`.

## 6. Verify end-to-end
```bash
python3 tools/sheets.py --selftest    # writes+reads a scratch cell
python3 tools/gmail.py --selftest     # lists labels, creates+deletes a test draft
```

## 7. Done
Report the sheet URL and the workflow: **/add-project** → **/source-vendors** → **/draft-outreach** → **/send-outreach** → **/follow-up** → **/log-meeting**, with **/pipeline** for status. Re-running /setup is safe if anything changes.

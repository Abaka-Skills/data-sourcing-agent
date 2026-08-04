---
name: log-meeting
description: Record notes from a vendor meeting/call and update the tracker (SOP step 5). The user pastes meeting notes for a vendor+project; this appends them and updates the status (In Contact / Collaborating / Rejected). Use when the user says "log my meeting with Truveta for P-06" or pastes call notes.
---

# Log a vendor meeting (SOP 5: Update after calls)

Record what happened on a vendor call and move the engagement forward. Commands run from the project root.

**Tracker:** default is `medical`. For a project in another tracker (e.g. `robotics`), prefix every `sheets.py` command with `ABAKA_TRACKER=<tracker>` (see `python3 tools/config.py trackers`).

## 1. Identify the vendor row
From the user's message, get the vendor and project. Find the row:
`ABAKA_TRACKER=<T> python3 tools/sheets.py get-vendors --project <P-ID>` → match the vendor name → note its primary key (**Vendor ID** for medical, **Engagement ID** for robotics) and current `Meeting notes`, `Pricing`, `Status`.
If the vendor/project is ambiguous, ask.

## 2. Append the notes (don't overwrite)
Read the current `Meeting notes`, then append the new note with today's date so history is preserved:
```bash
cat <<JSON | ABAKA_TRACKER=<T> python3 tools/sheets.py update-vendor <row PK id>
{"Meeting notes": "<existing notes>\n[<today YYYY-MM-DD>] <new notes>"}
JSON
```

## 3. Update status + captured facts
Ask the user (or infer from the notes, then confirm) the resulting **Status** — one of: `In Contact` · `Collaborating` · `Rejected` (use Rejected whether we passed or the vendor declined).
Also capture anything the notes revealed — e.g. `Pricing`, resolved compliance flags (`Commercial rights-clean?` Yes/No), or a `Contact name`/`Contact email` update:
```bash
echo '{"Status":"Collaborating","Pricing":"$X/study, MSA required","Commercial rights-clean?":"Yes"}' | ABAKA_TRACKER=<T> python3 tools/sheets.py update-vendor <row PK id: Vendor ID (medical) / Engagement ID (robotics)>
```

## 4. Confirm
Echo back the updated status, the appended note, and any captured pricing/compliance. If a next step is due (e.g. "send MSA", "intro call in 2 weeks"), note it in `Meeting notes` and tell the user.

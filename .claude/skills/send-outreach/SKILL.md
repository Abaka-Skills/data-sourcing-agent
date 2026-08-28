---
name: send-outreach
description: Send approved outreach drafts via Gmail (SOP step 4a) after an explicit manual-review gate. Only sends vendor rows the user approves, records the Gmail thread, and moves them to "outreached". Use after /draft-outreach, when the user says "send the outreach for P-06".
---

# Send outreach (SOP 4a: Send, with manual review gate)

Send the drafts the user approves. **This is the human-in-the-loop send gate — never send without explicit confirmation in this run.** Commands run from the project root. Take `<P-ID>` from the user.

## Backend: Lark project? Use larksource
For a **Lark project** (`P01`…`P09`), read/write through **`tools/larksource.py`** (`gmail.py` unchanged). Mapping:
- ready to send: `python3 tools/larksource.py outreach-list <P> --status Identified` — rows with a `✉draft` mark (non-empty Draft/email link) are drafted & ready. Use the `Draft/email link` value as the `<draft_id>`.
- after `gmail.py send-draft <draft_id>`, record the send — **including `rfc_message_id` from its output**, which is what reply-matching keys on: `echo '{"Vendor":"<exact vendor name>","Message-ID":"<rfc_message_id>","Status":"Outreached","Last contact date":"today","Follow-up count":0}' | python3 tools/larksource.py update-vendor <P>` ("today" is auto-stamped). This moves the row into the project's **Tracking** view; replies later flip it to In Contact automatically (tracking agent).
The mandatory review gate (step 2) is unchanged — still confirm the exact send list with the user first.

**Tracker:** default is `medical`. If this project lives in another tracker (e.g. `robotics`), prefix every `sheets.py`/`sourcing.py` command below with `ABAKA_TRACKER=<tracker>` (see `python3 tools/config.py trackers`). `gmail.py` is account-wide and needs no prefix.

## 1. List what's ready
`ABAKA_TRACKER=<T> python3 tools/sheets.py get-vendors --project <P-ID> --status Identified` — of these, the ones with a non-empty `Draft/email link` are drafted and ready to send.

## 2. Review gate (mandatory)
Present the drafted vendors as a table: Vendor · Contact email · Subject. Ask the user to confirm exactly which to send — **all**, a subset, or none. If a user prefers reviewing in Gmail, they can apply an "Approved" label there; but you still confirm the final send list with them here before sending. Do not proceed on silence.

## 3. Send each approved draft
For each confirmed vendor:
```bash
python3 tools/gmail.py send-draft <draft_id>     # returns {message_id, thread_id}
```
Then record the send (keyed on the row's primary key — **Vendor ID** for medical, **Engagement ID** for robotics):
```bash
cat <<JSON | ABAKA_TRACKER=<T> python3 tools/sheets.py update-vendor <row PK id>
{"Status":"Outreached","Last contact date":"$(date +%F)","Follow-up count":"0"}
JSON
```
No Thread ID is stored — follow-ups later resolve the Gmail thread from the vendor's Contact email. The daily flag job fills the **Follow-up Action** cell once a vendor becomes due; you don't set it here.

## 4. Finish
Set project status: `echo '{"Project status":"outreach"}' | python3 tools/sheets.py update-project <P-ID>`.
Report: who was emailed (with thread ids), who was skipped, and remind the user that **follow-ups are manual** — when they've checked their inbox they can run **/follow-up `<P-ID>`** (see /pipeline for who's due).

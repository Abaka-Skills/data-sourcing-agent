---
name: follow-up
description: Send follow-up emails to vendors who haven't replied (SOP step 4b). You trigger this after checking your inbox; it sends a templated nudge in the original Gmail thread automatically, with a guardrail that skips anyone who already replied. Use when the user says "follow up with P-06" or "follow up with Segmed".
---

# Follow up (SOP 4b: manual-trigger follow-ups)

Follow-ups are **human-triggered** — the user has checked their inbox and wants to nudge non-responders. Your invocation is the approval, so send automatically (no separate send confirmation), but respect the reply guardrail and the max-3 warning. Commands run from the project root.

**Tracker:** default is `medical`. For a project in another tracker (e.g. `robotics`), prefix `followups.py`/`sheets.py` commands with `ABAKA_TRACKER=<tracker>` (see `python3 tools/config.py trackers`).

## 1. Determine targets
- If the user named a vendor or project, target those.
- Otherwise show who's due: `ABAKA_TRACKER=<T> python3 tools/followups.py suggestions [--project <P-ID>]`. This lists `outreached` vendors with no reply for ≥ `followup_days` and `Follow-up count < followup_max` — the same ones the daily job marks **"Needs Follow-up"** (red) in the sheet's **Follow-up Action** column. Present them and confirm which to nudge.

## 2. Send each follow-up
The `<id>` is the row's primary key — **Vendor ID** (medical) or **Engagement ID** (robotics):
```bash
ABAKA_TRACKER=<T> python3 tools/followups.py send <id>
```
This resolves the vendor's Gmail thread from their **Contact email** (no Thread ID needed), sends a templated in-thread nudge, increments `Follow-up count`, updates `Last contact date`, and clears the red **Follow-up Action** flag.
This reads the Gmail thread, then:
- **Reply guardrail** — if the vendor already replied, it does **not** send; it marks the row `in contact` and returns `action: skipped`. Tell the user "X already replied — I did not nudge them; marked in-contact." Only re-send with `--force` if the user explicitly insists.
- **Otherwise** it sends a templated nudge **in the same thread**, increments `Follow-up count`, and updates `Last contact date` + next suggested date.
- If the response has `at_max: true`, warn the user this vendor has now hit the SOP maximum of 3 follow-ups and won't appear in future suggestions.

To personalize instead of using the template, pipe a body:
```bash
cat <<'BODY' | ABAKA_TRACKER=<T> python3 tools/followups.py send <id>
<your follow-up text>
BODY
```

## 3. Report
Summarize: who was nudged (with new follow-up count), who was skipped because they'd replied, and who's at the 3-follow-up max. Remind the user replies flip a vendor to `in contact` automatically on the next follow-up check, and that they can log a meeting with **/log-meeting** once a vendor engages.

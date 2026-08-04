---
name: draft-outreach
description: Draft personalized vendor outreach emails as Gmail drafts (SOP step 3), for any domain/tracker. For each identified vendor it writes an email stating our specific requirement, domain-appropriate gating questions, and a 30-min scoping-call ask, signed from the user's identity. Creates DRAFTS only — never sends. Use before /send-outreach.
---

# Draft outreach emails (SOP 3: Drafting outreach emails)

Create one **Gmail draft** per identified vendor. This step **never sends** — it only drafts. Commands run from the project root; prefix with `ABAKA_TRACKER=<T>` for the project's tracker (default `medical`, `robotics` for egocentric). Take `<P-ID>` from the user.

## 1. Preflight (identity required)
`python3 tools/config.py show` — confirm `profile` has name/role/company/email. If not, tell the user to run **/setup** (or `python3 tools/config.py set-profile name="..." role="..." company="..." email="..."`) and stop. The email is signed from this identity.

## 2. Load scope + vendors + domain gating questions
- `ABAKA_TRACKER=<T> python3 tools/sheets.py get-project <P-ID>` (the requirement to recite).
- `ABAKA_TRACKER=<T> python3 tools/sheets.py get-vendors --project <P-ID> --status Identified`.
- `ABAKA_TRACKER=<T> python3 tools/domains.py gating-questions` — the domain's gating questions (medical: de-id method, commercial rights, cohort size, follow-up, linkage, pricing · robotics: commercial+AI license, bystander consent/de-id, format/schema, provenance, origin, volume, sensor specs, annotation). Tailor them to each vendor, especially for any `Unknown` compliance/license field on the row.

## 3. Ensure a contact email per vendor
If a vendor's `Contact email` is blank, try to find one (WebFetch the vendor's contact/about page). If still none, **skip that vendor**, leave it `identified`, and report it so the user can add a contact by hand in the sheet. Don't invent an address.

## 4. Compose each email (personalized)
Tailor to what THAT vendor offers vs. our scope. Structure:
- **Subject** — specific, e.g. `Data partnership inquiry — breast cancer WSI + outcomes (Abaka)`.
- **Opening** — greet the contact; one line on who we are (`<name>, <role> at <company>`).
- **The ask (specific to scope)** — recite this project's concrete requirement: data type/modality, disease/cohort, cohort size, follow-up/longitudinality, linkage, region, and the compliance bar (de-identified; rights-clean for commercial AI / GDPR as applicable).
- **Gating questions (3–6)** — turn every `Unknown` compliance flag and key scope requirement into a crisp question, e.g.: Do you hold rights permitting **commercial AI model training** on derivatives? De-identification method (Safe Harbor / Expert Determination / GDPR)? Cohort size meeting **≥N**? Follow-up duration and outcomes captured? Linkage between imaging and outcomes? Indicative pricing / licensing model?
- **Call ask** — request a **30-minute scoping call**; include the scheduling link from the profile if present.
- **Signature** — from the profile (name, role, company, email, signature line).

Keep it concise and professional (roughly 150–220 words).

## 5. Create the draft + update the row
```bash
cat <<'BODY' | python3 tools/gmail.py create-draft --to "contact@vendor.com" --subject "<subject>"
<full email body>
BODY
```
Capture the returned `draft_id`, then record it on the row (Status stays **Identified** — the `Draft/email link` marks it ready to review; there is no separate "drafted" status):
```bash
echo '{"Draft/email link":"<draft_id>"}' | ABAKA_TRACKER=<T> python3 tools/sheets.py update-vendor <row PK id: Vendor ID (medical) / Engagement ID (robotics)>
```

## 6. Hand off for review
Show the user each drafted email (recipient + subject + body). Tell them to review/edit the drafts in **Gmail → Drafts** (edits there are preserved), then run **/send-outreach `<P-ID>`** to send after approval. Report any vendors skipped for a missing contact email.

# Lark Integration — Agent Reference

Agent-facing spec for putting the sourcing agent on Lark (Feishu international, `larksuite.com`).
This is the reference the coding session reads when building `tools/lark.py` and wiring the skills.
Not a human handout.

## Role in the stack
Lark **replaces or runs alongside** the Google stack:
- **Lark Base (Bitable)** = source of truth → replaces/augments the Google Sheet tracker.
- **Lark Mail** = vendor outreach send + reply read → replaces/augments Gmail.
- **Lark Docs** = read the PM's data-requirement doc; write the weekly report.
- **Lark bot (Messenger)** = post the daily status summary to a group.
- **Intake form** = a **Base form view** (submissions land as records — NO separate Forms API).

Keep the `~/.abaka/` secret pattern and the module shape of `gmail.py`/`sheets.py`.

## Auth model
Store `~/.abaka/lark.json` = `{"app_id": "...", "app_secret": "..."}` (chmod 600, never commit).

- **tenant_access_token** (app/bot, ~2h TTL): `POST /open-apis/auth/v3/tenant_access_token/internal` with app_id+secret. Cache + auto-refresh. Use for: **Base, Docs create, IM/bot, Contacts**.
- **user_access_token** (OAuth, acts as a person): needed for **Mail send/read** (send from a real mailbox, read that person's replies). OAuth: authorize URL → code → `POST /open-apis/authen/v1/oidc/access_token`; persist refresh token in `~/.abaka/lark_token.json`. Mirror `auth.py`.

## Capability → API endpoint → scope → token
| Capability | Endpoint (base `/open-apis`) | Scope | Token |
|---|---|---|---|
| Base read records | `GET /bitable/v1/apps/{app_token}/tables/{table_id}/records` | `bitable:app` | tenant |
| Base create/update record | `POST/PUT .../records[/{id}]` | `bitable:app` | tenant |
| Base list tables/fields | `GET /bitable/v1/apps/{app_token}/tables` | `bitable:app` | tenant |
| Read requirement doc | `GET /docx/v1/documents/{id}/raw_content` (or `/blocks`) | `docx:document:readonly` | tenant/user |
| Wiki-hosted doc | `GET /wiki/v2/spaces/...` → get obj token | `wiki:wiki:readonly` | tenant |
| Create weekly report | `POST /docx/v1/documents` + block ops | `docx:document` | tenant |
| Send vendor mail | Lark **Mail** user-mailbox send-message API | mail send (confirm ID) | **user** |
| Read replies | Mail user-mailbox list/get messages | mail read (confirm ID) | **user** |
| Post daily summary | `POST /im/v1/messages?receive_id_type=chat_id` | `im:message` | tenant |
| Resolve group chat | `GET /im/v1/chats` | `im:chat:readonly` | tenant |
| Resolve user/email | `GET /contact/v3/users/...` | `contact:user.base:readonly`, `contact:user.email:readonly` | tenant |

Exact **Mail** and **Docs** scope IDs vary by console/edition — match to what the tenant shows. Mail API may need the admin to switch it on (most likely blocker).

## `tools/lark.py` plan (same shape as gmail.py/sheets.py)
- token: `tenant_token()`, `user_token()` (+ cache in ~/.abaka)
- base (tracker backend): `get_records(table)`, `upsert_record(table, row)`, `update_record(table, id, fields)`
- mail: `send_mail(to, subject, body)`, `list_replies(since)`, `thread_for(email)`
- docs: `read_doc(id)`, `create_report(title, blocks)`
- im: `send_to_chat(chat_id, text)`
- `--selftest`, CLI per subcommand

## Config additions
`config.py`: add a Lark tracker type → stores Base **app_token** + **table_id** (the analog of a Google `sheet_id`). `domains.py` unchanged (domain profiles are backend-agnostic). Skills read/write through a small source abstraction so `medical/robotics` trackers can be Google **or** Lark.

## Mapping from current toolkit
- `sheets.py` (Google Sheet) → Lark Base table ("Vendor Tracker", "Projects").
- `gmail.py` (Gmail draft/send/thread) → Lark Mail.
- `followups.py` reply detection → poll Lark Mail, match sender email → set Status `In Contact`.
- Skills (`source/draft/send/follow-up/log/pipeline`) unchanged in logic; only the backend swaps.

## Auto-sync replies → Base status
1. **Poll first (robust):** cron reads the user mailbox, matches sender → vendor record → Status `In Contact`, clear follow-up flag. Reuse `followups.py` logic.
2. **Event-driven (later):** configure Event Subscription callback (or long-connection/WebSocket) + subscribe the mail-received event → update Base per reply. Only if Mail events are available on the plan.

## Daily summary + weekly report
- **Daily:** cron → read Base → status counts + due follow-ups → bot posts to the group `chat_id` via `im:message`.
- **Weekly:** aggregate the week's status changes → create a Lark Doc (`docx:document`) or post to chat.

## Open items to confirm before building
- Mail API enabled for the tenant? (admin toggle)
- Exact Mail/Docs scope IDs in this console.
- Base `app_token` + `table_id` for the tracker; group `chat_id` for summaries.
- Google + Lark in parallel, or full cutover? (decides how much of the source abstraction to build.)

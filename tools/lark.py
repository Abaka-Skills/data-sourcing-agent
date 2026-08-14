"""Lark (Feishu intl) backend for the sourcing agent.

Same shape as sheets.py / gmail.py: a thin adapter + CLI. The Lark Base is the
source of truth once we cut over from Google. Config + secrets live in
~/.abaka/lark.json (app_id, app_secret, base_url, and the `base` block with the
app_token + table ids), outside the repo.

Auth:
  * tenant_access_token (app/bot) — Base, IM/bot, Docs, Contacts. Cached at
    ~/.abaka/lark_tenant.json with expiry, auto-refreshed.
  * user_access_token (OAuth, acts as a person) — required for MAIL send/read.
    Not wired yet; mail_* functions raise a clear NeedsUserAuth until the
    one-time browser OAuth is done (mirrors auth.py).

CLI:
  python3 tools/lark.py --selftest
  python3 tools/lark.py records <TableName> [--limit N]
  python3 tools/lark.py projects
  python3 tools/lark.py send-im <chat_id> "<text>"
  python3 tools/lark.py summary [--chat <chat_id>]     # daily status summary
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ABAKA = pathlib.Path.home() / ".abaka"
CFG_PATH = ABAKA / "lark.json"
TOK_PATH = ABAKA / "lark_tenant.json"


def cfg() -> dict:
    if not CFG_PATH.exists():
        sys.exit("no ~/.abaka/lark.json — store {app_id, app_secret, base_url, base}")
    return json.loads(CFG_PATH.read_text())


def _c():
    c = cfg()
    return c, c["base_url"], c["base"]


class NeedsUserAuth(RuntimeError):
    pass


# ------------------------------------------------------------------ auth
def tenant_token() -> str:
    """Cached tenant_access_token; refresh ~5 min before expiry."""
    if TOK_PATH.exists():
        t = json.loads(TOK_PATH.read_text())
        if t.get("expire_at", 0) - 300 > time.time():
            return t["token"]
    c, base, _ = _c()
    body = json.dumps({"app_id": c["app_id"], "app_secret": c["app_secret"]}).encode()
    req = urllib.request.Request(base + "/open-apis/auth/v3/tenant_access_token/internal",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    r = json.loads(urllib.request.urlopen(req, timeout=25).read())
    if r.get("code") != 0:
        sys.exit(f"tenant token failed: {r}")
    tok = r["tenant_access_token"]
    TOK_PATH.write_text(json.dumps({"token": tok, "expire_at": time.time() + r.get("expire", 7200)}))
    os.chmod(TOK_PATH, 0o600)
    return tok


def api(method: str, path: str, body: dict | None = None, *, token: str | None = None,
        retries: int = 3) -> dict:
    _, base, _ = _c()
    url = path if path.startswith("http") else base + path
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + (token or tenant_token()))
        req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=40) as x:
                return json.loads(x.read().decode())
        except urllib.error.HTTPError as e:
            payload = e.read().decode()[:400]
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt); continue
            raise SystemExit(f"Lark {method} {path} -> HTTP {e.code}: {payload}")
    raise SystemExit(f"Lark {method} {path}: retries exhausted")


# ------------------------------------------------------------------ Base
def _tid(table: str) -> str:
    _, _, b = _c()
    tid = b["tables"].get(table)
    if not tid:
        sys.exit(f"unknown table {table!r}; known: {list(b['tables'])}")
    return tid


def _app() -> str:
    return _c()[2]["app_token"]


def project_tables() -> dict:
    """{project_name: table_id} for the per-project vendor tables (P01…P09)."""
    return dict(_c()[2].get("project_tables", {}))


def get_records(table: str, *, filter_str: str | None = None, page_size: int = 500) -> list[dict]:
    """All records of a table as [{record_id, fields}]. Handles pagination."""
    out, pt = [], None
    while True:
        q = {"page_size": min(page_size, 500)}
        if pt:
            q["page_token"] = pt
        if filter_str:
            q["filter"] = filter_str
        r = api("GET", f"/open-apis/bitable/v1/apps/{_app()}/tables/{_tid(table)}/records?"
                + urllib.parse.urlencode(q))
        d = r.get("data", {})
        out += d.get("items", [])
        pt = d.get("page_token")
        if not d.get("has_more"):
            break
        time.sleep(0.2)
    return out


def create_records(table: str, rows: list[dict]) -> list[dict]:
    """Batch-create. rows = [{field: value, ...}]. Chunks of 500."""
    made = []
    for i in range(0, len(rows), 500):
        chunk = [{"fields": r} for r in rows[i:i + 500]]
        r = api("POST", f"/open-apis/bitable/v1/apps/{_app()}/tables/{_tid(table)}/records/batch_create",
                {"records": chunk})
        made += r.get("data", {}).get("records", [])
        time.sleep(0.2)
    return made


def update_record(table: str, record_id: str, fields: dict) -> dict:
    return api("PUT", f"/open-apis/bitable/v1/apps/{_app()}/tables/{_tid(table)}/records/{record_id}",
               {"fields": fields})


def find_record(table: str, field: str, value: str) -> dict | None:
    for rec in get_records(table):
        v = rec.get("fields", {}).get(field)
        vs = v if isinstance(v, str) else (
            ",".join(e.get("text", e.get("name", "")) if isinstance(e, dict) else str(e) for e in v)
            if isinstance(v, list) else "")
        if vs.strip().lower() == value.strip().lower():
            return rec
    return None


def link(record_ids: list[str]) -> list[str]:
    """Value format for a Base link field (Project / Vendor / Engagement)."""
    return [r for r in record_ids if r]


# ------------------------------------------------------------------ IM (bot)
def send_im(chat_id: str, text: str) -> dict:
    """Post a text message to a chat as the bot. Bot must be in the chat."""
    return api("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id",
               {"receive_id": chat_id, "msg_type": "text",
                "content": json.dumps({"text": text})})


def list_chats() -> list[dict]:
    return api("GET", "/open-apis/im/v1/chats?page_size=50").get("data", {}).get("items", [])


# ------------------------------------------------------------------ Docs
def read_doc(document_id: str) -> str:
    r = api("GET", f"/open-apis/docx/v1/documents/{document_id}/raw_content")
    return r.get("data", {}).get("content", "")


# ------------------------------------------------------------------ Mail
# Reading works with the TENANT token (no user OAuth) as long as a folder_id is
# passed; the system inbox folder_id is literally "INBOX". Confirmed with scope
# mail:user_mailbox.message:readonly.
#   CAVEAT: the get-message response currently returns metadata only (message_id,
#   thread_id, internal_date, label_ids, smtp_message_id, references) — NOT
#   from/subject/body. Extracting sender/subject for reply-matching needs a
#   fuller mail read scope (TODO: confirm which). Until then, reply detection can
#   fall back to Gmail (gmail.py) which returns full headers.
DEFAULT_MAILBOX = "yulingl@abaka.ai"


def mail_inbox(mailbox: str = DEFAULT_MAILBOX, folder_id: str = "INBOX",
               page_size: int = 50) -> list[dict]:
    """List message metadata from a mailbox folder (tenant token)."""
    ids = api("GET", f"/open-apis/mail/v1/user_mailboxes/{mailbox}/messages"
              f"?folder_id={folder_id}&page_size={page_size}").get("data", {}).get("items", [])
    out = []
    for mid in ids:
        d = api("GET", f"/open-apis/mail/v1/user_mailboxes/{mailbox}/messages/{mid}")
        out.append(d.get("data", {}).get("message", {}))
    return out


def mail_send(to, subject: str, body: str, *, mailbox: str = DEFAULT_MAILBOX,
              html: bool = False, cc=None) -> dict:
    """Send an email as the user via Lark Mail. Needs the user OAuth token
    (lark_auth.user_token()) — raises NeedsUserAuth until that's set up.
    `to`/`cc` accept a string or list of addresses. Body field name may need a
    tweak per console edition — adjust after the first live send."""
    import lark_auth
    token = lark_auth.user_token()
    addrs = lambda v: [{"mail_address": a.strip()} for a in ([v] if isinstance(v, str) else (v or [])) if a]
    payload = {"subject": subject, "to": addrs(to),
               ("body_html" if html else "body_plain_text"): body}
    if cc:
        payload["cc"] = addrs(cc)
    r = api("POST", f"/open-apis/mail/v1/user_mailboxes/{mailbox}/messages/send",
            payload, token=token)
    if r.get("code") not in (0, None):
        raise SystemExit(f"Lark mail send failed: {r.get('code')} {r.get('msg')} — payload keys {list(payload)}")
    return r


# ------------------------------------------------------------------ derived: daily summary
def _cell(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ",".join(e.get("text", e.get("name", "")) if isinstance(e, dict) else str(e) for e in v)
    return "" if v is None else str(v)


def _snippet(f: dict, width: int = 90) -> str:
    """First meaningful line of a vendor's textual status (activity > source > notes)."""
    for k in ("Latest update", "Source Status", "Notes"):
        v = _cell(f.get(k)).strip()
        if not v or v.startswith("Setting:"):
            continue
        flat = " ".join(v.split())            # collapse newlines/runs so headers like "WhatsApp:" don't stand alone
        if flat:
            return flat[:width] + ("…" if len(flat) > width else "")
    return ""


def _project_clients() -> dict:
    """{project_name: client} via the Projects master, joined on the Open ↗ table_id."""
    pt, out = project_tables(), {}
    for r in get_records("Projects"):
        link = r["fields"].get("Open ↗")
        url = link.get("link", "") if isinstance(link, dict) else str(link or "")
        client = _cell(r["fields"].get("Client Name"))
        for name, tid in pt.items():
            if tid and tid in url:
                out[name] = client
    return out


def _followup_due(vendors: list[dict], days: int = 5, max_fu: int = 3) -> int:
    n = 0
    for v in vendors:
        f = v.get("fields", {})
        if _cell(f.get("Status")) != "Outreached":
            continue
        fu = int(_cell(f.get("Follow-up count")) or 0)
        last = f.get("Last contact date")
        d = (time.time() * 1000 - last) / 86_400_000 if isinstance(last, (int, float)) else None
        if fu < max_fu and (d is None or d >= days):
            n += 1
    return n


def status_summary() -> str:
    """Daily digest with textual status — counts + per-project vendor highlights."""
    from collections import Counter
    pt = project_tables()
    clients = _project_clients()
    _rank = {"S": 0, "A": 1, "B": 2}
    body, empty, total, active_projs = [], [], 0, 0
    for proj in sorted(pt):
        vendors = get_records(proj)
        total += len(vendors)
        if not vendors:
            empty.append(proj.split("-")[0])
            continue
        active_projs += 1
        st = Counter(_cell(v["fields"].get("Status")) or "—" for v in vendors)
        inact = sum(1 for v in vendors if _cell(v["fields"].get("Active/Inactive")) == "Inactive")
        client = clients.get(proj, "")
        breakdown = ", ".join(f"{k} {n}" for k, n in st.most_common())
        head = f"▸ {proj}{f' ({client})' if client else ''} — {len(vendors)} vendors · {breakdown}"
        if inact:
            head += f" · {inact} inactive"
        body.append(head)
        # up to 3 textual highlights, S-priority first
        picked = sorted(vendors, key=lambda v: _rank.get(_cell(v["fields"].get("Priority")), 9))
        shown = 0
        for v in picked:
            sn = _snippet(v["fields"])
            if sn:
                pr = _cell(v["fields"].get("Priority"))
                body.append(f"    • {_cell(v['fields'].get('Vendor'))}{f' [{pr}]' if pr else ''}: {sn}")
                shown += 1
            if shown >= 3:
                break
        due = _followup_due(vendors)
        if due:
            body.append(f"    ⏰ {due} due for follow-up")
    head = (f"📊 Sourcing status — {time.strftime('%Y-%m-%d')}\n"
            f"{total} vendors · {active_projs} active projects"
            f"{f' · pending: ' + ', '.join(empty) if empty else ''}")
    return head + "\n\n" + "\n".join(body)


# ------------------------------------------------------------------ CLI
def _main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd = argv[0]
    if cmd == "--selftest":
        print("ok: tenant token", tenant_token()[:12] + "…")
        print(f"ok: Projects = {len(get_records('Projects'))} records")
        for t in project_tables():
            print(f"ok: {t} = {len(get_records(t))} vendors")
        return 0
    if cmd == "records":
        table = argv[1]
        lim = 10
        if "--limit" in argv:
            lim = int(argv[argv.index("--limit") + 1])
        for rec in get_records(table)[:lim]:
            print(rec.get("record_id"), {k: _cell(v) for k, v in list(rec.get("fields", {}).items())[:5]})
        return 0
    if cmd == "projects":
        for rec in get_records("Projects"):
            f = rec.get("fields", {})
            print(rec["record_id"], _cell(f.get("Project")), "|", _cell(f.get("Status")), "|", _cell(f.get("Data Type")))
        return 0
    if cmd == "send-im":
        print(send_im(argv[1], argv[2]))
        return 0
    if cmd == "summary":
        text = status_summary()
        print(text)
        if "--chat" in argv:
            print(send_im(argv[argv.index("--chat") + 1], text))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

"""Gmail adapter — drafts, sending, and threaded replies.

Manual-review gate: outreach is created as a DRAFT (create_draft); nothing is
sent until send_draft is called explicitly. Follow-ups reuse the original
threadId + In-Reply-To/References headers so they land in the same Gmail thread,
and reply detection reads the thread deterministically (no LLM).

CLI:
  python3 tools/gmail.py --selftest
  echo "body" | python3 tools/gmail.py create-draft --to a@b.com --subject "Hi"
  python3 tools/gmail.py send-draft <draft_id>
  echo "body" | python3 tools/gmail.py send-in-thread --thread-id <tid> --to a@b.com --subject "Re: Hi"
  python3 tools/gmail.py thread <thread_id>
  python3 tools/gmail.py list-drafts
  python3 tools/gmail.py search --q "from:vendor@x.com"
"""
from __future__ import annotations

import base64
import json
import sys
from email.mime.text import MIMEText

import auth
import config


def _service(*, interactive: bool = True):
    return auth.gmail_service(interactive=interactive)


def _from_header() -> str | None:
    prof = config.load_profile()
    name, email = prof.get("name"), prof.get("email")
    if name and email:
        return f"{name} <{email}>"
    return email or None


# Always-CC list — every outbound draft/send copies these. Override per-call with
# cc=..., or set "cc_always" in ~/.abaka/profile.json.
DEFAULT_CC = ["tonixu@abaka.ai", "wzh@abaka.ai", "tomtang@abaka.ai"]

_CC_SENTINEL = object()


def _cc_list(cc) -> list[str]:
    if cc is _CC_SENTINEL:                       # not specified -> use the default
        try:
            import json as _json, pathlib as _pl
            p = _pl.Path.home() / ".abaka" / "profile.json"
            if p.exists():
                v = _json.loads(p.read_text()).get("cc_always")
                if v:
                    return v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]
        except Exception:
            pass
        return list(DEFAULT_CC)
    if not cc:
        return []
    return cc if isinstance(cc, list) else [x.strip() for x in str(cc).split(",") if x.strip()]


def _build_raw(to: str, subject: str, body: str, *, cc=_CC_SENTINEL,
               in_reply_to: str | None = None, references: str | None = None) -> str:
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    cc_list = _cc_list(cc)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    frm = _from_header()
    if frm:
        msg["From"] = frm
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _ids(sent: dict) -> dict:
    return {"message_id": sent.get("id"), "thread_id": sent.get("threadId")}


def create_draft(to: str, subject: str, body: str, thread_id: str | None = None,
                 cc=_CC_SENTINEL) -> dict:
    svc = _service()
    message = {"raw": _build_raw(to, subject, body, cc=cc)}
    if thread_id:
        message["threadId"] = thread_id
    draft = svc.users().drafts().create(userId="me", body={"message": message}).execute()
    return {"draft_id": draft["id"], **_ids(draft.get("message", {}))}


def delete_draft(draft_id: str) -> dict:
    _service().users().drafts().delete(userId="me", id=draft_id).execute()
    return {"deleted": draft_id}


def send_draft(draft_id: str) -> dict:
    svc = _service()
    sent = svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return {"draft_id": draft_id, **_ids(sent)}


def send_new(to: str, subject: str, body: str) -> dict:
    svc = _service()
    sent = svc.users().messages().send(
        userId="me", body={"raw": _build_raw(to, subject, body)}).execute()
    return _ids(sent)


def _thread_meta(thread_id: str) -> dict:
    return _service().users().threads().get(
        userId="me", id=thread_id, format="metadata",
        metadataHeaders=["From", "Date", "Subject", "Message-ID"]).execute()


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def list_thread_messages(thread_id: str) -> list[dict]:
    """Messages in a thread, oldest first, flagged is_from_me for reply detection."""
    thread = _thread_meta(thread_id)
    out = []
    for m in thread.get("messages", []):
        labels = m.get("labelIds", [])
        out.append({
            "id": m.get("id"),
            "from": _header(m, "From"),
            "subject": _header(m, "Subject"),
            "date": _header(m, "Date"),
            "message_id_header": _header(m, "Message-ID"),
            "snippet": m.get("snippet", ""),
            "internal_date_ms": int(m.get("internalDate", 0)),
            "is_from_me": "SENT" in labels,
        })
    out.sort(key=lambda x: x["internal_date_ms"])
    return out


def send_in_thread(thread_id: str, to: str, subject: str, body: str) -> dict:
    """Send a reply into an existing thread (used for follow-ups)."""
    msgs = list_thread_messages(thread_id)
    last = msgs[-1] if msgs else {}
    rfc_id = last.get("message_id_header") or None
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    raw = _build_raw(to, subject, body, in_reply_to=rfc_id, references=rfc_id)
    message = {"raw": raw, "threadId": thread_id}
    sent = _service().users().messages().send(userId="me", body=message).execute()
    return _ids(sent)


def has_reply_after(thread_id: str, after_ms: int | None = None) -> dict:
    """True if the thread contains an inbound (not-from-me) message, optionally
    after a given epoch-ms timestamp. Used by follow-up reply detection."""
    inbound = [m for m in list_thread_messages(thread_id) if not m["is_from_me"]]
    if after_ms is not None:
        inbound = [m for m in inbound if m["internal_date_ms"] > after_ms]
    latest = inbound[-1] if inbound else None
    return {"has_reply": bool(inbound),
            "latest": latest and {"from": latest["from"], "date": latest["date"],
                                  "snippet": latest["snippet"]}}


def latest_thread_with(email: str) -> str | None:
    """Thread id of the most recent thread involving an address (newest first).
    Lets follow-ups thread correctly without storing a Thread ID in the sheet."""
    if not email:
        return None
    hits = search(f"to:{email} OR from:{email}", max_results=5)
    return hits[0]["thread_id"] if hits else None


def list_drafts() -> list[dict]:
    svc = _service()
    resp = svc.users().drafts().list(userId="me", maxResults=100).execute()
    out = []
    for d in resp.get("drafts", []):
        full = svc.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
        msg = full.get("message", {})
        out.append({"draft_id": d["id"], "to": _header(msg, "To"),
                    "subject": _header(msg, "Subject"), "thread_id": msg.get("threadId")})
    return out


def search(query: str, max_results: int = 20) -> list[dict]:
    svc = _service()
    resp = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    out = []
    for m in resp.get("messages", []):
        full = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]).execute()
        out.append({"id": m["id"], "thread_id": full.get("threadId"),
                    "from": _header(full, "From"), "subject": _header(full, "Subject"),
                    "date": _header(full, "Date"), "snippet": full.get("snippet", "")})
    return out


# ----------------------------------- CLI ---------------------------------- #
def _opts(rest: list[str]) -> dict:
    out: dict = {}
    i = 0
    while i < len(rest):
        if rest[i].startswith("--"):
            key = rest[i][2:].replace("-", "_")
            out[key] = rest[i + 1] if i + 1 < len(rest) else ""
            i += 2
        else:
            i += 1
    return out


def _selftest() -> int:
    svc = _service()
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    me = svc.users().getProfile(userId="me").execute().get("emailAddress")
    print(f"ok: {len(labels)} labels; account {me}")
    d = create_draft(me, "Abaka selftest (safe to delete)", "selftest body")
    svc.users().drafts().delete(userId="me", id=d["draft_id"]).execute()
    print(f"ok: created + deleted test draft {d['draft_id']}")
    return 0


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "--selftest":
        return _selftest()
    o = _opts(rest)
    if cmd == "create-draft":
        print(json.dumps(create_draft(o["to"], o["subject"], sys.stdin.read(),
                                       o.get("thread_id"))))
    elif cmd == "send-draft":
        print(json.dumps(send_draft(rest[0])))
    elif cmd == "send":
        print(json.dumps(send_new(o["to"], o["subject"], sys.stdin.read())))
    elif cmd == "send-in-thread":
        print(json.dumps(send_in_thread(o["thread_id"], o["to"], o["subject"], sys.stdin.read())))
    elif cmd == "thread":
        print(json.dumps(list_thread_messages(rest[0]), indent=2))
    elif cmd == "has-reply":
        after = int(o["after_ms"]) if o.get("after_ms") else None
        print(json.dumps(has_reply_after(rest[0], after), indent=2))
    elif cmd == "list-drafts":
        print(json.dumps(list_drafts(), indent=2))
    elif cmd == "search":
        print(json.dumps(search(o["q"], int(o.get("max", 20))), indent=2))
    else:
        raise SystemExit(f"error: unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

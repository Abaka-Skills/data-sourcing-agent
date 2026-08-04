"""Follow-up helpers.

Two jobs:
  * flag  — a daily, deterministic sweep that marks every OUTREACHED vendor that
            is due (no contact for >= followup_days, < followup_max sends) with
            "Needs Follow-up" in a red cell in the domain's follow-up-action column.
            Runs headless (launchd) or on demand. NEVER sends email.
  * send  — human-triggered: send a templated nudge into the vendor's existing
            Gmail thread (resolved from their Contact email, so no Thread ID is
            stored in the sheet), with a reply guardrail. Your invocation is the
            approval.

CLI:
  python3 tools/followups.py flag                 # active tracker (respects ABAKA_TRACKER)
  python3 tools/followups.py flag --all-trackers  # every tracker (used by the daily job)
  python3 tools/followups.py suggestions [--project P-04]
  python3 tools/followups.py send <id> [--force]  # id = Vendor ID or Engagement ID; body on stdin optional
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import auth
import config
import domains
import gmail
import sheets

_RED = {"red": 0.86, "green": 0.20, "blue": 0.20}
_WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
_BLACK = {"red": 0.0, "green": 0.0, "blue": 0.0}


def _days_since(iso_date: str) -> int | None:
    try:
        d = datetime.date.fromisoformat(str(iso_date).strip())
    except (ValueError, TypeError):
        return None
    return (datetime.date.today() - d).days


def _int(v, default: int = 0) -> int:
    try:
        return int(str(v).strip() or default)
    except (ValueError, TypeError):
        return default


def _is_due(row: dict, days: int, max_fu: int) -> bool:
    if str(row.get("Status", "")).strip() != "Outreached":
        return False
    since = _days_since(row.get("Last contact date"))
    return since is not None and since >= days and _int(row.get("Follow-up count")) < max_fu


# ------------------------------ daily flag sweep --------------------------- #
def flag() -> dict:
    """Mark due outreached vendors 'Needs Follow-up' (red) in the action column."""
    import gspread

    prof = domains.get_profile(config.get_domain())
    col = prof.get("followup_action_col")
    if not col:
        print("flag: this domain has no follow-up-action column; nothing to do")
        return {"flagged": 0}
    ws = sheets.worksheet(sheets.VENDORS)
    headers = ws.row_values(1)
    if col not in headers:
        print(f"flag: column {col!r} not found in the sheet")
        return {"flagged": 0}
    col_idx = headers.index(col)
    cfg = config.load_config()
    days, max_fu = _int(cfg.get("followup_days"), 3), _int(cfg.get("followup_max"), 3)

    value_cells, fmt = [], []
    flagged = 0
    # Manage the whole column: mark due rows, clear everything else (so a vendor
    # that leaves 'outreached' or gets contacted loses a stale flag).
    for pos, row in enumerate(ws.get_all_records()):
        r = pos + 2  # 1-based sheet row (after header)
        due = _is_due(row, days, max_fu)
        value_cells.append(gspread.Cell(r, col_idx + 1, "Needs Follow-up" if due else ""))
        fmt.append(_action_format_request(ws.id, r - 1, col_idx, due))
        flagged += int(due)
    if value_cells:
        ws.update_cells(value_cells, value_input_option="USER_ENTERED")
    if fmt:
        auth.sheets_service().spreadsheets().batchUpdate(
            spreadsheetId=config.get_sheet_id(), body={"requests": fmt}).execute()
    print(f"flag: {flagged} vendor(s) marked 'Needs Follow-up'")
    return {"flagged": flagged}


def flag_all() -> dict:
    """Run flag() for every configured tracker that has a follow-up-action column."""
    results = {}
    for name in config.load_config().get("trackers", {}):
        os.environ["ABAKA_TRACKER"] = name
        sheets._spreadsheet = sheets._spreadsheet_key = None  # switch active sheet
        if not domains.get_profile(config.get_domain(name)).get("followup_action_col"):
            continue
        print(f"[{name}]", end=" ")
        results[name] = flag()
    return results


def _action_format_request(gid: int, row0: int, col0: int, due: bool) -> dict:
    return {"repeatCell": {
        "range": {"sheetId": gid, "startRowIndex": row0, "endRowIndex": row0 + 1,
                  "startColumnIndex": col0, "endColumnIndex": col0 + 1},
        "cell": {"userEnteredFormat": {
            "backgroundColor": _RED if due else _WHITE,
            "textFormat": {"bold": due, "foregroundColor": _WHITE if due else _BLACK}}},
        "fields": "userEnteredFormat(backgroundColor,textFormat)"}}


# ------------------------------- suggestions ------------------------------- #
def suggestions(project_id: str | None = None) -> list[dict]:
    cfg = config.load_config()
    days, max_fu = _int(cfg.get("followup_days"), 3), _int(cfg.get("followup_max"), 3)
    out = []
    for row in sheets.get_vendors(status="Outreached"):
        if project_id and project_id not in str(row.get("Project ID", "")):
            continue
        if not _is_due(row, days, max_fu):
            continue
        out.append({"Vendor ID": row.get("Vendor ID"), "Engagement ID": row.get("Engagement ID"),
                    "Vendor name": row.get("Vendor name"), "Project ID": row.get("Project ID"),
                    "Contact email": row.get("Contact email"),
                    "Follow-up count": _int(row.get("Follow-up count")),
                    "days_since_contact": _days_since(row.get("Last contact date"))})
    return out


# --------------------------------- sending -------------------------------- #
def _row_by_id(rid: str) -> dict:
    for row in sheets.get_records(sheets.VENDORS):
        if rid in (str(row.get("Engagement ID", "")).strip(), str(row.get("Vendor ID", "")).strip()):
            return row
    raise SystemExit(f"error: no vendor row with id {rid!r}")


def _template(row: dict) -> str:
    prof = config.load_profile()
    project = sheets.get_project(str(row.get("Project ID", "")).split(",")[0].strip()) or {}
    topic = (project.get("Disease/cohort") or project.get("Task/activity diversity")
             or project.get("Data type(s)") or project.get("Data type") or "the dataset we discussed")
    sig = prof.get("signature") or "\n".join(
        x for x in [prof.get("name"), prof.get("role"), prof.get("company"), prof.get("email")] if x)
    contact = (row.get("Contact name") or "").split(" ")[0]
    hello = f"Hi {contact}," if contact else "Hi,"
    return (f"{hello}\n\nJust following up on my note below about {topic}. I know inboxes get busy — "
            f"if there's a potential fit on your side, I'd welcome a quick 30-minute call to scope it.\n\n"
            f"Happy to share more detail on our requirements. Thanks for your time!\n\n{sig}")


def send_followup(rid: str, body: str | None = None, force: bool = False) -> dict:
    row = _row_by_id(rid)
    to = str(row.get("Contact email", "")).strip()
    if not to:
        raise SystemExit(f"error: {rid} has no Contact email")
    thread_id = gmail.latest_thread_with(to)
    if not thread_id:
        raise SystemExit(f"error: no prior Gmail thread with {to} — send initial outreach first")

    key_col = sheets._pk(sheets.VENDORS)
    key_val = row.get(key_col)
    msgs = gmail.list_thread_messages(thread_id)
    inbound = [m for m in msgs if not m["is_from_me"]]
    if inbound and not force:
        latest = inbound[-1]
        sheets.update_fields(sheets.VENDORS, key_val, {
            "Status": "In Contact",
            "Meeting notes": (str(row.get("Meeting notes", "")) +
                              f"\n[auto] reply detected {latest['date']}: {latest['snippet']}").strip()})
        return {"id": rid, "action": "skipped",
                "reason": "reply detected — vendor already responded; marked 'In Contact'",
                "latest_reply": {"from": latest["from"], "date": latest["date"], "snippet": latest["snippet"]}}

    subject = (msgs[-1]["subject"] if msgs else "") or "Following up"
    body = body or _template(row)
    sent = gmail.send_in_thread(thread_id, to, subject, body)

    cfg = config.load_config()
    max_fu = _int(cfg.get("followup_max"), 3)
    new_count = _int(row.get("Follow-up count")) + 1
    updates = {"Follow-up count": str(new_count),
               "Last contact date": datetime.date.today().isoformat()}
    prof = domains.get_profile(config.get_domain())
    if prof.get("followup_action_col"):
        updates[prof["followup_action_col"]] = ""  # just contacted → not due
    sheets.update_fields(sheets.VENDORS, key_val, updates)
    try:
        flag()  # recompute the red flags so the just-nudged row clears
    except Exception:
        pass
    return {"id": rid, "action": "sent", "follow_up_count": new_count,
            "at_max": new_count >= max_fu, "thread_id": sent.get("thread_id")}


# ----------------------------------- CLI ---------------------------------- #
def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "flag":
        print(json.dumps(flag_all() if "--all-trackers" in rest else flag(), indent=2))
    elif cmd == "suggestions":
        project = rest[rest.index("--project") + 1] if "--project" in rest else None
        print(json.dumps(suggestions(project), indent=2))
    elif cmd == "send":
        rid = rest[0]
        force = "--force" in rest
        stdin_body = "" if sys.stdin.isatty() else sys.stdin.read().strip()
        print(json.dumps(send_followup(rid, stdin_body or None, force), indent=2))
    else:
        raise SystemExit(f"error: unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

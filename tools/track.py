"""Tracking agent — sync vendor replies + inactivity into the Lark Base.

Model: each project is its own table (P01…P09, see lark.project_tables()); a
vendor is a row in its project's table. Runs 2x/day (cron). Two passes:

  1. poll_replies(): read Gmail for inbound replies, match sender email → a
     vendor row in whichever project table holds it, advance Status
     (Identified/Outreached → In Contact), stamp Last contact date, and write
     the reply into the "Latest update" activity field. A reply also re-marks
     the vendor Active.
  2. mark_inactive(): any vendor still at "Outreached" whose last contact is
     older than N days (default 7) flips Active/Inactive → Inactive.

Replies are read from Gmail (full headers) — Lark Mail read returns metadata
only, so Gmail stays the reply source until the fuller mail scope lands.

CLI:
  python3 tools/track.py run [--days 2] [--inactive-days 7] [--dry]
  python3 tools/track.py replies [--days 2] [--dry]
  python3 tools/track.py inactive [--inactive-days 7] [--dry]
"""
from __future__ import annotations

import re
import sys
import time
from email.utils import parsedate_to_datetime

sys.path.insert(0, "tools")
import gmail  # noqa: E402
import lark  # noqa: E402

ADVANCEABLE = {"", "Identified", "Outreached", "In Contact"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _email(header_from: str) -> str:
    m = EMAIL_RE.search(header_from or "")
    return m.group(0).lower() if m else ""


def _date_ms(date_header: str) -> int:
    try:
        return int(parsedate_to_datetime(date_header).timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def _update(tid: str, rid: str, fields: dict) -> None:
    lark.api("PUT", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records/{rid}",
             {"fields": fields})


def _vendor_index() -> dict[str, dict]:
    """Map lowercased Contact email -> {proj, tid, rid, fields} across all
    project tables. Later duplicates lose (first project wins)."""
    idx = {}
    for proj, tid in lark.project_tables().items():
        for rec in lark.get_records(proj):
            f = rec.get("fields", {})
            for e in EMAIL_RE.findall(lark._cell(f.get("Contact email")).lower()):
                idx.setdefault(e, {"proj": proj, "tid": tid, "rid": rec["record_id"], "fields": f})
    return idx


def poll_replies(days: int = 2, dry: bool = False) -> list[dict]:
    idx = _vendor_index()
    if not idx:
        return []
    hits = gmail.search(f"in:inbox newer_than:{days}d", max_results=100)
    seen, out = set(), []
    for msg in hits:
        em = _email(msg.get("from", ""))
        v = idx.get(em)
        if not v or em in seen:
            continue
        seen.add(em)
        cur = lark._cell(v["fields"].get("Status"))
        if cur not in ADVANCEABLE:          # respect human-set terminal states
            continue
        subj = (msg.get("subject") or "").strip()
        snip = (msg.get("snippet") or "").strip()
        fields = {
            "Status": "In Contact",
            "Last contact date": _date_ms(msg.get("date", "")),
            "Latest update": f"↩ Reply {msg.get('date','')}: {subj} — {snip}"[:280],
            "Active/Inactive": "Active",
        }
        out.append({"proj": v["proj"], "vendor": lark._cell(v["fields"].get("Vendor")),
                    "email": em, "from": cur or "—", "subject": subj})
        if not dry:
            _update(v["tid"], v["rid"], fields)
    return out


def mark_inactive(inactive_days: int = 7, dry: bool = False) -> list[dict]:
    now = time.time() * 1000
    cutoff = inactive_days * 86400 * 1000
    out = []
    for proj, tid in lark.project_tables().items():
        for rec in lark.get_records(proj):
            f = rec.get("fields", {})
            if lark._cell(f.get("Status")) != "Outreached":
                continue
            if lark._cell(f.get("Active/Inactive")) == "Inactive":
                continue
            last = f.get("Last contact date")
            stale = (not last) or (isinstance(last, (int, float)) and now - last > cutoff)
            if not stale:
                continue
            out.append({"proj": proj, "vendor": lark._cell(f.get("Vendor"))})
            if not dry:
                _update(tid, rec["record_id"], {"Active/Inactive": "Inactive"})
    return out


def run(days: int = 2, inactive_days: int = 7, dry: bool = False) -> dict:
    return {"replies": poll_replies(days, dry), "inactive": mark_inactive(inactive_days, dry)}


def _main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "run"
    dry = "--dry" in argv
    def opt(name, d):
        return int(argv[argv.index(name) + 1]) if name in argv else d
    days, idays = opt("--days", 2), opt("--inactive-days", 7)
    tag = " (dry-run)" if dry else ""
    if cmd in ("run", "replies"):
        r = poll_replies(days, dry)
        print(f"↩ {len(r)} reply→In Contact{tag}")
        for e in r:
            print(f"   [{e['proj']}] {e['vendor']}  <{e['email']}>  {e['from']}→In Contact | {e['subject'][:50]}")
    if cmd in ("run", "inactive"):
        r = mark_inactive(idays, dry)
        print(f"💤 {len(r)} → Inactive (>{idays}d no reply){tag}")
        for e in r:
            print(f"   [{e['proj']}] {e['vendor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

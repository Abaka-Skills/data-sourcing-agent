"""Lark sourcing backend — write sourced vendors into a project's P0X table.

The Lark model is one table per project (see lark.project_tables()); a vendor is
a row in its project's table (NOT a shared Vendor Tracker keyed by Vendor ID like
the Google backend). This module is what the source-vendors / draft-outreach
skills call when a project lives in Lark.

CLI (project id accepts a short code "P03" or a full name "P03-Robo-AppliedIntuition"):
  python3 tools/larksource.py project  <P>            # the intake scope to match
  python3 tools/larksource.py vendors  <P>            # existing rows (for re-check/dedup)
  python3 tools/larksource.py add-vendor <P>          # stdin JSON, dedup + upsert one vendor
  python3 tools/larksource.py outreach-list <P> [--status Identified] [--due]   # outreach/follow-up worklist
  python3 tools/larksource.py update-vendor <P>       # stdin JSON incl. "Vendor" — targeted row update
  python3 tools/larksource.py followup-send <P> "<vendor>" [--force]   # in-thread nudge + guardrail
  python3 tools/larksource.py dedup-key --name n --website w
"""
from __future__ import annotations

import json
import re
import sys
import time

sys.path.insert(0, "tools")
import lark  # noqa: E402

# generic sourcing keys (as the skill emits them) -> P0X column names
FIELD_ALIASES = {
    "Vendor name": "Vendor", "Vendor": "Vendor", "name": "Vendor",
    "Website": "Website", "website": "Website",
    "Origin": "Origin",
    "Supply type": "Supply type",
    "Category": "Category",
    "Contact name": "Contact name", "Contact role": "Contact role",
    "Contact email": "Contact email", "email": "Contact email",
    "Fit": "Fit",
    "Fit rationale": "Notes", "Notes": "Notes", "notes": "Notes",
    "Priority": "Priority",
    "Pricing": "Pricing",
    "Sample Data": "Sample Data",
    "Environments Supported": "Environments Supported", "Environments": "Environments Supported",
    "Hours": "Hours",
    "Status": "Status", "Source Status": "Source Status",
    "Owner": "Owner",
}
PRIORITY_MAP = {"high": "S", "medium": "A", "med": "A", "low": "B", "s": "S", "a": "A", "b": "B"}


def project_table(p: str) -> tuple[str, str]:
    """Resolve 'P03' or 'P03-Robo-AppliedIntuition' -> (full_name, table_id)."""
    pt = lark.project_tables()
    key = p.strip().lower().replace("-", "").replace("_", "")
    for name, tid in pt.items():
        norm = name.lower().replace("-", "").replace("_", "")
        if norm == key or norm.startswith(key) or name.lower().startswith(p.strip().lower()):
            return name, tid
    sys.exit(f"no project table matches {p!r}; known: {list(pt)}")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _weburl(v) -> str:
    """Extract a plain URL string from a URL-field value (dict/list/str)."""
    if isinstance(v, dict):
        return v.get("link", v.get("text", ""))
    if isinstance(v, list):
        return _weburl(v[0]) if v else ""
    return v or ""


# Aggregator / directory domains: many DIFFERENT vendors share these, so a
# domain match here is NOT a same-vendor signal — dedup by name only.
AGGREGATOR_DOMAINS = {"datarade.ai", "linkedin.com", "crunchbase.com", "github.com",
                      "huggingface.co", "kaggle.com", "facebook.com", "twitter.com", "x.com"}


def _domain(v) -> str:
    url = _weburl(v)
    m = re.search(r"https?://([^/]+)", url)
    host = (m.group(1) if m else url).lower().lstrip("www.")
    host = host.split("/")[0]
    return "" if host in AGGREGATOR_DOMAINS else host


def vendors(p: str) -> list[dict]:
    _, tid = project_table(p)
    out = []
    for r in lark.api("GET", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records?page_size=200").get("data", {}).get("items", []):
        f = r.get("fields", {})
        out.append({"record_id": r["record_id"], "Vendor": lark._cell(f.get("Vendor")),
                    "Website": _weburl(f.get("Website")), "Fit": lark._cell(f.get("Fit")),
                    "Status": lark._cell(f.get("Status"))})
    return out


def get_project(p: str) -> dict:
    name, tid = project_table(p)
    rec = None
    for r in lark.get_records("Projects"):
        link = r["fields"].get("Open ↗")
        url = link.get("link", "") if isinstance(link, dict) else str(link or "")
        if tid in url:                       # robust join: jump-link carries the table_id
            rec = r
            break
    rec = rec or lark.find_record("Projects", "Project", name)   # fallback: exact name
    return {"project": name, "fields": {k: (_weburl(v) if k == "Open ↗" else lark._cell(v))
                                        for k, v in (rec or {}).get("fields", {}).items()}}


def _map_fields(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        col = FIELD_ALIASES.get(k)
        if not col or v in (None, ""):
            continue
        if col == "Priority":
            v = PRIORITY_MAP.get(str(v).strip().lower(), v)
        if col == "Website" and isinstance(v, str):
            v = {"link": v, "text": v}          # URL field wants a link object
        out[col] = v
    out.setdefault("Status", "Identified")
    out.setdefault("Active/Inactive", "Active")
    return out


def _check(resp: dict, what: str) -> None:
    if resp.get("code") not in (0, None):
        sys.exit(f"{what} failed: {resp.get('code')} {resp.get('msg')}")


def upsert(p: str, row: dict) -> dict:
    """Dedup by normalized name or website domain, then insert/update one vendor."""
    name, tid = project_table(p)
    fields = _map_fields(row)
    vn = fields.get("Vendor", "")
    if not vn:
        sys.exit("add-vendor: row needs a Vendor / name")
    nkey, dkey = _norm(vn), _domain(fields.get("Website", ""))
    existing = lark.api("GET", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records?page_size=200").get("data", {}).get("items", [])
    match = None
    for r in existing:
        f = r.get("fields", {})
        if _norm(lark._cell(f.get("Vendor"))) == nkey or (dkey and _domain(f.get("Website")) == dkey):
            match = r
            break
    app = lark._app()
    if match:
        # don't stomp a human-advanced Status back to Identified
        cur = lark._cell(match["fields"].get("Status"))
        if cur and cur != "Identified" and fields.get("Status") == "Identified":
            fields.pop("Status", None)
        _check(lark.api("PUT", f"/open-apis/bitable/v1/apps/{app}/tables/{tid}/records/{match['record_id']}", {"fields": fields}), "update")
        return {"action": "updated", "project": name, "vendor": vn}
    _check(lark.api("POST", f"/open-apis/bitable/v1/apps/{app}/tables/{tid}/records", {"fields": fields}), "add")
    return {"action": "added", "project": name, "vendor": vn}


# ---------------------------------------------------------------- outreach
FOLLOWUP_DAYS = 5
FOLLOWUP_MAX = 3


def _now_ms() -> int:
    return int(time.time() * 1000)


def _days_since(ms) -> float | None:
    if not isinstance(ms, (int, float)):
        return None
    return (time.time() * 1000 - ms) / 86_400_000


def _find(tid: str, vendor: str) -> dict | None:
    nk = _norm(vendor)
    for r in lark.api("GET", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records?page_size=200").get("data", {}).get("items", []):
        if _norm(lark._cell(r["fields"].get("Vendor"))) == nk:
            return r
    return None


def outreach_list(p: str, status: str | None = None, due: bool = False,
                  days: int = FOLLOWUP_DAYS, max_fu: int = FOLLOWUP_MAX) -> list[dict]:
    """Vendors with the outreach-relevant fields. `due=True` filters to
    follow-up-due (Outreached, ≥days since last contact, < max follow-ups)."""
    _, tid = project_table(p)
    out = []
    for r in lark.api("GET", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records?page_size=200").get("data", {}).get("items", []):
        f = r.get("fields", {})
        st = lark._cell(f.get("Status"))
        fu = int(lark._cell(f.get("Follow-up count")) or 0)
        d = _days_since(f.get("Last contact date"))
        row = {"record_id": r["record_id"], "Vendor": lark._cell(f.get("Vendor")),
               "email": lark._cell(f.get("Contact email")), "Status": st,
               "draft": lark._cell(f.get("Draft/email link")), "follow_ups": fu,
               "days_since": round(d, 1) if d is not None else None}
        if due:
            if st != "Outreached" or fu >= max_fu or d is None or d < days:
                continue
        elif status and st != status:
            continue
        out.append(row)
    return out


def update_by_name(p: str, vendor: str, fields: dict) -> dict:
    """Targeted update of one vendor row by name — no Identified default."""
    _, tid = project_table(p)
    rec = _find(tid, vendor)
    if not rec:
        sys.exit(f"update-vendor: no vendor {vendor!r} in {p}")
    fields = dict(fields)
    if isinstance(fields.get("Website"), str):
        fields["Website"] = {"link": fields["Website"], "text": fields["Website"]}
    if "Follow-up count" in fields:
        fields["Follow-up count"] = int(fields["Follow-up count"])
    lcd = fields.get("Last contact date")           # DateTime field wants epoch-ms
    if isinstance(lcd, str):
        if lcd.strip().lower() in ("today", "now", ""):
            fields["Last contact date"] = _now_ms()
        else:
            try:
                fields["Last contact date"] = int(time.mktime(time.strptime(lcd.strip()[:10], "%Y-%m-%d")) * 1000)
            except ValueError:
                fields.pop("Last contact date")
    _check(lark.api("PUT", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records/{rec['record_id']}", {"fields": fields}), "update")
    return {"action": "updated", "vendor": lark._cell(rec["fields"].get("Vendor"))}


def followup_send(p: str, vendor: str, body: str | None = None, force: bool = False) -> dict:
    """Send an in-thread nudge, with the reply guardrail (mirrors followups.py)."""
    import gmail
    _, tid = project_table(p)
    rec = _find(tid, vendor)
    if not rec:
        sys.exit(f"followup-send: no vendor {vendor!r} in {p}")
    f = rec["fields"]
    email = lark._cell(f.get("Contact email")).strip()
    if not email:
        sys.exit(f"followup-send: {vendor} has no Contact email")
    thread = gmail.latest_thread_with(email)
    if not thread:
        sys.exit(f"followup-send: no Gmail thread with {email} (was outreach sent?)")
    if not force and gmail.has_reply_after(thread).get("has_reply"):
        update_by_name(p, vendor, {"Status": "In Contact", "Last contact date": _now_ms(),
                                   "Latest update": f"↩ already replied (seen {time.strftime('%Y-%m-%d')})"})
        return {"action": "skipped-replied", "vendor": vendor}
    msgs = gmail.list_thread_messages(thread)
    subject = next((m["subject"] for m in reversed(msgs) if m.get("subject")), "Following up")
    text = body or (f"Hi,\n\nJust following up on my note below — would a quick 30-minute call make sense "
                    f"to explore a data partnership? Happy to work around your schedule.\n\nBest regards")
    gmail.send_in_thread(thread, email, subject, text)
    fu = int(lark._cell(f.get("Follow-up count")) or 0) + 1
    update_by_name(p, vendor, {"Follow-up count": fu, "Last contact date": _now_ms(),
                               "Latest update": f"→ follow-up #{fu} sent {time.strftime('%Y-%m-%d')}"})
    return {"action": "sent", "vendor": vendor, "follow_up_count": fu, "at_max": fu >= FOLLOWUP_MAX}


def report(p: str) -> str:
    """Detailed textual per-project status — one block per vendor, grouped by
    Status, S-priority first."""
    name, tid = project_table(p)
    scope = get_project(p)["fields"]
    vs = lark.api("GET", f"/open-apis/bitable/v1/apps/{lark._app()}/tables/{tid}/records?page_size=200").get("data", {}).get("items", [])
    rank = {"S": 0, "A": 1, "B": 2}
    order = ["Collaborating", "In Contact", "Outreached", "Identified", "Rejected", ""]
    vs.sort(key=lambda r: (order.index(lark._cell(r["fields"].get("Status")) if lark._cell(r["fields"].get("Status")) in order else ""),
                           rank.get(lark._cell(r["fields"].get("Priority")), 9)))
    head = (f"📋 {name}" + (f" · {scope.get('Client Name','')}" if scope.get("Client Name") else "")
            + (f" · {scope.get('Data Type','')}" if scope.get("Data Type") else "") + f" — {len(vs)} vendors")
    lines = [head, ""]
    for r in vs:
        f = r["fields"]
        pr, st = lark._cell(f.get("Priority")), lark._cell(f.get("Status"))
        lines.append(f"● {lark._cell(f.get('Vendor'))}  [{pr or '—'}]  {st or '—'}")
        status_txt = lark._cell(f.get("Source Status")).strip() or lark._cell(f.get("Latest update")).strip()
        if status_txt:
            lines.append(f"   status: {' '.join(status_txt.split())[:220]}")
        for label, key in [("pricing", "Pricing"), ("hours", "Hours"), ("device", "Device")]:
            v = lark._cell(f.get(key)).strip()
            if v:
                lines.append(f"   {label}: {v[:120]}")
        sample = _weburl(f.get("Sample Data")) or lark._cell(f.get("Sample Data")).strip()
        if sample:
            lines.append(f"   sample: {'yes ✓' if sample.startswith('http') else sample[:80]}")
        lines.append("")
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "project":
        print(json.dumps(get_project(argv[1]), ensure_ascii=False, indent=1))
    elif cmd == "vendors":
        vs = vendors(argv[1])
        print(f"{len(vs)} existing in {project_table(argv[1])[0]}:")
        for v in vs:
            print(f"  {v['Vendor']:32} {v['Fit']:8} {v['Status']:12} {v['Website']}")
    elif cmd == "add-vendor":
        row = json.loads(sys.stdin.read())
        print(json.dumps(upsert(argv[1], row), ensure_ascii=False))
    elif cmd == "report":
        print(report(argv[1]))
    elif cmd == "outreach-list":
        def opt(n, d=None):
            return argv[argv.index(n) + 1] if n in argv else d
        due = "--due" in argv
        rows = outreach_list(argv[1], status=opt("--status"), due=due,
                             days=int(opt("--days", FOLLOWUP_DAYS)), max_fu=int(opt("--max", FOLLOWUP_MAX)))
        label = "follow-up due" if due else (opt("--status") or "all")
        print(f"{len(rows)} vendors [{label}] in {project_table(argv[1])[0]}:")
        for v in rows:
            mark = "✉draft" if v["draft"] else ("no-email" if not v["email"] else "")
            print(f"  {v['Vendor']:30} {v['Status']:11} fu={v['follow_ups']} "
                  f"{('%.0fd' % v['days_since']) if v['days_since'] is not None else '-':>5}  {v['email']:32} {mark}")
    elif cmd == "update-vendor":
        row = json.loads(sys.stdin.read())
        vendor = row.pop("Vendor", None) or row.pop("vendor", None)
        if not vendor:
            sys.exit("update-vendor: JSON must include a 'Vendor' key naming the row")
        print(json.dumps(update_by_name(argv[1], vendor, row), ensure_ascii=False))
    elif cmd == "followup-send":
        body = None if sys.stdin.isatty() else (sys.stdin.read().strip() or None)
        print(json.dumps(followup_send(argv[1], argv[2], body=body, force="--force" in argv), ensure_ascii=False))
    elif cmd == "dedup-key":
        def opt(n):
            return argv[argv.index(n) + 1] if n in argv else ""
        print(_norm(opt("--name")) + "|" + _domain(opt("--website")))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

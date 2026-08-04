"""Apollo.io adapter — contact discovery + sequence enrollment.

Complements the Gmail/Sheets toolkit: Apollo finds NAMED contacts with verified
emails at vendor companies (filling the gap where a vendor's website publishes
only a web form or a candidate/HR inbox), and can enroll them into an Apollo
sequence for automated multi-step outreach.

The Google Sheet stays the source of truth; this module reads/writes the
`Contact name` / `Contact role` / `Contact email` columns on Vendor Tracker rows.

Credentials: ~/.abaka/apollo.json  {"api_key": "..."}  (or env APOLLO_API_KEY).
Never commit that file — it lives outside the repo like the Google secrets.

API notes (verified 2026-07-16):
  * people search  -> POST /api/v1/mixed_people/api_search   (the older
    mixed_people/search is deprecated for API callers)
  * sequences      -> POST /api/v1/emailer_campaigns/search  (GET 404s)
  * reveal/enrich  -> POST /api/v1/people/match  (costs a credit; email_status
    "unavailable" means Apollo has no email for that person, not a plan limit)

CLI:
  python3 tools/apollo.py --selftest
  python3 tools/apollo.py sequences
  python3 tools/apollo.py find-contacts --domain keymakr.com [--reveal]
  python3 tools/apollo.py enrich-project P-04 [--apply] [--limit N]
  python3 tools/apollo.py push --project P-04 --sequence <id> [--confirm]
  python3 tools/apollo.py pull --project P-04
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

BASE = "https://api.apollo.io/api/v1"
ABAKA_DIR = pathlib.Path.home() / ".abaka"
KEY_PATH = ABAKA_DIR / "apollo.json"

# Titles worth reaching for vendor/partner sourcing, best-first.
DEFAULT_TITLES = [
    "business development", "partnerships", "sales", "commercial",
    "founder", "ceo", "chief executive", "managing director", "account executive",
]


def api_key() -> str:
    key = os.environ.get("APOLLO_API_KEY")
    if key:
        return key
    if KEY_PATH.exists():
        k = json.loads(KEY_PATH.read_text()).get("api_key")
        if k:
            return k
    sys.exit(
        "no Apollo API key. Put it in ~/.abaka/apollo.json as "
        '{"api_key": "..."} or set APOLLO_API_KEY.'
    )


def _call(method: str, path: str, body: dict | None = None, *, retries: int = 3):
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Cache-Control", "no-cache")
        req.add_header("accept", "application/json")
        req.add_header("x-api-key", api_key())
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            payload = e.read().decode()[:300]
            # 429 = rate limited; back off and retry
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise SystemExit(f"Apollo {method} {path} -> HTTP {e.code}: {payload}")
    raise SystemExit(f"Apollo {method} {path}: retries exhausted")


# ---------------------------------------------------------------- discovery

def search_people(domain: str, titles: list[str] | None = None, per_page: int = 10) -> list[dict]:
    """People at a company domain. Names/emails come back masked — reveal() them."""
    body = {
        "q_organization_domains_list": [domain],
        "page": 1,
        "per_page": per_page,
    }
    if titles:
        body["person_titles"] = titles
    return _call("POST", "/mixed_people/api_search", body).get("people", []) or []


def reveal(person_id: str, personal_emails: bool = False) -> dict:
    """Enrich one person -> real name + email. Costs an Apollo credit."""
    r = _call("POST", "/people/match",
              {"id": person_id, "reveal_personal_emails": personal_emails})
    return r.get("person") or {}


def best_contact(domain: str, titles: list[str] | None = None, tries: int = 5) -> dict | None:
    """Search a domain and reveal candidates until one has a usable email."""
    people = search_people(domain, titles or DEFAULT_TITLES, per_page=max(tries, 5))
    for p in people[:tries]:
        pid = p.get("id")
        if not pid:
            continue
        person = reveal(pid)
        email = person.get("email")
        if email and person.get("email_status") in ("verified", "likely", "guessed"):
            return {
                "name": person.get("name"),
                "title": person.get("title"),
                "email": email,
                "email_status": person.get("email_status"),
                "company": (person.get("organization") or {}).get("name"),
                "apollo_id": pid,
            }
        time.sleep(0.3)
    return None


def enrich_org(domain: str) -> dict:
    q = urllib.parse.urlencode({"domain": domain})
    return _call("GET", f"/organizations/enrich?{q}").get("organization") or {}


# ---------------------------------------------------------------- sequences

def sequences(per_page: int = 50) -> list[dict]:
    r = _call("POST", "/emailer_campaigns/search", {"page": 1, "per_page": per_page})
    return r.get("emailer_campaigns", []) or []


def add_to_sequence(sequence_id: str, contact_ids: list[str], email_account_id: str) -> dict:
    """Enroll contacts into a sequence. OUTWARD-FACING: this starts real sends."""
    return _call("POST", f"/emailer_campaigns/{sequence_id}/add_contact_ids", {
        "contact_ids": contact_ids,
        "emailer_campaign_id": sequence_id,
        "send_email_from_email_account_id": email_account_id,
    })


def email_accounts() -> list[dict]:
    return _call("GET", "/email_accounts").get("email_accounts", []) or []


def pick_mailbox(from_domain: str | None = None) -> dict:
    """Choose a sending mailbox. If from_domain given, prefer a mailbox on that
    domain (e.g. 'abaka.business'); else the default/active one."""
    accts = [a for a in email_accounts() if a.get("active")]
    if not accts:
        raise SystemExit("no active Apollo mailbox connected — connect one in Apollo → Settings → Mailboxes")
    if from_domain:
        dom = from_domain.lower().lstrip("@")
        matches = [a for a in accts if (a.get("email") or "").lower().endswith("@" + dom)]
        if matches:
            return matches[0]
        raise SystemExit(
            f"no connected Apollo mailbox on @{dom}. Connected: "
            + ", ".join(a.get("email") for a in accts)
            + ". Connect the mailbox in Apollo first (see domain-setup steps)."
        )
    for a in accts:
        if a.get("default"):
            return a
    return accts[0]


def create_contact(first_name: str, last_name: str, email: str,
                   title: str = "", company: str = "", website: str = "") -> dict:
    """Create (or match) an Apollo contact. Safe — does not send anything."""
    body = {"first_name": first_name, "last_name": last_name, "email": email}
    if title:
        body["title"] = title
    if company:
        body["organization_name"] = company
    if website:
        body["website_url"] = website
    return _call("POST", "/contacts", body).get("contact") or {}


def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:]) or parts[0]


def sync_contacts(project_id: str) -> list[dict]:
    """Mirror a project's tracker vendors (those with an email) into Apollo.

    Returns one record per vendor with the Apollo contact id. Creating contacts
    does NOT send anything — enrollment is a separate, gated step.
    """
    sh = _sheets()
    out = []
    for v in sh.get_vendors(project_id):
        email = (v.get("Contact email") or "").strip()
        if not email:
            continue
        first, last = _split_name(v.get("Contact name") or "")
        if not first:
            # generic inbox (info@/comercial@) — use the vendor name as the handle
            first, last = (v.get("Vendor name") or "Partner"), "Team"
        try:
            c = create_contact(first, last, email,
                               title=v.get("Contact role") or "",
                               company=v.get("Vendor name") or "",
                               website=v.get("Website") or "")
            out.append({"Vendor ID": v["Vendor ID"], "email": email,
                        "apollo_contact_id": c.get("id"), "result": "synced"})
        except SystemExit as e:
            out.append({"Vendor ID": v["Vendor ID"], "email": email, "result": f"error: {e}"})
        time.sleep(0.35)
    return out


def push(project_id: str, sequence_id: str, mailbox_id: str | None = None,
         from_domain: str | None = None) -> dict:
    """Sync a project's contacts into Apollo and enroll them in a sequence.

    OUTWARD-FACING — enrollment starts the live cadence. Callers must gate this
    behind an explicit human approval. Pass from_domain (e.g. 'abaka.business')
    to send from a specific sending domain's mailbox.
    """
    if not mailbox_id:
        mailbox_id = pick_mailbox(from_domain)["id"]
    synced = sync_contacts(project_id)
    ids = [s["apollo_contact_id"] for s in synced if s.get("apollo_contact_id")]
    if not ids:
        return {"enrolled": 0, "detail": "no contacts with emails on this project"}
    res = add_to_sequence(sequence_id, ids, mailbox_id)
    return {"enrolled": len(ids), "sequence": sequence_id, "mailbox": mailbox_id,
            "contacts": synced, "apollo_response_keys": list(res.keys())}


# ---------------------------------------------------------------- status sync

# Apollo per-contact campaign status -> tracker Status
_STATUS_MAP = {
    "replied": "In Contact", "interested": "In Contact", "meeting_booked": "In Contact",
    "active": "Outreached", "finished": "Outreached", "paused": "Outreached",
    "not_sent": "Identified",
}


def _campaign_contacts(sequence_id: str) -> list[dict]:
    out, page = [], 1
    while True:
        r = _call("POST", "/contacts/search",
                  {"q_emailer_campaign_ids": [sequence_id], "page": page, "per_page": 100})
        batch = r.get("contacts", []) or []
        out.extend(batch)
        pag = r.get("pagination", {}) or {}
        if page >= (pag.get("total_pages") or 1) or not batch:
            break
        page += 1
        time.sleep(0.3)
    return out


def pull(project_id: str, sequence_id: str, apply: bool = False) -> dict:
    """Read the sequence's live status from Apollo and reflect it on the project's
    tracker rows: replied -> In Contact, bounced -> flagged, else Outreached."""
    sh = _sheets()
    camp = _call("GET", f"/emailer_campaigns/{sequence_id}").get("emailer_campaign") or {}
    stats = {
        "delivered": camp.get("unique_delivered_open_tracked"),
        "opened": camp.get("unique_opened"),
        "replied_rate": camp.get("reply_rate"),
        "bounced": camp.get("unique_bounced"),
        "contact_statuses": camp.get("contact_statuses"),
    }
    email_to_vid = {}
    for v in sh.get_vendors(project_id):
        e = (v.get("Contact email") or "").strip().lower()
        if e:
            email_to_vid[e] = (v["Vendor ID"], v.get("Status"))

    updates = []
    for ct in _campaign_contacts(sequence_id):
        email = (ct.get("email") or "").strip().lower()
        if email not in email_to_vid:
            continue
        vid, cur = email_to_vid[email]
        # find this campaign's status entry on the contact
        st = None
        for cs in (ct.get("contact_campaign_statuses") or []):
            if cs.get("emailer_campaign_id") == sequence_id:
                st = (cs.get("status") or cs.get("status_cd") or "").lower()
                break
        if (ct.get("email_status") == "bounced") or st in ("bounced", "hard_bounced"):
            new = None  # don't overwrite; just flag below
            flag = "Bounced"
        else:
            new = _STATUS_MAP.get(st)
            flag = None
        rec = {"Vendor ID": vid, "email": email, "apollo_status": st, "new_status": new, "flag": flag}
        if apply and (new and new != cur or flag):
            fields = {}
            if new and new != cur:
                fields["Status"] = new
            if flag:
                fields["Follow-up Action"] = flag
            if fields:
                fields["Last updated"] = sh.today()
                sh.update_fields("Vendor Tracker", vid, fields)
                rec["written"] = True
        updates.append(rec)
        time.sleep(0.05)
    return {"sequence": sequence_id, "stats": stats,
            "matched_rows": len(updates), "updates": updates,
            "applied": bool(apply)}


# ---------------------------------------------------------------- tracker glue

def _sheets():
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import sheets  # noqa: E402
    return sheets


def _domain_of(website: str) -> str | None:
    if not website:
        return None
    host = urllib.parse.urlparse(website if "//" in website else f"https://{website}").netloc
    return host.replace("www.", "").strip() or None


def enrich_project(project_id: str, apply: bool = False, limit: int | None = None) -> list[dict]:
    """Find a named contact for every vendor on a project that lacks an email."""
    sh = _sheets()
    rows = [v for v in sh.get_vendors(project_id) if not (v.get("Contact email") or "").strip()]
    if limit:
        rows = rows[:limit]
    out = []
    for v in rows:
        dom = _domain_of(v.get("Website", ""))
        rec = {"Vendor ID": v["Vendor ID"], "Vendor name": v.get("Vendor name"), "domain": dom}
        if not dom:
            rec["result"] = "no website on row"
            out.append(rec)
            continue
        try:
            hit = best_contact(dom)
        except SystemExit as e:
            rec["result"] = f"error: {e}"
            out.append(rec)
            continue
        if hit:
            rec.update(hit)
            rec["result"] = "found"
            if apply:
                sh.update_fields("Vendor Tracker", v["Vendor ID"], {
                    "Contact name": hit["name"] or "",
                    "Contact role": hit["title"] or "",
                    "Contact email": hit["email"],
                })
                rec["written"] = True
        else:
            rec["result"] = "no email available"
        out.append(rec)
        time.sleep(0.4)
    return out


# ---------------------------------------------------------------- CLI

def _opts(rest: list[str]) -> dict:
    o = {}
    i = 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("--"):
            k = a[2:]
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                o[k] = rest[i + 1]
                i += 2
            else:
                o[k] = True
                i += 1
        else:
            i += 1
    return o


def _selftest() -> int:
    h = _call("GET", "/auth/health")
    print(f"ok: auth healthy={h.get('healthy')} logged_in={h.get('is_logged_in')}")
    seqs = sequences()
    print(f"ok: {len(seqs)} sequence(s) visible")
    for s in seqs[:5]:
        print(f"    {s.get('id')}  {s.get('name')}  (steps: {s.get('num_steps')})")
    return 0


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    o = _opts(rest)

    if cmd == "--selftest":
        return _selftest()
    if cmd == "sequences":
        print(json.dumps(sequences(), indent=2, default=str)[:4000])
    elif cmd in ("email-accounts", "mailboxes"):
        print(json.dumps([{k: a.get(k) for k in ("id", "email", "type", "active", "default")}
                          for a in email_accounts()], indent=2))
    elif cmd == "find-contacts":
        dom = o.get("domain") or sys.exit("--domain required")
        titles = (o["titles"].split(",") if isinstance(o.get("titles"), str) else DEFAULT_TITLES)
        if o.get("reveal"):
            print(json.dumps(best_contact(dom, titles), indent=2))
        else:
            ppl = search_people(dom, titles)
            print(json.dumps([{"id": p.get("id"), "title": p.get("title"),
                               "org": (p.get("organization") or {}).get("name")} for p in ppl], indent=2))
    elif cmd == "enrich-project":
        pid = rest[0] if rest and not rest[0].startswith("--") else sys.exit("project id required")
        res = enrich_project(pid, apply=bool(o.get("apply")),
                             limit=int(o["limit"]) if o.get("limit") else None)
        print(json.dumps(res, indent=2))
        found = sum(1 for r in res if r.get("result") == "found")
        print(f"\n{found}/{len(res)} contacts found"
              f"{' (written to sheet)' if o.get('apply') else ' (dry run — pass --apply to write)'}")
    elif cmd == "sync-contacts":
        pid = rest[0] if rest and not rest[0].startswith("--") else sys.exit("project id required")
        res = sync_contacts(pid)
        print(json.dumps(res, indent=2))
        print(f"\n{sum(1 for r in res if r.get('result') == 'synced')}/{len(res)} contacts in Apollo "
              "(nothing sent — enrollment is a separate step)")
    elif cmd == "push":
        pid, seq = o.get("project"), o.get("sequence")
        if not pid or not seq:
            return sys.exit("usage: push --project P-05 --sequence <id> [--from-domain abaka.business] --confirm")
        if not o.get("confirm"):
            return sys.exit("push ENROLLS contacts into a live sequence (starts real sends). "
                            "Re-run with --confirm once a human has approved.")
        fd = o.get("from-domain") if isinstance(o.get("from-domain"), str) else None
        print(json.dumps(push(pid, seq, o.get("mailbox"), from_domain=fd), indent=2))
    elif cmd == "pull":
        pid, seq = o.get("project"), o.get("sequence")
        if not pid or not seq:
            return sys.exit("usage: pull --project P-05 --sequence <id> [--apply]")
        res = pull(pid, seq, apply=bool(o.get("apply")))
        print(json.dumps(res, indent=2, default=str))
        print(f"\n{res['matched_rows']} tracker rows matched"
              f"{' (written)' if o.get('apply') else ' (dry run — pass --apply to write)'}")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

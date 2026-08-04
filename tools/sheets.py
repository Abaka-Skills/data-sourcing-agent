"""Google Sheet adapter — the single source of truth.

Three tabs:
  Projects        one row per sourcing project        (PK: "Project ID")
  Vendor Tracker  one row per vendor x project         (PK: "Engagement ID")
  Config          hidden key/value mirror of identity/settings

Design rules that keep the sheet safe to hand-edit while an agent also writes it:
  * Upserts are keyed and merge-only. We update just the cells we were given and
    always stamp "Last updated"; columns a human edited are never blanked.
  * Reads return plain dicts (gspread get_all_records), so skills reason over
    normal Python data.

CLI (skills shell out to these):
  python3 tools/sheets.py --selftest
  python3 tools/sheets.py get-projects
  python3 tools/sheets.py get-project P-06
  python3 tools/sheets.py get-vendors [--project P-06] [--status outreached]
  python3 tools/sheets.py next-id project|vendor|engagement
  echo '{...}' | python3 tools/sheets.py upsert-project
  echo '{...}' | python3 tools/sheets.py upsert-vendor
  echo '{...fields...}' | python3 tools/sheets.py update-vendor E-001
"""
from __future__ import annotations

import datetime
import json
import re
import sys

import auth
import config
import domains

# Tab names are the same across domains; row read/write here is header-agnostic.
# The Vendor Tracker primary key is domain-specific (see _pk).
PROJECTS = "Projects"
VENDORS = "Vendor Tracker"
CONFIG = "Config"


def _pk(name: str) -> str:
    """Primary-key column for a tab. Vendor Tracker key is domain-specific:
    'Vendor ID' where the model is one-row-per-vendor (medical), else 'Engagement ID'."""
    if name == VENDORS:
        try:
            return domains.get_profile(config.get_domain()).get("vendor_key", "Engagement ID")
        except SystemExit:
            return "Engagement ID"
    return {PROJECTS: "Project ID", CONFIG: "Key"}[name]

_spreadsheet = None       # process-level cache
_spreadsheet_key = None   # the sheet_id the cache is for


def today() -> str:
    return datetime.date.today().isoformat()


def spreadsheet(*, interactive: bool = True):
    """Open the Google Sheet for the ACTIVE tracker (ABAKA_TRACKER env / default)."""
    global _spreadsheet, _spreadsheet_key
    sheet_id = config.get_sheet_id()  # friendly error before we touch auth
    if _spreadsheet is None or _spreadsheet_key != sheet_id:
        client = auth.gspread_client(interactive=interactive)
        _spreadsheet = client.open_by_key(sheet_id)
        _spreadsheet_key = sheet_id
    return _spreadsheet


def worksheet(name: str, *, interactive: bool = True):
    return spreadsheet(interactive=interactive).worksheet(name)


def get_records(name: str) -> list[dict]:
    return worksheet(name).get_all_records()


def _headers(ws) -> list[str]:
    return ws.row_values(1)


def _row_index(ws, key_col: str, key_val) -> int | None:
    """1-based spreadsheet row number for the record whose key_col == key_val."""
    records = ws.get_all_records()
    for offset, rec in enumerate(records):
        if str(rec.get(key_col, "")).strip() == str(key_val).strip():
            return offset + 2  # +1 header, +1 for 1-based
    return None


def _write_cells(ws, row_idx: int, headers: list[str], fields: dict) -> None:
    import gspread

    cells = []
    for col_name, value in fields.items():
        if col_name not in headers:
            continue
        col_idx = headers.index(col_name) + 1
        cells.append(gspread.Cell(row_idx, col_idx, "" if value is None else str(value)))
    if cells:
        ws.update_cells(cells, value_input_option="USER_ENTERED")


def upsert(name: str, row: dict) -> dict:
    """Insert or merge-update a row keyed on the tab's PK. Stamps Last updated."""
    ws = worksheet(name)
    headers = _headers(ws)
    key_col = _pk(name)
    key_val = row.get(key_col)
    if not key_val:
        raise SystemExit(f"error: {name} upsert requires a {key_col}")

    fields = dict(row)
    if "Last updated" in headers:
        fields["Last updated"] = today()

    idx = _row_index(ws, key_col, key_val)
    if idx is None:
        # Append a fresh, header-aligned row.
        values = [str(fields.get(h, "")) if fields.get(h) is not None else "" for h in headers]
        ws.append_row(values, value_input_option="USER_ENTERED")
        action = "inserted"
    else:
        _write_cells(ws, idx, headers, fields)
        action = "updated"
    return {"action": action, key_col: key_val}


def update_fields(name: str, key_val, fields: dict) -> dict:
    ws = worksheet(name)
    headers = _headers(ws)
    key_col = _pk(name)
    idx = _row_index(ws, key_col, key_val)
    if idx is None:
        raise SystemExit(f"error: no {name} row with {key_col}={key_val!r}")
    patch = dict(fields)
    if "Last updated" in headers:
        patch["Last updated"] = today()
    _write_cells(ws, idx, headers, patch)
    return {"action": "updated", key_col: key_val}


def get_vendors(project_id: str | None = None, status: str | None = None) -> list[dict]:
    rows = get_records(VENDORS)
    if project_id:
        rows = [r for r in rows if str(r.get("Project ID", "")).strip() == project_id]
    if status:
        rows = [r for r in rows if str(r.get("Status", "")).strip() == status]
    return rows


def get_project(project_id: str) -> dict | None:
    for r in get_records(PROJECTS):
        if str(r.get("Project ID", "")).strip() == project_id:
            return r
    return None


# ------------------------------- id helpers ------------------------------- #
def _max_suffix(values, prefix: str) -> int:
    best = 0
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    for v in values:
        m = pat.match(str(v).strip())
        if m:
            best = max(best, int(m.group(1)))
    return best


def next_project_id() -> str:
    ids = [r.get("Project ID", "") for r in get_records(PROJECTS)]
    return f"P-{_max_suffix(ids, 'P-') + 1:02d}"


def next_vendor_id() -> str:
    ids = [r.get("Vendor ID", "") for r in get_records(VENDORS)]
    return f"V-{_max_suffix(ids, 'V-') + 1:03d}"


def next_engagement_id() -> str:
    ids = [r.get("Engagement ID", "") for r in get_records(VENDORS)]
    return f"E-{_max_suffix(ids, 'E-') + 1:03d}"


# --------------------------------- config --------------------------------- #
def config_set(key: str, value: str) -> None:
    upsert(CONFIG, {"Key": key, "Value": value})


# ----------------------------------- CLI ---------------------------------- #
def _selftest() -> int:
    ws = worksheet(CONFIG)
    token = f"selftest-{datetime.datetime.now().isoformat(timespec='seconds')}"
    ws.update_acell("Z1", token)
    got = ws.acell("Z1").value
    ws.update_acell("Z1", "")
    ok = got == token
    print(f"{'ok' if ok else 'FAIL'}: wrote+read Config!Z1 (wrote {token!r}, read {got!r})")
    return 0 if ok else 1


def _read_stdin_json() -> dict:
    data = sys.stdin.read().strip()
    if not data:
        raise SystemExit("error: expected JSON on stdin")
    return json.loads(data)


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd == "--selftest":
        return _selftest()
    if cmd == "get-projects":
        print(json.dumps(get_records(PROJECTS), indent=2))
    elif cmd == "get-project":
        print(json.dumps(get_project(rest[0]), indent=2))
    elif cmd == "get-vendors":
        project = status = None
        i = 0
        while i < len(rest):
            if rest[i] == "--project":
                project = rest[i + 1]; i += 2
            elif rest[i] == "--status":
                status = rest[i + 1]; i += 2
            else:
                i += 1
        print(json.dumps(get_vendors(project, status), indent=2))
    elif cmd == "next-id":
        kind = rest[0]
        fn = {"project": next_project_id, "vendor": next_vendor_id,
              "engagement": next_engagement_id}[kind]
        print(fn())
    elif cmd == "upsert-project":
        print(json.dumps(upsert(PROJECTS, _read_stdin_json())))
    elif cmd == "upsert-vendor":
        print(json.dumps(upsert(VENDORS, _read_stdin_json())))
    elif cmd == "update-vendor":
        print(json.dumps(update_fields(VENDORS, rest[0], _read_stdin_json())))
    elif cmd == "update-project":
        print(json.dumps(update_fields(PROJECTS, rest[0], _read_stdin_json())))
    else:
        raise SystemExit(f"error: unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

"""Create or repair a tracker spreadsheet for a given domain.

Each tracker is one Google Sheet tied to one domain profile (tools/domains.py).
Idempotent: ensures the Projects / Vendor Tracker / Config tabs exist with the
domain's headers, dropdown validation, a frozen bold header row, and a hidden
Config tab. Registers the tracker in ~/.abaka/config.json.

Usage:
  python3 scripts/setup_sheet.py --create "Abaka Medical Data Sourcing"                       # medical (default)
  python3 scripts/setup_sheet.py --create --tracker robotics "Abaka Robotics Egocentric Sourcing"
  python3 scripts/setup_sheet.py --ensure <sheet_id> --tracker robotics --domain robotics_egocentric
  python3 scripts/setup_sheet.py                     # ensure the active tracker's sheet
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import auth  # noqa: E402
import config  # noqa: E402
import domains  # noqa: E402
import sheets  # noqa: E402

MIN_ROWS = {domains.PROJECTS_TAB: 1000, domains.VENDORS_TAB: 2000, domains.CONFIG_TAB: 200}

# Fit color-coding (conditional formatting): strong=green, partial=yellow, potential=blue.
FIT_COLORS = {
    "strong": {"red": 0.72, "green": 0.88, "blue": 0.72},
    "partial": {"red": 1.0, "green": 0.90, "blue": 0.55},
    "potential": {"red": 0.72, "green": 0.83, "blue": 0.96},
}


def _profile_tabs(profile: dict) -> dict:
    return {
        domains.PROJECTS_TAB: profile["project_headers"],
        domains.VENDORS_TAB: profile["vendor_headers"],
        domains.CONFIG_TAB: profile["config_headers"],
    }


def _set_headers(ws, headers: list[str]) -> None:
    import gspread

    need_cols = max(len(headers), 26)  # keep >=26 so the Config!Z1 selftest cell exists
    if ws.col_count < need_cols or ws.row_count < MIN_ROWS.get(ws.title, 1000):
        ws.resize(rows=max(ws.row_count, MIN_ROWS.get(ws.title, 1000)), cols=need_cols)
    if ws.row_values(1)[: len(headers)] != headers:
        cells = [gspread.Cell(1, i + 1, h) for i, h in enumerate(headers)]
        ws.update_cells(cells, value_input_option="USER_ENTERED")


def ensure_worksheets(ss, tabs: dict) -> dict:
    existing = {w.title: w for w in ss.worksheets()}
    if domains.PROJECTS_TAB not in existing and "Sheet1" in existing:
        existing["Sheet1"].update_title(domains.PROJECTS_TAB)
        existing = {w.title: w for w in ss.worksheets()}
    for title, headers in tabs.items():
        if title not in existing:
            ss.add_worksheet(title=title, rows=MIN_ROWS[title], cols=max(len(headers), 26))
    by_title = {w.title: w for w in ss.worksheets()}
    for title, headers in tabs.items():
        _set_headers(by_title[title], headers)
    return {w.title: w for w in ss.worksheets()}


def _enum_validation(gid, title, headers, col_name, values):
    c = headers.index(col_name)
    return {"setDataValidation": {
        "range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": MIN_ROWS[title],
                  "startColumnIndex": c, "endColumnIndex": c + 1},
        "rule": {"condition": {"type": "ONE_OF_LIST",
                               "values": [{"userEnteredValue": v} for v in values]},
                 "showCustomUi": True, "strict": False}}}


def _color_rule(gid, title, col_idx, value, color):
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": gid, "startRowIndex": 1, "endRowIndex": MIN_ROWS[title],
                    "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1}],
        "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": value}]},
                        "format": {"backgroundColor": color}}}}}


def apply_formatting(sheet_id: str, tabs: dict, profile: dict, ws_by_title: dict) -> None:
    service = auth.sheets_service()
    notes = profile.get("column_notes", {})
    # Clear existing conditional-format rules on managed tabs first (idempotent re-runs).
    meta = service.spreadsheets().get(
        spreadsheetId=sheet_id, fields="sheets(properties.sheetId,conditionalFormats)").execute()
    cf_counts = {s["properties"]["sheetId"]: len(s.get("conditionalFormats", []))
                 for s in meta["sheets"]}
    requests = []
    for title in tabs:
        gid = ws_by_title[title].id
        for _ in range(cf_counts.get(gid, 0)):
            requests.append({"deleteConditionalFormatRule": {"sheetId": gid, "index": 0}})

    for title, headers in tabs.items():
        gid = ws_by_title[title].id
        # Freeze header row (all tabs) + first 3 columns through Vendor name (Vendor Tracker).
        frozen_cols = 3 if title == domains.VENDORS_TAB else 0
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": gid,
                           "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": frozen_cols}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}})
        requests.append({"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold"}})
        # Dropdowns
        for col_name, values in profile["validations"].get(title, {}).items():
            if col_name in headers:
                requests.append(_enum_validation(gid, title, headers, col_name, values))
        # Fit colors (strong=green/partial=yellow/potential=blue) + Status colors
        if "Fit" in headers:
            for val, color in FIT_COLORS.items():
                requests.append(_color_rule(gid, title, headers.index("Fit"), val, color))
        if "Status" in headers:
            for val, color in domains.STATUS_COLORS.items():
                requests.append(_color_rule(gid, title, headers.index("Status"), val, color))
        # Header tooltips (cell notes on row 1)
        if any(h in notes for h in headers):
            requests.append({"updateCells": {
                "start": {"sheetId": gid, "rowIndex": 0, "columnIndex": 0},
                "rows": [{"values": [({"note": notes[h]} if h in notes else {}) for h in headers]}],
                "fields": "note"}})
        # Interactive sort/filter on the data tabs.
        if title in (domains.PROJECTS_TAB, domains.VENDORS_TAB):
            requests.append({"setBasicFilter": {"filter": {"range": {
                "sheetId": gid, "startRowIndex": 0,
                "startColumnIndex": 0, "endColumnIndex": len(headers)}}}})
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": ws_by_title[domains.CONFIG_TAB].id, "hidden": True},
        "fields": "hidden"}})
    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": requests}).execute()


def _col_a1(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


# Dashboard palette
_DASH = {
    "title_bg": {"red": 0.17, "green": 0.33, "blue": 0.39},   # dark teal banner
    "title_fg": {"red": 1.0, "green": 1.0, "blue": 1.0},
    "section_bg": {"red": 0.90, "green": 0.93, "blue": 0.95},  # light grey-blue
    "projhdr_bg": {"red": 0.81, "green": 0.89, "blue": 0.95},  # light blue
    "border": {"style": "SOLID", "color": {"red": 0.7, "green": 0.7, "blue": 0.7}},
}


def build_dashboard(ss, profile: dict) -> None:
    """Create/refresh a styled, live 'Dashboard' tab (banner, colored rows, borders)."""
    vh = profile["vendor_headers"]
    VT = f"'{domains.VENDORS_TAB}'"
    fit, status = _col_a1(vh.index("Fit")), _col_a1(vh.index("Status"))
    pid, draft = _col_a1(vh.index("Project ID")), _col_a1(vh.index("Draft/email link"))
    action = _col_a1(vh.index("Follow-up Action"))
    nproj = len(ss.worksheet(domains.PROJECTS_TAB).get_all_records())

    existing = {w.title: w for w in ss.worksheets()}
    ws = existing.get("Dashboard") or ss.add_worksheet(title="Dashboard", rows=100, cols=6, index=0)
    ws.clear()

    # Build rows while recording indices for later styling.
    rows, fit_rows, status_rows = [], {}, {}
    rows.append([f"{profile['label']} — Dashboard", "", ""])          # 0 title
    rows.append(["", "", ""])
    fit_hdr = len(rows); rows.append(["By Fit", "Count", ""])
    for f in ("strong", "partial", "potential"):
        fit_rows[len(rows)] = f
        rows.append([f, f"=COUNTIF({VT}!{fit}2:{fit},\"{f}\")", ""])
    rows.append(["", "", ""])
    stat_hdr = len(rows); rows.append(["By Status", "Count", ""])
    for s in domains.STATUS_VALUES:
        status_rows[len(rows)] = s
        rows.append([s, f"=COUNTIF({VT}!{status}2:{status},\"{s}\")", ""])
    rows.append(["", "", ""])
    act_hdr = len(rows); rows.append(["Action Items", "Count", ""])
    rows.append(["Drafts awaiting review",
                 f"=COUNTIFS({VT}!{status}2:{status},\"Identified\",{VT}!{draft}2:{draft},\"<>\")", ""])
    rows.append(["Needs follow-up", f"=COUNTIF({VT}!{action}2:{action},\"Needs Follow-up\")", ""])
    act_last = len(rows)
    rows.append(["", "", ""])
    proj_hdr = len(rows); rows.append(["By Project", "Vendors", "Strong"])
    proj_start = len(rows)
    rows.append([
        "=ARRAYFORMULA(IF('Projects'!A2:A=\"\",\"\",'Projects'!A2:A))",
        f"=ARRAYFORMULA(IF('Projects'!A2:A=\"\",\"\",COUNTIF({VT}!{pid}2:{pid},\"*\"&'Projects'!A2:A&\"*\")))",
        f"=ARRAYFORMULA(IF('Projects'!A2:A=\"\",\"\",COUNTIFS({VT}!{pid}2:{pid},\"*\"&'Projects'!A2:A&\"*\",{VT}!{fit}2:{fit},\"strong\")))"])
    ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")

    gid = ws.id
    reqs = []

    def fmt(r0, r1, c0, c1, cell, fields):
        reqs.append({"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
                      "startColumnIndex": c0, "endColumnIndex": c1},
            "cell": cell, "fields": fields}})

    def borders(r0, r1, c0, c1):
        b = _DASH["border"]
        reqs.append({"updateBorders": {
            "range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1,
                      "startColumnIndex": c0, "endColumnIndex": c1},
            "top": b, "bottom": b, "left": b, "right": b,
            "innerHorizontal": b, "innerVertical": b}})

    # Title banner (merge A1:C1)
    reqs.append({"mergeCells": {"mergeType": "MERGE_ALL",
                 "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1,
                           "startColumnIndex": 0, "endColumnIndex": 3}}})
    fmt(0, 1, 0, 3, {"userEnteredFormat": {
        "backgroundColor": _DASH["title_bg"], "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": _DASH["title_fg"]}}},
        "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)")
    # Section headers
    for hr in (fit_hdr, stat_hdr, act_hdr):
        fmt(hr, hr + 1, 0, 3, {"userEnteredFormat": {
            "backgroundColor": _DASH["section_bg"], "textFormat": {"bold": True}}},
            "userEnteredFormat(backgroundColor,textFormat)")
    # Project table header (colored + bold)
    fmt(proj_hdr, proj_hdr + 1, 0, 3, {"userEnteredFormat": {
        "backgroundColor": _DASH["projhdr_bg"], "textFormat": {"bold": True},
        "horizontalAlignment": "LEFT"}},
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)")
    # Fit / Status colored rows (match the tracker's Fit + Status colors)
    for r, f in fit_rows.items():
        fmt(r, r + 1, 0, 2, {"userEnteredFormat": {"backgroundColor": FIT_COLORS[f]}},
            "userEnteredFormat.backgroundColor")
    for r, s in status_rows.items():
        if s in domains.STATUS_COLORS:
            fmt(r, r + 1, 0, 2, {"userEnteredFormat": {"backgroundColor": domains.STATUS_COLORS[s]}},
                "userEnteredFormat.backgroundColor")
    # Center + bold the count columns (B for the stat tables, B:C for the project table)
    fmt(fit_hdr, act_last, 1, 2, {"userEnteredFormat": {
        "horizontalAlignment": "CENTER", "textFormat": {"bold": True}}},
        "userEnteredFormat(horizontalAlignment,textFormat)")
    fmt(proj_start, proj_start + max(nproj, 1), 1, 3, {"userEnteredFormat": {
        "horizontalAlignment": "CENTER"}}, "userEnteredFormat.horizontalAlignment")
    # Borders around each table
    borders(fit_hdr, fit_hdr + 4, 0, 2)                 # By Fit
    borders(stat_hdr, stat_hdr + 1 + len(domains.STATUS_VALUES), 0, 2)  # By Status
    borders(act_hdr, act_last, 0, 2)                    # Action Items
    borders(proj_hdr, proj_start + max(nproj, 1), 0, 3)  # By Project
    # Column widths
    for c0, c1, px in ((0, 1, 240), (1, 2, 110), (2, 3, 90)):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    # Keep Dashboard leftmost + a little taller title row
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": gid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 34}, "fields": "pixelSize"}})
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": gid, "index": 0}, "fields": "index"}})
    auth.sheets_service().spreadsheets().batchUpdate(
        spreadsheetId=ss.id, body={"requests": reqs}).execute()


def _mirror_identity_to_config() -> None:
    for key, value in config.load_profile().items():
        sheets.config_set(f"profile.{key}", value)


def _finish(ss, tracker: str, domain: str) -> None:
    profile = domains.get_profile(domain)
    tabs = _profile_tabs(profile)
    ws_by_title = ensure_worksheets(ss, tabs)
    config.set_tracker(tracker, ss.id, ss.url, domain)
    # Make this the active tracker for the rest of the process so sheets.* writes here.
    os.environ["ABAKA_TRACKER"] = tracker
    sheets._spreadsheet, sheets._spreadsheet_key = ss, ss.id
    apply_formatting(ss.id, tabs, profile, ws_by_title)
    build_dashboard(ss, profile)
    _mirror_identity_to_config()
    print(f"ok: tracker '{tracker}' ({domain}) ready — {ss.url}")
    print(f"    sheet_id={ss.id}")


def run_create(title: str, tracker: str, domain: str) -> None:
    service = auth.sheets_service()  # Sheets API create needs only the 'spreadsheets' scope
    created = service.spreadsheets().create(body={"properties": {"title": title}}).execute()
    ss = auth.gspread_client().open_by_key(created["spreadsheetId"])
    _finish(ss, tracker, domain)


def run_ensure(sheet_id: str, tracker: str, domain: str) -> None:
    _finish(auth.gspread_client().open_by_key(sheet_id), tracker, domain)


def _resolve_domain(tracker: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    if tracker in domains.DEFAULT_DOMAIN_FOR_TRACKER:
        return domains.DEFAULT_DOMAIN_FOR_TRACKER[tracker]
    try:
        return config.get_domain(tracker)   # existing tracker already knows its domain
    except SystemExit:
        raise SystemExit(f"error: pass --domain for tracker {tracker!r} "
                         f"(known domains: {', '.join(domains.PROFILES)})")


def _main(argv: list[str]) -> int:
    create = False
    ensure = tracker = domain_opt = None
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            return 0
        if a == "--create":
            create = True; i += 1
        elif a == "--ensure":
            ensure = argv[i + 1]; i += 2
        elif a == "--tracker":
            tracker = argv[i + 1]; i += 2
        elif a == "--domain":
            domain_opt = argv[i + 1]; i += 2
        else:
            positionals.append(a); i += 1

    tracker = tracker or config.active_tracker_name()
    domain = _resolve_domain(tracker, domain_opt)
    if create:
        if not positionals:
            raise SystemExit('error: --create needs a title, e.g. --create "Abaka Sourcing"')
        run_create(positionals[0], tracker, domain)
    elif ensure:
        run_ensure(ensure, tracker, domain)
    else:
        run_ensure(config.get_sheet_id(tracker), tracker, domain)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

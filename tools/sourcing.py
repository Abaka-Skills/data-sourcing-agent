"""Deterministic helpers for vendor sourcing (SOP step 2).

The agent does the actual web searching + judgement; this module keeps the
mechanical parts consistent and repeatable:
  * build_queries()   - the exact search queries for a project's scope
  * dedup_key()       - normalized identity so the same vendor is never
                        researched or added twice
  * classify_fit()    - turn hard-gates + 6 dimension scores into
                        strong / partial / potential / no-fit, identically each time
  * existing vendor re-check helpers (SOP 2a "check known vendors first")

CLI:
  python3 tools/sourcing.py queries P-06
  python3 tools/sourcing.py existing P-06          # known vendors to re-check
  python3 tools/sourcing.py dedup-key --name "Segmed, Inc." --website segmed.ai
  python3 tools/sourcing.py log-nofit V-003 P-06
  echo '{"gates":{...},"dims":{...}}' | python3 tools/sourcing.py classify
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse

import config
import domains
import sheets

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|limited|corp|corp\.|corporation|co|co\.|"
    r"gmbh|ag|sa|s\.a\.|kk|k\.k\.|plc|bv|b\.v\.|pte)\b",
    re.IGNORECASE,
)


# ------------------------------ normalization ----------------------------- #
def normalize_domain(website: str) -> str:
    if not website:
        return ""
    w = website.strip().lower()
    if "//" not in w:
        w = "//" + w
    netloc = urlparse(w).netloc or ""
    netloc = netloc.split("@")[-1].split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def normalize_name(name: str) -> str:
    n = (name or "").lower()
    n = _LEGAL_SUFFIXES.sub(" ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def dedup_key(name: str = "", website: str = "") -> str:
    """Primary key = root domain; fall back to normalized name."""
    domain = normalize_domain(website)
    if domain:
        return f"domain:{domain}"
    nm = normalize_name(name)
    return f"name:{nm}" if nm else ""


# ------------------------------- fit scoring ------------------------------- #
def classify_fit(gates: dict, dims: dict, domain: str | None = None) -> dict:
    """Score a vendor using the active domain's rubric.

    gates: {gate: true|false|null}   null = unknown (never a confirmed No).
    dims:  {dimension: 0|1|2}         (dimensions are domain-specific, max 12).
    """
    fit_cfg = domains.get_profile(domain or config.get_domain())["fit"]
    for g in fit_cfg["gates"]:
        if gates.get(g) is False:
            return {"fit": "no-fit", "score": 0,
                    "reason": f"hard gate '{g}' failed (confirmed No)"}
    score = sum(int(dims.get(d, 0)) for d in fit_cfg["dimensions"])
    gate_ok = gates.get(fit_cfg["strong_gate"]) is True
    core_met = all(int(dims.get(d, 0)) == 2 for d in fit_cfg["strong_core_dims"])
    if score >= fit_cfg["min_strong"] and gate_ok and core_met:
        fit = "strong"
    elif score >= fit_cfg["min_partial"]:
        fit = "partial"
    elif score >= fit_cfg["min_potential"]:
        fit = "potential"
    else:
        fit = "no-fit"
    return {"fit": fit, "score": score,
            "reason": f"score {score}/12; {fit_cfg['strong_gate']}={gates.get(fit_cfg['strong_gate'])}; "
                      f"core dims {fit_cfg['strong_core_dims']} met={core_met}"}


# ------------------------------ query building ----------------------------- #
def build_queries(project: dict, domain: str | None = None) -> list[dict]:
    """Return the domain's search-query archetypes filled in from the project scope."""
    return domains.get_profile(domain or config.get_domain())["build_queries"](project)


# --------------------------- existing-vendor recheck ----------------------- #
# Columns that are per-engagement (project-specific), so they are NOT carried
# over as vendor-level attributes when collapsing rows. Everything else
# (vendor name, website, and all domain-specific capability columns) is.
_ENGAGEMENT_COLS = {
    "Engagement ID", "Project ID", "Fit", "Fit rationale", "Status", "Last contact date",
    "Follow-up count", "Follow-up suggested after", "Thread ID", "Draft/email link",
    "Meeting notes", "Source of finding", "Date identified", "Re-check log", "Last updated",
}


def distinct_vendors() -> dict:
    """Collapse Vendor Tracker rows to one entry per Vendor ID (domain-agnostic).

    Returns {vendor_id: {vendor-level attrs.., projects:[...], engagement_ids:[...],
    nofit_projects:[...], first_engagement: E-xxx}}.
    """
    out: dict = {}
    for row in sheets.get_records(sheets.VENDORS):
        vid = str(row.get("Vendor ID", "")).strip()
        if not vid:
            continue
        entry = out.setdefault(vid, {"Vendor ID": vid, "projects": [], "engagement_ids": [],
                                     "nofit_projects": []})
        for attr, val in row.items():
            if attr in _ENGAGEMENT_COLS or attr == "Vendor ID":
                continue
            if not entry.get(attr) and val:
                entry[attr] = val
        # Project ID may be a comma-list (one-row-per-vendor domains like medical).
        for pid in [x.strip() for x in str(row.get("Project ID", "")).split(",") if x.strip()]:
            if pid not in entry["projects"]:
                entry["projects"].append(pid)
        eng = str(row.get("Engagement ID", "")).strip()
        if eng:
            entry["engagement_ids"].append(eng)
            entry.setdefault("first_engagement", eng)
        for tok in str(row.get("Re-check log", "")).split(";"):
            tok = tok.strip()
            if tok.endswith(":no-fit"):
                np = tok.split(":")[0].strip()
                if np and np not in entry["nofit_projects"]:
                    entry["nofit_projects"].append(np)
    return out


def existing_candidates_for(project_id: str) -> list[dict]:
    """Known vendors worth re-checking against a new project: not already linked
    to it, and not previously marked no-fit for it."""
    return [v for v in distinct_vendors().values()
            if project_id not in v["projects"] and project_id not in v["nofit_projects"]]


def log_nofit(vendor_id: str, project_id: str) -> dict:
    vendors = distinct_vendors()
    entry = vendors.get(vendor_id)
    if not entry or not entry.get("first_engagement"):
        raise SystemExit(f"error: no engagement rows for vendor {vendor_id!r}")
    # Append to the vendor's first engagement row's Re-check log (vendor-scoped in practice).
    eng = entry["first_engagement"]
    current = ""
    for row in sheets.get_records(sheets.VENDORS):
        if str(row.get("Engagement ID", "")).strip() == eng:
            current = str(row.get("Re-check log", "")).strip()
            break
    token = f"{project_id}:no-fit"
    if token not in current:
        current = f"{current}; {token}".strip("; ").strip()
    sheets.update_fields(sheets.VENDORS, eng, {"Re-check log": current})
    return {"vendor_id": vendor_id, "engagement": eng, "re_check_log": current}


# ----------------------------------- CLI ---------------------------------- #
def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "queries":
        proj = sheets.get_project(rest[0])
        if not proj:
            raise SystemExit(f"error: no project {rest[0]!r}")
        print(json.dumps(build_queries(proj), indent=2))
    elif cmd == "existing":
        print(json.dumps(existing_candidates_for(rest[0]), indent=2))
    elif cmd == "dedup-key":
        name = website = ""
        i = 0
        while i < len(rest):
            if rest[i] == "--name":
                name = rest[i + 1]; i += 2
            elif rest[i] == "--website":
                website = rest[i + 1]; i += 2
            else:
                i += 1
        print(dedup_key(name, website))
    elif cmd == "classify":
        payload = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(classify_fit(payload.get("gates", {}), payload.get("dims", {})), indent=2))
    elif cmd == "log-nofit":
        print(json.dumps(log_nofit(rest[0], rest[1]), indent=2))
    else:
        raise SystemExit(f"error: unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

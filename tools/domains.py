"""Domain profiles — everything that differs by sourcing domain lives here.

The plumbing (sheet I/O, Gmail, follow-ups, dedup, status flow, fit *levels*) is
domain-agnostic. What changes per domain is only: the sheet columns, the fit
rubric (dimensions + hard gates + "strong" criteria), the web-search query
archetypes, the outreach gating questions, and the dropdown validations.

Profiles shipped:
  medical             - EHR / imaging / WSI / outcomes datasets (the original build)
  robotics_egocentric - human egocentric video for robot learning

Each tracker (Google Sheet) is tied to one domain. sheets.py reads/writes rows
header-agnostically, so adding a domain is just adding a profile here + creating
a sheet with that profile's headers (scripts/setup_sheet.py --domain ...).

CLI:
  python3 tools/domains.py show [--domain robotics_egocentric]
  python3 tools/domains.py headers projects|vendor [--domain ...]
  python3 tools/domains.py gating-questions [--domain ...]
"""
from __future__ import annotations

import json
import re
import sys

# Tab names + enums shared across domains.
PROJECTS_TAB = "Projects"
VENDORS_TAB = "Vendor Tracker"
CONFIG_TAB = "Config"
CONFIG_HEADERS = ["Key", "Value"]

FIT_VALUES = ["strong", "partial", "potential"]
STATUS_VALUES = ["Identified", "Outreached", "In Contact", "Collaborating", "Rejected"]
PROJECT_STATUS_VALUES = ["scoping", "sourcing", "outreach", "meetings", "closed"]
YES_NO_UNKNOWN = ["Yes", "No", "Unknown"]
PRIORITY_VALUES = ["High", "Medium", "Low"]

# Status cell colors (conditional formatting). Identified stays uncolored (white).
STATUS_COLORS = {
    "Collaborating": {"red": 0.72, "green": 0.88, "blue": 0.72},   # green
    "In Contact": {"red": 0.72, "green": 0.83, "blue": 0.96},      # blue
    "Outreached": {"red": 1.0, "green": 0.90, "blue": 0.55},       # yellow
    "Rejected": {"red": 0.85, "green": 0.85, "blue": 0.85},        # grey
}

# Tracker-name -> default domain (used by setup_sheet when --domain is omitted).
DEFAULT_DOMAIN_FOR_TRACKER = {"medical": "medical", "robotics": "robotics_egocentric"}


def _q(archetype: str, query: str) -> dict:
    return {"archetype": archetype, "query": re.sub(r"\s+", " ", query).strip()}


# ============================== MEDICAL =================================== #
MEDICAL_PROJECT_HEADERS = [
    "Project ID", "Project name", "Raw requirement", "Date created", "Requester",
    "Data type(s)", "Modality detail", "Disease/cohort", "Patient criteria",
    "Cohort size req", "Time frame / follow-up", "Linkage req", "Data source region",
    "Compliance req", "Priority", "Project status", "Notes", "Last updated",
]
# One row per VENDOR (keyed on Vendor ID). A vendor serving multiple projects
# lists them all in Project ID (e.g. "P-04, P-06"). "Follow-up Action" is
# maintained by the daily flag job (followups.py flag), not by humans.
MEDICAL_VENDOR_HEADERS = [
    "Engagement ID", "Vendor ID", "Vendor name", "Website", "Project ID",
    "Data type/modalities offered", "Disease/therapeutic focus", "Data origin",
    "Scale", "Fit", "Fit rationale", "De-identified?", "Commercial rights-clean?",
    "Pricing", "Date identified", "Contact name", "Contact email",
    "Contact role", "Message-ID", "Status", "Last contact date", "Follow-up count",
    "Follow-up Action", "Draft/email link", "Meeting notes",
]


def _medical_queries(project: dict) -> list[dict]:
    data_type = project.get("Data type(s)", "")
    modality = project.get("Modality detail", "")
    disease = project.get("Disease/cohort", "")
    region = (project.get("Data source region", "") or "").lower()
    cap = modality or data_type
    q = [
        _q("capability-direct", f"{cap} {disease} de-identified real-world data vendor commercial licensing"),
        _q("linkage-specific", f"{disease} {data_type} linked EHR outcomes real-world data provider"),
    ]
    if "eu" in region or "europ" in region:
        q.append(_q("compliance-eu", f"{disease} {cap} GDPR pseudonymized EHDS medical data provider Europe"))
    if "asia" in region:
        q.append(_q("compliance-asia", f"{disease} {cap} anonymized medical data provider Asia"))
    if any(k in region for k in ("us", "usa", "united states")) or not region or "global" in region:
        q.append(_q("compliance-us", f"{disease} {cap} HIPAA de-identified dataset commercial AI license"))
    if "global" in region or not region:
        q.append(_q("compliance-eu", f"{disease} {cap} GDPR pseudonymized medical data provider Europe"))
    q.append(_q("marketplace", f"Datarade {disease} {data_type} dataset provider"))
    q.append(_q("marketplace", f"{disease} {data_type} dataset AWS Data Exchange OR Snowflake Marketplace"))
    q.append(_q("broker-fallback", f"{disease} {data_type} data broker Datavant HealthVerity Protege license"))
    q.append(_q("registry-fallback", f"{disease} {modality} public dataset TCIA EUCAIM biobank"))
    q.append(_q("competitor-expansion", '"<VENDOR>" competitors alternatives real-world medical data'))
    return q


MEDICAL_GATING_QUESTIONS = [
    "Do you hold rights permitting commercial AI model training on derivatives, and is that transferable?",
    "What de-identification method do you use (HIPAA Safe Harbor / Expert Determination / GDPR)?",
    "Can you meet the required cohort size, and confirm demographic/stage filters?",
    "What follow-up duration and outcomes are captured (recurrence/progression/death)?",
    "Is the required cross-modal linkage (imaging <-> EHR <-> outcomes) available at the record level?",
    "What is the indicative pricing / licensing model?",
]

MEDICAL = {
    "domain": "medical",
    "label": "Medical datasets (EHR / imaging / WSI / outcomes)",
    "project_headers": MEDICAL_PROJECT_HEADERS,
    "vendor_headers": MEDICAL_VENDOR_HEADERS,
    "config_headers": CONFIG_HEADERS,
    "vendor_key": "Vendor ID",          # one row per vendor
    "multi_project": True,               # Project ID holds a comma-list of projects
    "followup_action_col": "Follow-up Action",
    "fit": {
        "dimensions": ["modality", "disease", "linkage", "cohort_size", "followup", "demographics"],
        "gates": ["data_type", "commercial", "region", "real"],
        "strong_gate": "commercial", "strong_core_dims": ["modality", "disease"],
        "min_strong": 9, "min_partial": 5, "min_potential": 2,
    },
    "validations": {
        PROJECTS_TAB: {"Project status": PROJECT_STATUS_VALUES, "Priority": PRIORITY_VALUES},
        VENDORS_TAB: {"Fit": FIT_VALUES, "Status": STATUS_VALUES,
                      "De-identified?": YES_NO_UNKNOWN, "Commercial rights-clean?": YES_NO_UNKNOWN},
    },
    "column_notes": {
        "Vendor ID": "Stable row key — don't change.",
        "Engagement ID": "Row id — don't change.",
        "Project ID": "Which project(s) this vendor is sourced for; comma-list if several (e.g. P-04, P-06).",
        "Fit": "strong / partial / potential — match to the project (color-coded).",
        "Fit rationale": "Why this fit level; per-project notes.",
        "Data origin": "Where the data originates, e.g. USA, USA + Europe, Global.",
        "De-identified?": "Yes / No / Unknown.",
        "Commercial rights-clean?": "Rights permit commercial AI training: Yes / No / Unknown.",
        "Pricing": "Leave blank if unknown.",
        "Status": "Pipeline stage: Identified -> Outreached -> In Contact -> Collaborating / Rejected.",
        "Last contact date": "Date of the last outreach/follow-up (auto).",
        "Follow-up count": "Number of follow-up emails sent (auto).",
        "Follow-up Action": "Auto-managed daily: shows 'Needs Follow-up' (red) when due. Don't edit.",
        "Draft/email link": "Gmail draft id, set by /draft-outreach.",
    },
    "build_queries": _medical_queries,
    "gating_questions": MEDICAL_GATING_QUESTIONS,
}


# ========================= ROBOTICS EGOCENTRIC ============================ #
ROBO_PROJECT_HEADERS = [
    "Project ID", "Project name", "Raw requirement", "Date created", "Requester",
    "Data type", "Modalities", "Sensor specs", "Volume req", "Task/activity diversity",
    "Annotation req", "Embodiment", "Format/schema", "License-class req",
    "Privacy/consent req", "Data origin region", "Priority", "Project status",
    "Notes", "Last updated",
]
# One row per VENDOR (keyed on Vendor ID); Project ID holds a comma-list (P-01, P-02).
ROBO_VENDOR_HEADERS = [
    "Engagement ID", "Vendor ID", "Vendor name", "Website", "Project ID", "Supply type",
    "Modalities offered", "Sensor specs", "Volume", "Embodiment",
    "Annotation", "Format/schema", "Fit", "Fit rationale", "License class",
    "Commercial+AI rights", "Consent/privacy", "Origin", "Pricing",
    "Date identified", "Contact name", "Contact email", "Contact role", "Message-ID", "Status",
    "Last contact date", "Follow-up count", "Follow-up Action",
    "Draft/email link", "Meeting notes", "Last updated",
]
SUPPLY_TYPE_VALUES = ["bespoke-collection", "dataset-license",
                      "bespoke-collection + dataset-license", "annotation-only", "unknown"]
LICENSE_CLASS_VALUES = ["commercial-OK", "research-only", "gated-approval", "unknown"]


def _robotics_queries(project: dict) -> list[dict]:
    activity = (project.get("Task/activity diversity") or project.get("Data type")
                or "everyday manipulation activities")
    region = project.get("Data origin region", "")
    return [
        _q("collection-for-hire", '"egocentric video data collection" service commercial license robotics for hire'),
        _q("dataset-license", f"{activity} first-person head-mounted egocentric video dataset commercial use license"),
        _q("provider-startup", f"physical AI OR embodied AI egocentric training data provider startup 2026 {region}"),
        _q("dataset-audit", '"<DATASET>" license commercial use egocentric (audit each dataset: commercial vs research-only)'),
        _q("privacy", "egocentric video privacy consent bystander de-identification GDPR biometric data vendor"),
        _q("competitor-expansion", '"<VENDOR>" competitors alternatives egocentric video robot learning data collection'),
    ]


ROBO_GATING_QUESTIONS = [
    "Do you grant a commercial + AI-training license, including the right to train on and ship derivative model weights, and is it transferable?",
    "What is your consent + privacy chain — participant consent, bystander face/biometric de-identification (e.g. EgoBlur), and GDPR/BIPA posture?",
    "What delivery format/schema do you provide (VRS / MP4+metadata / LeRobot / RLDS), and are synchronization and calibration intact?",
    "Provenance: is any of the data repackaged from research-only datasets (e.g. Ego4D DUA limits, Epic-Kitchens non-commercial)?",
    "In which country is the data collected/originated? (We exclude China-origin data for this project.)",
    "Volume: how many hours, participants, and distinct scenes, and what is your sustainable collection throughput and $/hour?",
    "Modalities and sensor specs (RGB resolution/fps/FOV; gaze / IMU / audio / hand-object pose), and single ego vs ego+exo views?",
    "Annotation depth: language narrations, action/step labels, hand/object pose, success/failure labels?",
]

ROBOTICS_EGOCENTRIC = {
    "domain": "robotics_egocentric",
    "label": "Robotics — human egocentric video for robot learning",
    "project_headers": ROBO_PROJECT_HEADERS,
    "vendor_headers": ROBO_VENDOR_HEADERS,
    "config_headers": CONFIG_HEADERS,
    "vendor_key": "Vendor ID",           # one row per vendor
    "multi_project": True,                # Project ID holds a comma-list of projects
    "followup_action_col": "Follow-up Action",
    "fit": {
        "dimensions": ["modality_sensor", "embodiment_match", "volume_throughput",
                       "task_diversity", "annotation_depth", "quality_governance"],
        "gates": ["commercial_license", "consent_privacy", "trainable_format",
                  "clean_provenance", "origin_ok"],
        "strong_gate": "commercial_license", "strong_core_dims": ["modality_sensor"],
        "min_strong": 9, "min_partial": 5, "min_potential": 2,
    },
    "validations": {
        PROJECTS_TAB: {"Project status": PROJECT_STATUS_VALUES, "Priority": PRIORITY_VALUES},
        VENDORS_TAB: {"Fit": FIT_VALUES, "Status": STATUS_VALUES,
                      "Supply type": SUPPLY_TYPE_VALUES, "License class": LICENSE_CLASS_VALUES,
                      "Commercial+AI rights": YES_NO_UNKNOWN, "Consent/privacy": YES_NO_UNKNOWN},
    },
    "column_notes": {
        "Vendor ID": "Stable row key — don't change.",
        "Engagement ID": "Row id — don't change.",
        "Project ID": "Which project(s) this vendor is sourced for; comma-list if several (e.g. P-01, P-02).",
        "Supply type": "bespoke-collection / dataset-license / annotation-only.",
        "Fit": "strong / partial / potential — match to the project (color-coded).",
        "Fit rationale": "Why this fit level; per-project notes.",
        "License class": "commercial-OK / research-only / gated-approval / unknown.",
        "Commercial+AI rights": "Rights permit commercial AI training: Yes / No / Unknown.",
        "Consent/privacy": "Participant + bystander consent chain in place: Yes / No / Unknown.",
        "Origin": "Where the data is collected, e.g. USA, Global. China is excluded.",
        "Pricing": "Leave blank if unknown.",
        "Status": "Pipeline stage: Identified -> Outreached -> In Contact -> Collaborating / Rejected.",
        "Last contact date": "Date of the last outreach/follow-up (auto).",
        "Follow-up count": "Number of follow-up emails sent (auto).",
        "Follow-up Action": "Auto-managed daily: shows 'Needs Follow-up' (red) when due. Don't edit.",
        "Draft/email link": "Gmail draft id, set by /draft-outreach.",
    },
    "build_queries": _robotics_queries,
    "gating_questions": ROBO_GATING_QUESTIONS,
}


PROFILES = {"medical": MEDICAL, "robotics_egocentric": ROBOTICS_EGOCENTRIC}


def get_profile(name: str) -> dict:
    if name not in PROFILES:
        raise SystemExit(f"error: unknown domain {name!r}. Known: {', '.join(PROFILES)}")
    return PROFILES[name]


# ----------------------------------- CLI ---------------------------------- #
def _resolve_domain(rest: list[str]) -> str:
    if "--domain" in rest:
        return rest[rest.index("--domain") + 1]
    import config  # lazy: only needed to resolve the active tracker's domain
    return config.get_domain()


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    prof = get_profile(_resolve_domain(rest))
    if cmd == "show":
        print(json.dumps({k: v for k, v in prof.items() if not callable(v)}, indent=2))
    elif cmd == "headers":
        which = rest[0] if rest else "projects"
        print(json.dumps(prof["project_headers"] if which == "projects" else prof["vendor_headers"], indent=2))
    elif cmd == "gating-questions":
        print(json.dumps(prof["gating_questions"], indent=2))
    else:
        raise SystemExit(f"error: unknown command {cmd!r}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

---
name: add-project
description: Scope a new data-sourcing requirement (SOP step 1) into the tracker. Works for any domain/tracker — medical (EHR/imaging/WSI/outcomes) or robotics_egocentric (human egocentric video). Use when the user gives a new requirement, e.g. "P-06: breast-cancer H&E WSI…" or "egocentric kitchen video, commercial-clean, ≥500 hrs".
---

# Add a sourcing project (SOP 1: Scope & requirement)

Turn a free-text requirement into one row in the correct tracker's **Projects** tab. All commands run from the project root.

## 0. Pick the tracker/domain
Trackers are separate Google Sheets, one per domain. See them with `python3 tools/config.py trackers`.
- Medical requirement → tracker `medical` (default; no prefix needed).
- Robotics egocentric-video requirement → tracker `robotics`. **Prefix every command with `ABAKA_TRACKER=robotics`.**
If the needed tracker doesn't exist yet, tell the user to create it: `python3 scripts/setup_sheet.py --create --tracker robotics "Abaka Robotics Egocentric Sourcing"` (or run **/setup**). Use `<T>` below for the chosen tracker prefix.

## 1. Preflight
`ABAKA_TRACKER=<T> python3 tools/config.py trackers` — confirm the tracker has a `sheet_id`. If not, create it first (step 0).

## 2. Get the requirement
Use the user's text verbatim (it becomes `Raw requirement`); ask if none given.

## 3. Fetch the domain's columns and parse into them
Get the exact Projects columns for this domain — don't guess:
```bash
ABAKA_TRACKER=<T> python3 tools/domains.py headers projects
```
Map the requirement into those columns (leave blank if truly absent — never invent). Show the parse to the user to confirm/correct.
- **medical** columns: Data type(s) · Modality detail · Disease/cohort · Patient criteria · Cohort size req · Time frame / follow-up · Linkage req · Data source region · Compliance req.
- **robotics_egocentric** columns: Data type (egocentric human video) · Modalities (RGB/gaze/IMU/audio/hand-pose) · Sensor specs (res/fps/FOV, #views) · Volume req (hours/participants/scenes) · Task/activity diversity · Annotation req · Embodiment (human first-person) · Format/schema (VRS/MP4/LeRobot) · License-class req · Privacy/consent req · Data origin region.
- **Origin rule:** if the user excludes a region (e.g. "no China"), record it in `Data origin region` **and** `Notes` so sourcing enforces it.

## 4. Assign the Project ID
Use the user's ID if given, else `ABAKA_TRACKER=<T> python3 tools/sheets.py next-id project`.

## 5. Write the row
Pipe JSON keyed to that domain's columns (upsert is keyed on `Project ID`):
```bash
cat <<'JSON' | ABAKA_TRACKER=<T> python3 tools/sheets.py upsert-project
{ "Project ID":"...", "Project name":"...", "Raw requirement":"<verbatim>",
  "Date created":"<today>", "Requester":"<user>", ...domain columns..., "Priority":"High",
  "Project status":"scoping", "Notes":"origin/compliance constraints here" }
JSON
```

## 6. Confirm
Print the Project ID, the parsed fields, the tracker's sheet URL (`python3 tools/config.py trackers`), and tell the user to run **/source-vendors `<Project ID>`** (with the same tracker).

---
name: source-vendors
description: Find data vendors that fit a sourcing project (SOP step 2), for any domain/tracker. Re-checks existing vendors first, then web-searches new ones, scores fit (strong/partial/potential), and writes Vendor Tracker rows. Handles medical and robotics_egocentric. Use for "source vendors for P-06" or "source the robotics project".
---

# Source vendors (SOP 2)

Populate the **Vendor Tracker** for a project. **Re-check known vendors first, then discover new ones.** Commands run from the project root. Prefix commands with `ABAKA_TRACKER=<T>` (default `medical`, use `robotics` for egocentric video). Take `<P-ID>` from the user.

## 1. Load scope + domain rubric + ROW MODEL
- `ABAKA_TRACKER=<T> python3 tools/sheets.py get-project <P-ID>` — the scope to match.
- `ABAKA_TRACKER=<T> python3 tools/domains.py show` — gives the fit **gates + dimensions** (use these exact keys when scoring) and the vendor columns.
- **Row model (both domains): one row per vendor**, keyed on **Vendor ID**. A vendor serving several projects lists them all in `Project ID` (e.g. `"P-01, P-02"`) — never multiple rows for one vendor.
- Note the project's origin constraint (e.g. exclude China) — enforce it as a hard gate.

## 2. Re-check EXISTING vendors first (before any web search)
`ABAKA_TRACKER=<T> python3 tools/sourcing.py existing <P-ID>` → known vendors not yet linked to this project. Re-score each against this scope from stored attributes (one targeted WebFetch only to fill a gap).
- **Fit:** append this project to the vendor's `Project ID` cell (read `"P-01"` → write `"P-01, <P-ID>"`) and refresh `Fit`/`Fit rationale` if this project changes them, keyed on Vendor ID:
  ```bash
  echo '{"Project ID":"P-01, <P-ID>","Fit":"strong"}' | ABAKA_TRACKER=<T> python3 tools/sheets.py update-vendor <Vendor ID>
  ```
- **No fit:** skip (no-fits are not persisted).

## 3. Discover NEW vendors
`ABAKA_TRACKER=<T> python3 tools/sourcing.py queries <P-ID>` → the domain's query archetypes. Run each with **WebSearch** (~10 results each); fill `<VENDOR>`/`<DATASET>` placeholders from live hits. Compile 15–25 candidates; dedup with `python3 tools/sourcing.py dedup-key --name "<n>" --website "<w>"` (reuse an existing Vendor ID on a match).

**Robotics specifics:** tag **Supply type** (dataset-license vs bespoke-collection); **audit each dataset's license** (`License class` = commercial-OK / research-only / gated-approval); record **Origin** and exclude China-origin; privacy = bystander face/biometric consent.

## 4. Deep-dive the shortlist (~8–12)
For the strongest candidates, WebFetch homepage + a data/solutions or compliance/legal page. Extract the domain's columns (from step 1) + a contact email/URL if discoverable (don't fabricate).

## 5. Score fit and write rows
Judge the domain's hard gates + dimensions, then classify consistently (use the gate/dim KEYS from `domains show`):
```bash
echo '{"gates":{...},"dims":{...}}' | ABAKA_TRACKER=<T> python3 tools/sourcing.py classify
```
A confirmed `false` gate ⇒ excluded (skip). `null` = Unknown ⇒ becomes an outreach gating question. For each fitting NEW vendor, allocate a Vendor ID (the PK) + an Engagement ID immediately before writing, then upsert into the domain's columns:
```bash
VID=$(ABAKA_TRACKER=<T> python3 tools/sheets.py next-id vendor)
EID=$(ABAKA_TRACKER=<T> python3 tools/sheets.py next-id engagement)
cat <<JSON | ABAKA_TRACKER=<T> python3 tools/sheets.py upsert-vendor
{ "Vendor ID":"$VID", "Engagement ID":"$EID", "Vendor name":"...", "Website":"...",
  "Project ID":"<P-ID>", ...domain columns..., "Fit":"partial",
  "Fit rationale":"dims met vs gaps", "Date identified":"<today>", "Status":"Identified" }
JSON
```
Set `Project ID` to this project (or a comma-list if the vendor already fits several). For **robotics** also fill `Supply type` / `License class` / a concise `Origin` (e.g. `USA`, `USA + Europe`; mark China-origin excluded). Leave `Pricing` blank when unknown; reuse the same Vendor ID across a vendor's projects.

## 6. Finish
`echo '{"Project status":"sourcing"}' | ABAKA_TRACKER=<T> python3 tools/sheets.py update-project <P-ID>`. Summarize: counts by fit; reused-vs-new; (robotics) Supply type + License class + China exclusions. Recommend **/draft-outreach `<P-ID>`**.

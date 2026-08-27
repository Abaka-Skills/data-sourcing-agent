---
name: source-vendors
description: Find data vendors that fit a sourcing project (SOP step 2), for any domain/tracker. Re-checks existing vendors first, then web-searches new ones, scores fit (strong/partial/potential), and writes Vendor Tracker rows. Handles medical and robotics_egocentric. Use for "source vendors for P-06" or "source the robotics project".
---

# Source vendors (SOP 2)

Populate the **Vendor Tracker** for a project. **Re-check known vendors first, then discover new ones.** Commands run from the project root. Prefix commands with `ABAKA_TRACKER=<T>` (default `medical`, use `robotics` for egocentric video). Take `<P-ID>` from the user.

## STANDING RULES (always apply)
1. **No very large / generic giants (大厂).** Prefer smaller & mid-size specialists. By default EXCLUDE the big generalist data-services giants unless the user explicitly asks for them — e.g. Appen, TELUS International, Sama, iMerit, TaskUs, Innodata, CloudFactory, Scale AI, Defined.ai, and other household-name / public data-services companies. Flag any borderline "big" candidate rather than silently including it.
2. **Check who's already been contacted first — across ALL projects, not just this one.** Before sourcing, scan every project table (`python3 -c "import sys; sys.path.insert(0,'tools'); import lark; print(lark.project_tables())"`, then read each) and build the set of existing vendor names + their Status. DEDUP against it: never re-source or re-email a vendor already in the pipeline (Identified / Outreached / In Contact / Collaborating / Rejected). Tell the user which relevant vendors are already in-flight and in which project. (`larksource.py add-vendor` dedups within one project table; this rule extends it to cross-project.)

## Backend: Google Sheet vs Lark Base
Two backends exist. **Pick by where the project lives:**
- **Lark project** (id looks like `P01`…`P09` / `P03-Robo-AppliedIntuition`; the live system): use **`tools/larksource.py`** — vendors are written straight into that project's own table (P0X, with its Vendor/Tracking/Samples/Documents views). No Vendor ID / Engagement ID / Project ID column — the table *is* the project. Commands:
  - `python3 tools/larksource.py project <P>` — the intake scope to match (replaces `sheets.py get-project`).
  - `python3 tools/larksource.py vendors <P>` — existing rows for re-check/dedup (replaces step 2's `sourcing.py existing`).
  - `echo '{...}' | python3 tools/larksource.py add-vendor <P>` — dedup (by name/website domain) + upsert one vendor (replaces the `upsert-vendor` block in step 5). Defaults Status=Identified, Active/Inactive=Active.
  - Still use `tools/domains.py show` for the rubric and `tools/sourcing.py classify` for scoring (both backend-agnostic). Skip the Vendor ID/Engagement ID `next-id` calls entirely.
  - Field keys map automatically (`Vendor name`→Vendor, `Fit rationale`→Notes, `Environments`→Environments Supported, Priority High/Med/Low→S/A/B, etc.).
- **Google Sheet project** (id looks like `P-06`): use `tools/sheets.py` as written below.

The scoring, dedup, gating-question and origin-exclusion logic is identical for both — only the read/write tool changes.

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

"""
Firmware & Docs engine — pure Python, no UI dependency.

Asks Claude (with web search) whether each product's has_firmware,
Firmware Added and documents added flags match what the manufacturer
actually publishes. Follows the engine contract described in
tools/product_name_qa/core.py.
"""

from openpyxl import Workbook

from tools.common import claude, excel
from tools.common.data import ROW_COL, SOURCE_COL, Columns, clean, find_col

NAME = "Firmware & Docs"

SYSTEM_PROMPT = """
You are a firmware and documentation verification agent for a professional
broadcast equipment database. For each product you must run three checks.

FIELD DEFINITIONS:
- has_firmware:      TRUE = this product has firmware available from the manufacturer
                     FALSE = this product has no firmware (hardware-only, no software updates)
- Firmware Added:    TRUE = the firmware has been added to our system / database
                     FALSE = firmware has NOT been added yet
- documents added:   TRUE = official documents have been added to our system
                     FALSE = documents have NOT been added yet
A field missing from the input means the database doesn't track it — set its flag to "N/A".

WHAT COUNTS AS FIRMWARE (search for these):
Firmware binary files (.bin, .hex, .img), firmware update packages,
software updates, firmware release notes, official downloads from the
manufacturer's support or downloads page.
Do NOT count third-party drivers, operating system software, or DAWs.

WHAT COUNTS AS DOCUMENTS (search for these):
User manuals, installation guides, quick start guides, spec sheets,
datasheets, technical bulletins, programming guides, admin guides.
Must be official manufacturer documents. Do NOT count forum posts,
reviews, or user-generated content.

─────────────────────────────────────────────────────────
CHECK 1 — has_firmware
─────────────────────────────────────────────────────────
Search the manufacturer's official support/downloads page for firmware
files for this specific model.

If has_firmware = FALSE:
  Firmware found online   → flag_has_firmware = "Flag"
    (product DOES have firmware but is marked FALSE — needs correction)
  Not found               → flag_has_firmware = "Pass"
    (correctly marked FALSE — no firmware exists)

If has_firmware = TRUE:
  Firmware found online   → flag_has_firmware = "Pass"
    (correctly marked TRUE)
  Not found               → flag_has_firmware = "Warning"
    (marked TRUE but cannot verify — may be correct or may need review)

firmware_found_online: true OR false (based on your search)
firmware_source: URL of firmware download page if found, else empty string

─────────────────────────────────────────────────────────
CHECK 2 — Firmware Added (cross-check)
─────────────────────────────────────────────────────────
ONLY run this check if has_firmware = TRUE (recorded in DB).
If has_firmware = FALSE, set flag_firmware_added = "N/A" and skip.

If has_firmware = TRUE AND Firmware Added = FALSE:
  → flag_firmware_added = "Flag"
    (firmware exists but has not been added to the system — action needed)

If has_firmware = TRUE AND Firmware Added = TRUE:
  → flag_firmware_added = "Pass"

CONTRADICTION CHECK:
If has_firmware = FALSE AND Firmware Added = TRUE:
  → flag_firmware_added = "Flag (Contradiction)"
    (how can firmware be added if the product has no firmware?)

─────────────────────────────────────────────────────────
CHECK 3 — documents added
─────────────────────────────────────────────────────────
If documents added = TRUE:
  → flag_documents = "Pass" (already added, skip search)

If documents added = FALSE:
  Search the manufacturer's website and major sources for official
  documents for this specific model.
  Documents found online  → flag_documents = "Flag"
    (documents available but not added — action needed)
    Record the document source URL.
  Not found               → flag_documents = "Pass"
    (nothing available to add — correctly marked FALSE)

documents_found_online: true OR false
documents_source: URL of document (manual/datasheet) if found, else empty string

─────────────────────────────────────────────────────────
OVERALL STATUS (assign one):
- Pass          → all flags are correctly set, no action needed
- Flag          → one or more flags need attention or correction
- Warning       → uncertain result on at least one check — human review recommended
- Unknown       → could not determine from available sources

─────────────────────────────────────────────────────────
SEARCH STRATEGY:
1. If product_url provided → check it first for downloads/support links
2. Search manufacturer's support, downloads, or resources page for this model
   (search by product_name if model is blank)
3. Check major authorised sources (B&H, Markertek) as secondary for documents

─────────────────────────────────────────────────────────
OUTPUT FORMAT — CRITICAL:
Return ONLY a raw JSON array. Start with [ and end with ].
No markdown, no backticks, no text before or after.
One object per product in the input, each with EXACTLY these keys:
[
  {
    "_id": "exact value from input",
    "firmware_found_online": true or false,
    "firmware_source": "URL if found else empty string",
    "flag_has_firmware": "Pass OR Flag OR Warning OR N/A",
    "flag_firmware_added": "Pass OR Flag OR Flag (Contradiction) OR N/A",
    "documents_found_online": true or false,
    "documents_source": "URL if found else empty string",
    "flag_documents": "Pass OR Flag OR N/A",
    "overall_status": "Pass OR Flag OR Warning OR Unknown",
    "note": "1-2 sentences summarising what was found and what action is needed"
  }
]
"""

# Input field name sent to the model → column name variants in the file.
FLAG_FIELDS = {
    "has_firmware": ("has_firmware", "has firmware"),
    "Firmware Added": ("Firmware Added", "firmware_added"),
    "documents added": ("documents added", "documents_added", "docs added"),
}

# Contradiction is worse than a plain flag, so it's checked first.
RULES = [("flag (contradiction)", excel.BAD)] + excel.DEFAULT_STATUS_RULES + [
    ("yes", excel.GOOD), ("no", excel.BAD)]


def _flag_cols(df) -> dict:
    return {field: col for field, names in FLAG_FIELDS.items()
            if (col := find_col(df, *names)) is not None}


def missing_columns(df) -> list:
    cols = Columns(df)
    missing = [name for name, col in (("mfr", cols.mfr), ("model", cols.model)) if col is None]
    if not _flag_cols(df):
        missing.append("has_firmware / Firmware Added / documents added (at least one)")
    return missing


def _result(status: str, note: str, **extra) -> dict:
    r = {"overall_status": status, "note": note}
    r.update(extra)
    return _normalise(r)


def _normalise(r: dict) -> dict:
    r["qa_status"] = str(r.get("overall_status", "")).strip() or claude.NEEDS_REVIEW
    r["qa_note"] = str(r.get("note", "")).strip()
    return r


def run(df, client, model, batch_size, batch_pause, log, progress=None, status=None) -> dict:
    cols = Columns(df)
    flag_cols = _flag_cols(df)
    results, records = {}, []
    for _, row in df.iterrows():
        rec = cols.product(row)
        if not rec["model"] and not rec["product_name"]:
            results[row[ROW_COL]] = _result("Skip", "No model number or product name — nothing to search for.")
            continue
        for field, col in flag_cols.items():
            rec[field] = clean(row.get(col))
        if cols.url and clean(row.get(cols.url)):
            rec["product_url"] = clean(row.get(cols.url))
        rec["_row"] = row[ROW_COL]
        records.append(rec)

    log(f"{len(records)} product(s) to check — columns: {', '.join(flag_cols)}", "dim")
    answered = claude.run_batches(
        client, model, records, SYSTEM_PROMPT, use_search=True,
        batch_size=batch_size, batch_pause=batch_pause, log=log,
        progress=progress, status=status, label=NAME,
        review_result=lambda rec, why: _result(claude.NEEDS_REVIEW, why),
    )
    for row, r in answered.items():
        results[row] = _normalise(r)
    return results


def _count(results: dict, key: str, value: str, contains: bool = False) -> int:
    value = value.lower()

    def hit(r):
        v = str(r.get(key, "")).lower()
        return value in v if contains else v == value

    return sum(1 for r in results.values() if hit(r))


def summarize(results: dict) -> list:
    return [
        ("Total products checked", len(results), None),
        ("", "", None),
        ("Overall results:", "", None),
        ("  Pass — all flags correct", _count(results, "qa_status", "pass"), excel.GOOD),
        ("  Flag — action needed", _count(results, "qa_status", "flag"), excel.WARN),
        ("  Warning — review recommended", _count(results, "qa_status", "warning"), excel.WARN),
        ("  Unknown — could not determine", _count(results, "qa_status", "unknown"), excel.INFO),
        ("  Skipped — nothing to search for", _count(results, "qa_status", "skip"), excel.INFO),
        ("  Needs human review (API couldn't process)",
         _count(results, "qa_status", claude.NEEDS_REVIEW), excel.REVIEW),
        ("", "", None),
        ("By flag:", "", None),
        ("  has_firmware needs correction", _count(results, "flag_has_firmware", "flag"), excel.WARN),
        ("  has_firmware unverifiable", _count(results, "flag_has_firmware", "warning"), excel.WARN),
        ("  Firmware not yet added to system",
         _count(results, "flag_firmware_added", "flag", contains=True), excel.WARN),
        ("  Documents not yet added to system", _count(results, "flag_documents", "flag"), excel.WARN),
        ("", "", None),
        ("Firmware found online (total)",
         sum(1 for r in results.values() if r.get("firmware_found_online") is True), None),
        ("Documents found online (total)",
         sum(1 for r in results.values() if r.get("documents_found_online") is True), None),
    ]


def _yes_no(v) -> str:
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    return ""


def save_excel(df, results: dict, path: str) -> None:
    cols = Columns(df)
    flag_cols = _flag_cols(df)
    columns = ["Source File", "Manufacturer", "Model", "Product Name",
               "has_firmware (DB)", "Firmware Added (DB)", "documents added (DB)",
               "Firmware Found Online", "Firmware Source",
               "has_firmware Flag", "Firmware Added Flag",
               "Documents Found Online", "Documents Source",
               "Documents Flag", "Overall Status", "Note"]
    rows = []
    for _, row in df.iterrows():
        r = results.get(row[ROW_COL])
        if r is None:
            continue
        p = cols.product(row)

        def db(field):
            return clean(row.get(flag_cols[field])) if field in flag_cols else ""

        rows.append({
            "Source File": row.get(SOURCE_COL, ""), "Manufacturer": p["mfr"], "Model": p["model"],
            "Product Name": p["product_name"],
            "has_firmware (DB)": db("has_firmware"), "Firmware Added (DB)": db("Firmware Added"),
            "documents added (DB)": db("documents added"),
            "Firmware Found Online": _yes_no(r.get("firmware_found_online")),
            "Firmware Source": r.get("firmware_source", ""),
            "has_firmware Flag": r.get("flag_has_firmware", ""),
            "Firmware Added Flag": r.get("flag_firmware_added", ""),
            "Documents Found Online": _yes_no(r.get("documents_found_online")),
            "Documents Source": r.get("documents_source", ""),
            "Documents Flag": r.get("flag_documents", ""),
            "Overall Status": r["qa_status"], "Note": r["qa_note"],
        })
    wb = Workbook()
    excel.write_sheet(wb, "Firmware & Docs", columns, rows,
                      widths=[14, 16, 22, 38, 14, 16, 16, 18, 44, 16, 18, 20, 44, 14, 14, 55],
                      status_cols=["Firmware Found Online", "has_firmware Flag", "Firmware Added Flag",
                                   "Documents Found Online", "Documents Flag", "Overall Status"],
                      rules=RULES, row_height=52)
    excel.write_summary(wb, "Summary", summarize(results), heading=f"{NAME} — Summary")
    wb.save(path)

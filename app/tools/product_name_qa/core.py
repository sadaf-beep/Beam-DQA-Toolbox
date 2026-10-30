"""
Product Name QA engine — pure Python, no UI dependency.

Asks Claude (training knowledge only, no web search) whether each record's
manufacturer, model and product name are consistent and well-formed.

Engine contract shared by every QA tool (the pipeline relies on it):
  missing_columns(df) -> list of required columns the file lacks
  run(df, ...)        -> {row: result} with a result for EVERY row of df;
                         each result has "qa_status" and "qa_note"
  summarize(results)  -> [(label, count, colors)] for the Summary sheet
  save_excel(df, results, path)
"""

from openpyxl import Workbook

from tools.common import claude, excel
from tools.common.data import REF_COLS, ROW_COL, SOURCE_COL, Columns

NAME = "Product Name QA"
REQUIRED_COLS = ["mfr", "model"]

SYSTEM_PROMPT = """
You are a product data QA agent for a professional broadcast equipment database called Domino Squad.
Your job is to review product records and flag data quality issues with precision and clarity.

FIELD DEFINITIONS:
- mfr: The manufacturer / brand name of the product
- model: The manufacturer's official model number or part number
- product_name: The full descriptive name of the product

QA STATUS VALUES:
- Pass: Data is accurate, consistent, and logically sound
- Warning: Data may be slightly off or incomplete but does not critically break the record
- Fail: Critical flaw — mismatched fields, wrong manufacturer, invalid model number, missing required data

MODE: Logic-only — use your training knowledge only. You have no web access.
You are checking for logical consistency and obvious data quality issues, not verifying
against live manufacturer websites. Flag what you can reason about confidently from
your knowledge of the broadcast equipment industry.

YOUR CHECKS (run all of these for every record):
1. Do manufacturer + model + product name make sense TOGETHER?
   A model number from Shure paired with a Sennheiser manufacturer name = Fail.
   A product name describing a microphone when the model is a video switcher = Fail.
2. Is the product name plausibly correct for this manufacturer and model number?
   If you recognise the model number and the name is wrong or significantly off = Fail.
   If you recognise the model but the name has a minor abbreviation or variation = Warning.
   If you do not recognise the model number, do not guess — leave this as Pass unless
   there is another clear cross-field issue.
3. Is the manufacturer name spelled correctly and using the standard industry form?
   "Sennhieser" instead of "Sennheiser" = Fail. "GV" instead of "Grass Valley" = Warning.
4. Does the model number look structurally correct for that manufacturer?
   Check for transposed characters, extra spaces, or obvious typos based on known
   manufacturer model number conventions (e.g. Evertz uses dashes, Cisco uses hyphens).
5. Are any required fields blank? (mfr, model, product_name are all required) = Fail.

KEY RULES:
- Never evaluate fields in isolation — use the full record to detect cross-field inconsistencies.
- Always suggest a corrected value when you identify an error, unless none can be inferred.
- If you do not recognise a product, default to Pass — do not fabricate issues.
- Keep feedback professional and actionable — these are suggestions for human review, not verdicts.
- If genuinely uncertain, use Warning, not Fail.

OUTPUT FORMAT — CRITICAL:
Return ONLY a raw JSON array. Start with [ and end with ].
No markdown, no backticks, no explanation before or after.
One object per product in the input, each with EXACTLY these keys:
[
  {
    "_id": "exact value from input",
    "overall_status": "Pass OR Warning OR Fail",
    "general_feedback": "1-2 sentence summary of the audit result",
    "flagged_fields": "comma-separated list of field names with issues, or empty string if none",
    "issues": "pipe-separated list of issue descriptions matching flagged_fields order, or empty string",
    "suggested_corrections": "pipe-separated list of suggested corrections matching order, or empty string"
  }
]
"""


def missing_columns(df) -> list:
    cols = Columns(df)
    return [name for name, col in (("mfr", cols.mfr), ("model", cols.model)) if col is None]


def _result(status: str, feedback: str, **extra) -> dict:
    r = {"overall_status": status, "general_feedback": feedback, "flagged_fields": "",
         "issues": "", "suggested_corrections": ""}
    r.update(extra)
    return _normalise(r)


def _normalise(r: dict) -> dict:
    r["qa_status"] = str(r.get("overall_status", "")).strip() or claude.NEEDS_REVIEW
    note = str(r.get("general_feedback", "")).strip()
    if r.get("issues"):
        note += f" Issues: {r['issues']}"
    if r.get("suggested_corrections"):
        note += f" Suggested: {r['suggested_corrections']}"
    r["qa_note"] = note.strip()
    return r


def run(df, client, model, batch_size, batch_pause, log, progress=None, status=None,
        stop=None) -> dict:
    cols = Columns(df)
    results, records = {}, []
    for _, row in df.iterrows():
        rec = cols.product(row)
        if not rec["mfr"] and not rec["model"]:
            results[row[ROW_COL]] = _result("Fail", "Manufacturer and model are both blank.",
                                            flagged_fields="mfr, model")
            continue
        rec["_row"] = row[ROW_COL]
        records.append(rec)

    log(f"{len(records)} product(s) to check", "dim")
    answered = claude.run_batches(
        client, model, records, SYSTEM_PROMPT, use_search=False,
        batch_size=batch_size, batch_pause=batch_pause, log=log,
        progress=progress, status=status, label=NAME, stop=stop,
        review_result=lambda rec, why: _result(claude.NEEDS_REVIEW, why),
    )
    for row, r in answered.items():
        results[row] = _normalise(r)
    return results


def _count(results: dict, status: str) -> int:
    return sum(1 for r in results.values() if r["qa_status"].lower() == status.lower())


def summarize(results: dict) -> list:
    return [
        ("Total products checked", len(results), None),
        ("Pass", _count(results, "pass"), excel.GOOD),
        ("Warning", _count(results, "warning"), excel.WARN),
        ("Fail", _count(results, "fail"), excel.BAD),
        ("Needs human review (API couldn't process)", _count(results, claude.NEEDS_REVIEW), excel.REVIEW),
    ]


def save_excel(df, results: dict, path: str) -> None:
    cols = Columns(df)
    columns = ["Source File", REF_COLS[0], "Manufacturer", "Model", "Product Name", "Overall Status",
               "General Feedback", "Flagged Fields", "Issues", "Suggested Corrections", REF_COLS[1]]
    rows = []
    for _, row in df.iterrows():
        r = results.get(row[ROW_COL])
        if r is None:
            continue
        p = cols.product(row)
        rows.append({
            "Source File": row.get(SOURCE_COL, ""), **cols.refs(row),
            "Manufacturer": p["mfr"], "Model": p["model"],
            "Product Name": p["product_name"], "Overall Status": r["qa_status"],
            "General Feedback": r.get("general_feedback", ""), "Flagged Fields": r.get("flagged_fields", ""),
            "Issues": r.get("issues", ""), "Suggested Corrections": r.get("suggested_corrections", ""),
        })
    wb = Workbook()
    excel.write_sheet(wb, "QA Results", columns, rows, widths=[18, 38, 20, 24, 44, 14, 48, 28, 55, 55, 45],
                      status_cols=["Overall Status"], row_height=52)
    excel.write_summary(wb, "Summary", summarize(results), heading=f"{NAME} — Summary")
    wb.save(path)

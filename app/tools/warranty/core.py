"""
Warranty Validation engine — pure Python, no UI dependency.

Asks Claude (with web search) whether each product's recorded
warranty_length (in days) matches the manufacturer's published warranty.
In this database 36500 days means LIFETIME — an intentional convention,
not an error. Follows the engine contract described in
tools/product_name_qa/core.py.
"""

from openpyxl import Workbook

from tools.common import claude, excel
from tools.common.data import ROW_COL, SOURCE_COL, Columns, clean, find_col

NAME = "Warranty Validation"
LIFETIME_DAYS = 36500

SYSTEM_PROMPT = """
You are a warranty verification agent for a professional broadcast equipment database.
Your job is to verify whether the recorded warranty_length (in days) is correct for each product.

DATABASE CONVENTION — IMPORTANT:
warranty_length = 36500 means LIFETIME WARRANTY in this database.
This is NOT a data error. It is an intentional convention.

VERIFICATION LOGIC — follow this exactly for every product:

STEP 1 — CHECK FOR LIFETIME (warranty_length = 36500):
Search to verify whether this specific product actually qualifies for a lifetime warranty.
  If the manufacturer confirms lifetime warranty for this model → status: Pass (Lifetime)
  If the manufacturer shows a standard limited warranty instead → status: Fail
    (the product does not qualify for lifetime and should have a different value)
  If you cannot determine → status: Warning

STEP 2 — ALL OTHER VALUES:
First search for a PRODUCT-SPECIFIC warranty for this exact model.
If found → convert to days and compare with recorded value (±15 day tolerance).
If not found → search for the MANUFACTURER STANDARD WARRANTY (general warranty policy).
If standard found → use as fallback, note it is not product-specific.
If neither found → status: Unknown.

WARRANTY CONVERSION TABLE (use exactly):
  "1 year"           = 365 days
  "2 years"          = 730 days
  "3 years"          = 1095 days
  "5 years"          = 1825 days
  "lifetime"         = 36500 days
  "limited lifetime" = 36500 days
  "90 days"          = 90 days
  "1 month"          = 30 days
  "6 months"         = 180 days
  For other periods: multiply months × 30 or years × 365

TOLERANCE: ±15 days. If the difference between recorded and verified is ≤15 days → Pass.

STATUS VALUES (use exactly one):
- Pass             → recorded warranty matches verified warranty (within ±15 days)
- Pass (Lifetime)  → warranty_length = 36500 AND product confirmed as lifetime warranty
- Fail             → recorded warranty does not match verified (beyond ±15 day tolerance)
- Warning          → could not find product-specific warranty, using standard MFR fallback
                     OR result is uncertain — human review recommended
- Unknown          → no warranty information found from any source
- Error            → warranty_length is blank, null, zero, or clearly invalid

SEARCH STRATEGY:
1. If product_url provided → check it first for warranty information
2. Search manufacturer's official website for the product's warranty terms
   (search by product_name if model is blank)
3. Check manufacturer's general warranty policy page as fallback
4. Do NOT use eBay, forums, or secondhand sites as warranty sources

OUTPUT FORMAT — CRITICAL:
Return ONLY a raw JSON array. Start with [ and end with ].
No markdown, no backticks, no text before or after.
One object per product in the input, each with EXACTLY these keys:
[
  {
    "_id": "exact value from input",
    "warranty_type": "Lifetime OR Product-Specific OR Standard MFR OR Unknown",
    "verified_warranty_days": "number in days if found, else empty string",
    "verified_warranty_description": "e.g. 2 years, Limited Lifetime, 1 year, or empty string",
    "status": "Pass OR Pass (Lifetime) OR Fail OR Warning OR Unknown OR Error",
    "source": "URL or source where warranty was found, else empty string",
    "note": "1-2 sentences explaining the result"
  }
]
"""

# "Pass (Lifetime)" must win over plain "pass", and Error is its own colour.
RULES = [("error", excel.REVIEW)] + excel.DEFAULT_STATUS_RULES


def _warranty_col(df):
    return find_col(df, "warranty_length", "warranty length", "warranty")


def missing_columns(df) -> list:
    cols = Columns(df)
    missing = [name for name, col in (("mfr", cols.mfr), ("model", cols.model)) if col is None]
    if _warranty_col(df) is None:
        missing.append("warranty_length")
    return missing


def _valid_days(value: str) -> bool:
    """A usable warranty is a positive number of days — blank, zero and
    text values are reported as Error without spending an API call."""
    try:
        return float(value) > 0
    except ValueError:
        return False


def _result(status: str, note: str, **extra) -> dict:
    r = {"status": status, "note": note}
    r.update(extra)
    return _normalise(r)


def _normalise(r: dict) -> dict:
    r["qa_status"] = str(r.get("status", "")).strip() or claude.NEEDS_REVIEW
    r["qa_note"] = str(r.get("note", "")).strip()
    return r


def run(df, client, model, batch_size, batch_pause, log, progress=None, status=None) -> dict:
    cols = Columns(df)
    w_col = _warranty_col(df)
    results, records = {}, []
    for _, row in df.iterrows():
        rec = cols.product(row)
        days = clean(row.get(w_col))
        if not _valid_days(days):
            results[row[ROW_COL]] = _result(
                "Error", f"warranty_length is blank, zero or not a number ({days or 'blank'}).")
            continue
        if not rec["model"] and not rec["product_name"]:
            results[row[ROW_COL]] = _result("Skip", "No model number or product name — nothing to search for.")
            continue
        rec["warranty_length"] = days
        if cols.url and clean(row.get(cols.url)):
            rec["product_url"] = clean(row.get(cols.url))
        rec["_row"] = row[ROW_COL]
        records.append(rec)

    log(f"{len(records)} product(s) to check", "dim")
    answered = claude.run_batches(
        client, model, records, SYSTEM_PROMPT, use_search=True,
        batch_size=batch_size, batch_pause=batch_pause, log=log,
        progress=progress, status=status, label=NAME,
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
        ("", "", None),
        ("Pass — warranty correct", _count(results, "pass") + _count(results, "pass (lifetime)"), excel.GOOD),
        ("  of which: Lifetime verified", _count(results, "pass (lifetime)"), excel.GOOD),
        ("Fail — warranty mismatch", _count(results, "fail"), excel.BAD),
        ("Warning — using MFR standard / uncertain", _count(results, "warning"), excel.WARN),
        ("Unknown — no data found", _count(results, "unknown"), excel.INFO),
        ("Error — invalid warranty value", _count(results, "error"), excel.REVIEW),
        ("Skipped — nothing to search for", _count(results, "skip"), excel.INFO),
        ("Needs human review (API couldn't process)", _count(results, claude.NEEDS_REVIEW), excel.REVIEW),
    ]


def save_excel(df, results: dict, path: str) -> None:
    cols = Columns(df)
    w_col = _warranty_col(df)
    columns = ["Source File", "Manufacturer", "Model", "Product Name",
               "Recorded Warranty (days)", "Warranty Type",
               "Verified Warranty (days)", "Verified Warranty Description",
               "Status", "Source", "Note"]
    rows = []
    for _, row in df.iterrows():
        r = results.get(row[ROW_COL])
        if r is None:
            continue
        p = cols.product(row)
        rows.append({
            "Source File": row.get(SOURCE_COL, ""), "Manufacturer": p["mfr"], "Model": p["model"],
            "Product Name": p["product_name"],
            "Recorded Warranty (days)": clean(row.get(w_col)) if w_col else "",
            "Warranty Type": r.get("warranty_type", ""),
            "Verified Warranty (days)": r.get("verified_warranty_days", ""),
            "Verified Warranty Description": r.get("verified_warranty_description", ""),
            "Status": r["qa_status"], "Source": r.get("source", ""), "Note": r["qa_note"],
        })
    wb = Workbook()
    excel.write_sheet(wb, "Warranty Check", columns, rows,
                      widths=[16, 18, 22, 40, 20, 20, 22, 26, 18, 50, 55],
                      status_cols=["Status"], rules=RULES, row_height=48)
    excel.write_summary(wb, "Summary", summarize(results), heading=f"{NAME} — Summary")
    wb.save(path)

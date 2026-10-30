"""
Dimensions Validation engine — pure Python, no UI dependency.

Asks Claude (with web search) to check each product's recorded product and
package dimensions for unit consistency, completeness and accuracy against
manufacturer specs. Follows the engine contract in product_name_qa/core.py.

Unit consistency is left to the model rather than pre-computed here:
dimensions are often stored as JSON with no units at all, so a regex
can't reliably tell "Metric" from "Blank", and a wrong deterministic
verdict would be worse than the model's.
"""

from openpyxl import Workbook

from tools.common import claude, excel
from tools.common.data import ROW_COL, SOURCE_COL, Columns, clean, find_col

NAME = "Dimensions Validation"

PRODUCT_DIM_NAMES = ("product_dimension", "product dimension", "product_dimensions", "dimensions")
PACKAGE_DIM_NAMES = ("package_dimensions", "package dimensions", "package_dimension")

SYSTEM_PROMPT = """
You are a dimensions verification agent for a professional broadcast equipment database.
For each product you must run three checks in order.

DIMENSIONS FORMAT:
Dimensions may be stored as JSON objects like {"width":"12","height":"8","depth":"4"}
or as plain strings like "12 x 8 x 4 in" or "305 x 203 x 102 mm".
Parse whichever format is present. If a field is null, empty, or missing → treat as blank.

─────────────────────────────────────────
CHECK 1 — UNIT CONSISTENCY
─────────────────────────────────────────
Detect the unit system used in product_dimension and package_dimensions separately.

METRIC indicators:   mm, cm, m, kg, g
IMPERIAL indicators: in, inch, inches, ", ft, lbs, lb, oz

Unit system values to assign:
- "Metric"   → all dimension values use metric units
- "Imperial" → all dimension values use imperial units
- "Mixed"    → some values metric, some imperial WITHIN the same field
- "Blank"    → field is empty, null, or missing

CONSISTENCY RULE:
If product_dimension unit system ≠ package_dimensions unit system
AND neither is "Blank" → flag as INCONSISTENT.
This includes: product=Metric + package=Imperial, or product=Imperial + package=Metric.
Both Metric = Consistent. Both Imperial = Consistent.

─────────────────────────────────────────
CHECK 2 — COMPLETENESS
─────────────────────────────────────────
Only run this check if product_dimension OR package_dimensions is blank.

Search the manufacturer website or authorised resellers for this product's dimensions.
  If manufacturer specs are found online:
    → Set status to "Flag — Missing Data"
    → Record the found dimensions in verified fields
    → Note which field(s) are blank and what the correct values should be
  If no manufacturer specs found anywhere online:
    → Set status to "Skip"
    → Do not flag — nothing actionable

─────────────────────────────────────────
CHECK 3 — ACCURACY
─────────────────────────────────────────
Only run this check if BOTH recorded fields are populated (not blank).

FIRST — check whether the dimensions are marked as approximate.
Look for any of these signals in the dimension fields or anywhere in the record:
  "approximate", "approx", "approx.", "est.", "estimated", "~", "circa", "about"

IF APPROXIMATE DIMENSIONS ARE DETECTED:
  Do NOT do a precise comparison against manufacturer specs.
  Instead apply a logic check:
    - Does this dimension make practical sense for this type of product?
    - Is the size reasonable for broadcast equipment of this category?
      (e.g. a rack unit should be ~482mm wide, a handheld mic ~200mm long)
    - Are the proportions logical? (depth should not exceed width for most rack gear)
  If the approximate dimensions seem reasonable → Pass (Approximate)
  If the approximate dimensions seem clearly wrong for this product type
    (e.g. a small accessory recorded as 2000mm × 1500mm) → Warning (Approximate — Review)
  Do NOT search the manufacturer for exact specs in this case.

IF DIMENSIONS ARE NOT MARKED AS APPROXIMATE:
  Search for the manufacturer's official dimensions for this specific model.
  Compare recorded vs manufacturer specs.
  TOLERANCE: ±5mm OR ±0.2 inches (apply after converting to same unit if needed).
    Within tolerance  → Pass or Warning (note the difference)
    Beyond tolerance  → Fail — Mismatch (record both values)
    Specs not found   → Unknown (cannot verify)
  When comparing: always convert to the same unit before comparing.
    1 inch = 25.4 mm. Round to 1 decimal place after converting.

─────────────────────────────────────────
SEARCH STRATEGY:
1. If product_url is provided → check that page first for dimension data
2. Search manufacturer's official product page for this model
3. Check major authorised resellers (B&H, Markertek, Adorama) as secondary sources
4. Do NOT use eBay, forums, or unverified sources

─────────────────────────────────────────
STATUS VALUES (use exactly one per product):
- Pass                      → dimensions correct, units consistent, matches manufacturer
- Pass (Approximate)        → dimensions marked as approximate and are reasonable for this product type
- Fail — Mismatch           → recorded dimensions don't match manufacturer specs beyond tolerance
- Fail — Inconsistent Units → product and package dimensions use different unit systems
- Fail — Mixed Units        → dimensions within a single field use mixed metric/imperial
- Flag — Missing Data       → one or both dimension fields blank, but manufacturer specs found online
- Warning                   → dimensions slightly off but within extended tolerance — human review recommended
- Warning (Approximate — Review) → dimensions marked as approximate but seem off for this product type
- Skip                      → dimension fields blank AND no manufacturer specs found — nothing to action
- Unknown                   → specs found but unable to confidently compare

─────────────────────────────────────────
OUTPUT FORMAT — CRITICAL:
Return ONLY a raw JSON array. Start with [ and end with ].
No markdown, no backticks, no text before or after the array.
One object per product in the input, each with EXACTLY these keys:
[
  {
    "_id": "exact value from input",
    "product_unit_system": "Metric OR Imperial OR Mixed OR Blank",
    "package_unit_system": "Metric OR Imperial OR Mixed OR Blank",
    "unit_consistency": "Consistent OR Inconsistent OR N/A",
    "verified_product_dimensions": "manufacturer spec if found, else empty string",
    "verified_package_dimensions": "manufacturer spec if found, else empty string",
    "status": "one of the STATUS VALUES above, exactly as written",
    "source": "URL where dimensions were found, else empty string",
    "note": "1-2 sentences explaining the result, which fields have issues, and any specific values that differ"
  }
]
"""


def _dim_cols(df):
    return find_col(df, *PRODUCT_DIM_NAMES), find_col(df, *PACKAGE_DIM_NAMES)


def missing_columns(df) -> list:
    cols = Columns(df)
    missing = [name for name, col in (("mfr", cols.mfr), ("model", cols.model)) if col is None]
    if _dim_cols(df) == (None, None):
        missing.append("product_dimension or package_dimensions")
    return missing


def _result(status: str, note: str, **extra) -> dict:
    r = {"status": status, "note": note, "product_unit_system": "", "package_unit_system": "",
         "unit_consistency": "", "verified_product_dimensions": "",
         "verified_package_dimensions": "", "source": ""}
    r.update(extra)
    return _normalise(r)


def _normalise(r: dict) -> dict:
    r["qa_status"] = str(r.get("status", "")).strip() or claude.NEEDS_REVIEW
    r["qa_note"] = str(r.get("note", "")).strip()
    return r


def run(df, client, model, batch_size, batch_pause, log, progress=None, status=None) -> dict:
    cols = Columns(df)
    prod_col, pkg_col = _dim_cols(df)
    results, records = {}, []
    for _, row in df.iterrows():
        rec = cols.product(row)
        rec["product_dimension"] = clean(row.get(prod_col)) if prod_col else ""
        rec["package_dimensions"] = clean(row.get(pkg_col)) if pkg_col else ""
        if not rec["mfr"] and not rec["model"]:
            results[row[ROW_COL]] = _result(
                claude.NEEDS_REVIEW, "Manufacturer and model are both blank — nothing to search for.")
            continue
        if cols.url:
            url = clean(row.get(cols.url))
            if url:
                rec["product_url"] = url
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


def _count(results: dict, pred) -> int:
    return sum(1 for r in results.values() if pred(r["qa_status"].lower()))


def summarize(results: dict) -> list:
    incon = sum(1 for r in results.values() if str(r.get("unit_consistency", "")).lower() == "inconsistent")
    return [
        ("Total products checked", len(results), None),
        ("", "", None),
        ("By result:", "", None),
        ("  Pass — dimensions correct", _count(results, lambda s: s.startswith("pass")), excel.GOOD),
        ("  Fail — dimension mismatch", _count(results, lambda s: "mismatch" in s), excel.BAD),
        ("  Fail — inconsistent unit systems", _count(results, lambda s: "inconsistent" in s), excel.BAD),
        ("  Fail — mixed units in field", _count(results, lambda s: "mixed" in s), excel.BAD),
        ("  Flag — missing data (fill these)", _count(results, lambda s: "missing" in s), excel.WARN),
        ("  Warning — slight difference / review", _count(results, lambda s: s.startswith("warning")), excel.WARN),
        ("  Skip — no data available", _count(results, lambda s: s == "skip"), excel.INFO),
        ("  Unknown — unable to verify", _count(results, lambda s: s == "unknown"), excel.INFO),
        ("  Needs human review (API couldn't process)",
         _count(results, lambda s: s == claude.NEEDS_REVIEW.lower()), excel.REVIEW),
        ("", "", None),
        ("Unit consistency issues total", incon, excel.BAD),
    ]


def save_excel(df, results: dict, path: str) -> None:
    cols = Columns(df)
    prod_col, pkg_col = _dim_cols(df)
    columns = ["Source File", "Manufacturer", "Model", "Product Name",
               "Recorded Product Dimensions", "Recorded Package Dimensions",
               "Product Unit System", "Package Unit System", "Unit Consistency",
               "Verified Product Dimensions", "Verified Package Dimensions",
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
            "Recorded Product Dimensions": clean(row.get(prod_col)) if prod_col else "",
            "Recorded Package Dimensions": clean(row.get(pkg_col)) if pkg_col else "",
            "Product Unit System": r.get("product_unit_system", ""),
            "Package Unit System": r.get("package_unit_system", ""),
            "Unit Consistency": r.get("unit_consistency", ""),
            "Verified Product Dimensions": r.get("verified_product_dimensions", ""),
            "Verified Package Dimensions": r.get("verified_package_dimensions", ""),
            "Status": r["qa_status"], "Source": r.get("source", ""), "Note": r["qa_note"],
        })
    # "Inconsistent" must be checked before "consistent", which it contains;
    # "Fail — Mismatch" is red here, not the default amber for "mismatch".
    rules = [("inconsistent", excel.BAD), ("consistent", excel.GOOD),
             ("fail", excel.BAD)] + excel.DEFAULT_STATUS_RULES
    wb = Workbook()
    excel.write_sheet(wb, "Dimensions Check", columns, rows,
                      widths=[14, 16, 20, 36, 32, 32, 14, 14, 14, 32, 32, 22, 44, 55],
                      status_cols=["Unit Consistency", "Status"], rules=rules, row_height=52)
    excel.write_summary(wb, "Summary", summarize(results), heading=f"{NAME} — Summary")
    wb.save(path)

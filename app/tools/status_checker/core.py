"""
Product status verification engine — pure Python, no UI dependency.

Calls the Claude API (with the web search tool) to verify whether products
in a CSV are still supported, discontinued, or unknown. Kept separate from
the Flet view so the verification logic can be tested and reused on its own.
"""

import json
import time
from pathlib import Path

import anthropic
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from tools.common import claude, excel
from tools.common.data import REF_COLS, ROW_COL, Columns, find_col

NAME = "Status Check"

# Model choices, the saved API key and fatal-error handling are shared
# with the other AI tools.
MODELS = claude.MODELS
DEFAULT_MODEL_LABEL = claude.DEFAULT_MODEL_LABEL
load_cfg = claude.load_cfg
save_cfg = claude.save_cfg
FatalAPIError = claude.FatalAPIError

REQUIRED_COLS = ["mfr", "product_status"]
BASE_COLS = ["mfr", "model", "product name", "product_status"]
URL_COL = "product_url"

NEEDS_REVIEW_STATUS = "could not check"

SYSTEM_PROMPT = """
You are a product lifecycle status verification agent for a professional broadcast equipment database.

VALID STATUS VALUES (only three accepted):
- supported    → currently sold, OR no longer manufactured/sold but the manufacturer still
                 actively supports it (software/firmware updates, driver support, official
                 service/repair, or a support page that still lists it as current)
- discontinued → manufacturer stopped BOTH selling it AND supporting it — no more
                 software/firmware updates, no active service/repair support, and either an
                 explicit end-of-support/EOL statement or no evidence of ongoing support
- unknown      → status genuinely cannot be determined from available online sources

END OF SALE IS NOT THE SAME AS END OF SUPPORT:
An older model that's no longer manufactured or sold is NOT automatically discontinued. Many
manufacturers keep issuing firmware/software updates and honoring service/support for products
long after they stop selling them — that product is still supported. Only call it discontinued
once support itself has ended, not merely production/sales.

IDENTIFYING THE PRODUCT:
Some products have no model number. If Model is blank, search using the manufacturer and
Product Name instead — a missing model number is not by itself a reason to return unknown.

VERIFICATION STRATEGY — TWO TIER:

TIER 1 — URL CHECK FIRST (when product_url is provided):
Search for or visit the product_url directly. Look for clear signals:
  CLEAR SUPPORTED: active product page, pricing shown, add to cart, current specs → supported, no further search needed.
  CLEAR DISCONTINUED: 404, "discontinued", "end of support", "end of life" → discontinued, no further search needed.
  "Replaced by" / "no longer available for purchase" alone is NOT clear discontinued — it only
  means sales stopped; proceed to Tier 2 to check whether support has also ended.
  AMBIGUOUS: page loads but no clear status, generic page, no pricing → proceed to Tier 2.

TIER 2 — FULL SEARCH (when URL is ambiguous, missing, or Tier 1 inconclusive):
1. Search the manufacturer's official website for the model number, or the product name if no
   model number was given.
2. Check the manufacturer's SUPPORT/downloads page specifically — recent firmware/software
   updates, current driver support, or the product still being listed as actively supported
   means supported, even if it's no longer sold. Only treat it as discontinued once you find an
   explicit end-of-support/EOL announcement, or confirm there's no ongoing support at all.
3. Check authorised resellers (B&H Photo, Markertek, Sweetwater, Adorama, CDW).
   Active new/in-stock listing at authorised dealer = supported.
4. Do NOT count eBay or secondhand marketplaces as evidence of supported.

KEY RULES:
- End of sale / end of production / "no longer available to purchase" / replaced by a newer
  model → NOT by itself discontinued. Check for continued software/firmware/service support
  before concluding anything.
- End of support / end of life / "no longer supported" / no firmware or service activity found
  after a genuine attempt to check → discontinued.
- Product with an active successor is still supported on its own if the manufacturer continues
  supporting it directly (updates/service) — a newer model existing doesn't discontinue the old
  one by itself; it only does if support for the old one has also actually ended.
- Acquired manufacturer: check if acquiring company explicitly continues support
- Never guess — if inconclusive after both tiers, return unknown. Unknown is a last resort
  (the DQA team no longer uses it otherwise): only when there is genuinely no way to tell
  whether the product is supported or discontinued.
- NEVER omit a product from the output. Every product you were given must appear exactly once,
  even one you could only search by name, or one you couldn't verify at all — in that last case
  return Verified Status: unknown with a Reason explaining what was missing/inconclusive.
- If Match is "Mismatch", Reason is REQUIRED and must explain the discrepancy — never leave
  Reason empty when Match is Mismatch.

OUTPUT — CRITICAL:
Return ONLY a raw JSON array. Start with [ and end with ].
No markdown, no backticks, no text before or after the JSON array.

Each object must have EXACTLY these keys:
[
  {
    "_id": "exact value from input, if the input has one",
    "Manufacturer": "exact value from input",
    "Model": "exact value from input (empty string if none was given)",
    "Product Name": "exact value from input",
    "DB Status": "status as recorded in database",
    "Verified Status": "supported OR discontinued OR unknown",
    "Match": "Confirmed OR Mismatch",
    "Suggested Status": "correction if mismatch, else empty string",
    "Reason": "1-2 sentences with source citation — REQUIRED if Match is Mismatch, else empty string"
  }
]
"""

NAV = "#0D1B2A"


def load_df(path: str) -> pd.DataFrame:
    """Load a product CSV, trying multiple encodings."""
    for enc in ["utf-8", "latin-1", "cp1252", "iso-8859-1"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not read file — check its encoding")


def _hex_fill(h):
    return PatternFill("solid", fgColor=h.lstrip("#"))


def _thin_border():
    side = Side(style="thin", color="DDDDDD")
    return Border(left=side, right=side, top=side, bottom=side)


def save_excel(results: list, output_path: str) -> None:
    """Write results to a formatted Excel workbook with a Results + Summary sheet."""
    cols = ["Source File", "Manufacturer", "Model", "Product Name",
            "DB Status", "Verified Status", "Match", "Suggested Status", "Reason", *REF_COLS]
    df = pd.DataFrame(results)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df[cols].to_excel(output_path, index=False, sheet_name="Results")

    wb = load_workbook(output_path)
    ws = wb["Results"]

    widths = [18, 20, 24, 44, 14, 16, 16, 16, 55, 38, 45]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for cell in ws[1]:
        cell.fill = _hex_fill(NAV)
        cell.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _thin_border()
    ws.row_dimensions[1].height = 22

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        mv = str(row[6].value or "")
        vv = str(row[5].value or "").lower()
        needs_review = vv == NEEDS_REVIEW_STATUS or "Needs Review" in mv
        bg = ("F3E5F5" if needs_review else
              "FFF8E1" if "Mismatch" in mv else
              "F1F8E9" if "Confirmed" in mv else "FAFAFA")
        for cell in row:
            cell.fill = _hex_fill(bg)
            cell.font = Font(name="Calibri", size=9)
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            cell.border = _thin_border()
        mc = row[6]
        if needs_review:
            mc.fill = _hex_fill("E1BEE7")
            mc.font = Font(name="Calibri", size=9, bold=True, color="4A148C")
        elif "Mismatch" in mv:
            mc.fill = _hex_fill("FFE082")
            mc.font = Font(name="Calibri", size=9, bold=True, color="7A5000")
        elif "Confirmed" in mv:
            mc.fill = _hex_fill("A5D6A7")
            mc.font = Font(name="Calibri", size=9, bold=True, color="1B5E20")
        vc = row[5]
        if vv == "supported":
            vc.fill = _hex_fill("C8E6C9")
            vc.font = Font(name="Calibri", size=9, color="1B5E20")
        elif vv == "discontinued":
            vc.fill = _hex_fill("FFCDD2")
            vc.font = Font(name="Calibri", size=9, color="B71C1C")
        elif vv == "unknown":
            vc.fill = _hex_fill("E3F2FD")
            vc.font = Font(name="Calibri", size=9, color="0D47A1")
        elif vv == NEEDS_REVIEW_STATUS:
            vc.fill = _hex_fill("E1BEE7")
            vc.font = Font(name="Calibri", size=9, bold=True, color="4A148C")
        ws.row_dimensions[row[0].row].height = 40

    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("Summary")
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 12
    conf = sum(1 for r in results if "Confirmed" in str(r.get("Match", "")))
    mis = sum(1 for r in results if "Mismatch" in str(r.get("Match", "")))
    unk = sum(1 for r in results if str(r.get("Verified Status", "")).lower() == "unknown")
    review = sum(1 for r in results if str(r.get("Verified Status", "")).lower() == NEEDS_REVIEW_STATUS)
    ws2["A1"] = "Summary"
    ws2["A1"].font = Font(bold=True, color="FFFFFF", name="Calibri", size=12)
    ws2["A1"].fill = _hex_fill(NAV)
    ws2["B1"].fill = _hex_fill(NAV)
    ws2.row_dimensions[1].height = 24
    for i, (lbl, cnt, bg) in enumerate([
        ("Total products checked", len(results), "F5F5F5"),
        ("Confirmed correct", conf, "C8E6C9"),
        ("Mismatches found", mis, "FFE082"),
        ("Unable to verify", unk, "E3F2FD"),
        ("Needs human review (API couldn't process)", review, "E1BEE7"),
    ], 2):
        c1 = ws2.cell(i, 1, lbl)
        c1.font = Font(name="Calibri", size=10)
        c1.fill = _hex_fill(bg)
        c2 = ws2.cell(i, 2, cnt)
        c2.font = Font(name="Calibri", size=10, bold=True)
        c2.alignment = Alignment(horizontal="center")
        c2.fill = _hex_fill(bg)
        ws2.row_dimensions[i].height = 18
    wb.save(output_path)


def _clean(val) -> str:
    """Turn a possibly-NaN/None cell value into a plain, trimmed string —
    pandas represents a blank cell as float('nan'), and str(nan) == 'nan',
    which would otherwise get treated as a real (bogus) value."""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _product_key(mfr, model, name) -> tuple:
    """Identity key for matching a product across runs/responses. Falls
    back to product name when there's no model number, so two different
    model-less products from the same manufacturer don't collide under a
    shared blank-model key."""
    mfr_k = _clean(mfr).lower()
    model_k = _clean(model)
    if model_k:
        return (mfr_k, model_k.lower())
    return (mfr_k, f"noname:{_clean(name).lower()}")


def load_already_verified(output_path: str):
    """For recovery mode: read an existing output workbook and return
    (existing_results, set of product keys already verified).

    Rows flagged NEEDS_REVIEW_STATUS are deliberately excluded from both —
    they were never actually verified, so a recovery run should retry them,
    not skip them forever."""
    if not Path(output_path).exists():
        return [], set()
    existing = pd.read_excel(output_path, sheet_name="Results")
    records = existing.to_dict("records")
    verified = [r for r in records if str(r.get("Verified Status", "")).lower() != NEEDS_REVIEW_STATUS]
    keys = {
        _product_key(r.get("Manufacturer", ""), r.get("Model", ""), r.get("Product Name", ""))
        for r in verified
    }
    return verified, keys


def _needs_review(batch_records: list, reason: str) -> list:
    """Build a placeholder result for every product in a batch the API
    couldn't process, instead of retrying it — so a rate limit, an overload,
    or a malformed response doesn't spend more time/money chasing products
    the model already failed to return; a human decides those instead."""
    return [
        {
            "Manufacturer": _clean(r.get("mfr", "")),
            "Model": _clean(r.get("model", "")),
            "Product Name": _clean(r.get("product name", "")),
            "DB Status": _clean(r.get("product_status", "")),
            "Verified Status": NEEDS_REVIEW_STATUS,
            "Match": "Needs Review",
            "Suggested Status": "",
            "Reason": reason,
        }
        for r in batch_records
    ]


def _ensure_mismatch_reason(results: list) -> list:
    """Guarantee every Mismatch carries a note, regardless of whether the
    model followed the prompt's instruction to always explain one."""
    for r in results:
        if str(r.get("Match", "")).strip().lower() == "mismatch" and not str(r.get("Reason", "")).strip():
            r["Reason"] = "Flagged as a mismatch but the model didn't provide an explanation — verify manually."
    return results


def verify_batch(client: anthropic.Anthropic, model: str, batch_records: list,
                  log_callback) -> list:
    """Send one batch to Claude — a single attempt, no retries. If the call
    errors or the response can't be parsed, every product in the batch comes
    back flagged as needing human review rather than being retried."""
    prompt = f"Verify the status of these products:\n{json.dumps(batch_records, indent=2)}"

    try:
        return _ensure_mismatch_reason(claude.ask_json(client, model, SYSTEM_PROMPT, prompt, use_search=True))

    except (ValueError, json.JSONDecodeError) as e:
        log_callback("Malformed response from the model — flagging batch for human review", "warn")
        return _needs_review(batch_records, f"Unusable model response: {str(e)[:80]}")

    except anthropic.RateLimitError:
        log_callback("Rate limited — flagging batch for human review", "warn")
        return _needs_review(batch_records, "Rate limited by the API")

    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            log_callback("API overloaded (529) — flagging batch for human review", "warn")
            return _needs_review(batch_records, "API was overloaded (529)")
        if e.status_code in claude.FATAL_STATUS_CODES:
            raise FatalAPIError(f"API error {e.status_code} — check your API key and model selection") from e
        log_callback(f"API error ({e.status_code}) — flagging batch for human review", "bad")
        return _needs_review(batch_records, f"API error ({e.status_code})")

    except Exception as e:
        log_callback(f"Unexpected error: {str(e)[:100]} — flagging batch for human review", "bad")
        return _needs_review(batch_records, f"Unexpected error: {str(e)[:100]}")


def _canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename whatever headers the file uses (Studio export "Page URL",
    "Product Name", older "product_url", ...) to the names this module and
    its prompt work with, and add the report's reference columns."""
    cols = Columns(df)
    rename = {}
    for found, canonical in ((cols.mfr, "mfr"), (cols.model, "model"), (cols.name, "product name"),
                             (find_col(df, "product_status", "status"), "product_status"),
                             (cols.url, URL_COL)):
        if found is not None and found != canonical:
            rename[found] = canonical
    out = df.rename(columns=rename)
    refs = df.apply(lambda r: pd.Series(cols.refs(r)), axis=1) if len(df) else pd.DataFrame(columns=REF_COLS)
    for c in REF_COLS:
        out[c] = refs[c] if c in refs else ""
    return out


def check_file(path: str, client: anthropic.Anthropic, model: str, batch_size: int,
                batch_pause: int, skip_keys: set, progress_callback, log_callback,
                status_callback, stop=None):
    """
    Verify all rows of one CSV against Claude. Returns
    (file_results, total_rows, skipped_already_verified_count).

    progress_callback(pct, done_batches, total_batches) and log_callback(msg, tag)
    follow the same shape as the Link Checker's core.check_file callbacks.
    `stop` is an optional threading.Event checked before each batch — rows
    not reached are simply left out, so a Recovery-mode re-run picks them up.
    """
    df = _canonical_columns(load_df(path))

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f'Missing columns: {", ".join(missing)}\nFound: {", ".join(df.columns)}')

    df = df.dropna(subset=REQUIRED_COLS).reset_index(drop=True)

    has_name = "product name" in df.columns

    if skip_keys:
        before = len(df)
        df = df[~df.apply(
            lambda r: _product_key(r.get("mfr", ""), r.get("model", ""), r.get("product name", ""))
            in skip_keys, axis=1,
        )].reset_index(drop=True)
        skipped = before - len(df)
        if skipped:
            log_callback(f"Skipping {skipped} already-verified products", "dim")
    else:
        skipped = 0

    total = len(df)
    if total == 0:
        return [], 0, skipped

    has_url = URL_COL in df.columns
    if has_url:
        log_callback("product_url detected — two-tier verification active", "dim")
    cols = [c for c in (BASE_COLS + ([URL_COL] if has_url else [])) if c in df.columns]

    name = Path(path).name
    file_results = []

    # A product needs a model number OR a product name to search for at
    # all — flag those with neither for human review instead of either
    # dropping them (the old behavior) or burning an API call on them.
    def identifiable(row):
        if _clean(row.get("model")):
            return True
        return has_name and bool(_clean(row.get("product name")))

    identifiable_mask = df.apply(identifiable, axis=1)
    unsearchable = df[~identifiable_mask]
    df = df[identifiable_mask].reset_index(drop=True)

    if len(unsearchable) > 0:
        unsearchable_records = unsearchable[[c for c in BASE_COLS if c in unsearchable.columns]].to_dict(orient="records")
        log_callback(
            f"{len(unsearchable_records)} product(s) have no model number or product name — "
            "flagging for human review instead of skipping", "warn")
        flagged = _needs_review(unsearchable_records, "No model number or product name given — nothing to search for")
        for r in flagged:
            r["Source File"] = name
        file_results.extend(flagged)

    total_searchable = len(df)
    if total_searchable == 0:
        return file_results, total, skipped

    num_batches = (total_searchable + batch_size - 1) // batch_size
    refs_by_key = {
        _product_key(r.get("mfr", ""), r.get("model", ""), r.get("product name", "")): {c: r[c] for c in REF_COLS}
        for r in df.to_dict(orient="records")
    }

    for bn, start in enumerate(range(0, total_searchable, batch_size), 1):
        if stop is not None and stop.is_set():
            log_callback(f"Stopped — {total_searchable - start} product(s) in this file not checked", "warn")
            break
        end = min(start + batch_size, total_searchable)
        batch = df.iloc[start:end][cols].to_dict(orient="records")

        log_callback(f"Batch {bn}/{num_batches} (rows {start + 1}-{end})...", "dim")
        status_callback(f"{name}  •  batch {bn}/{num_batches}")

        results = verify_batch(client, model, batch, log_callback)

        returned_keys = {
            _product_key(r.get("Manufacturer", ""), r.get("Model", ""), r.get("Product Name", ""))
            for r in results
        }
        missing_records = [
            b for b in batch
            if _product_key(b.get("mfr", ""), b.get("model", ""), b.get("product name", ""))
            not in returned_keys
        ]
        if missing_records:
            log_callback(
                f"{len(missing_records)} product(s) missing from the model's response — "
                "flagging for human review", "warn")
            results = results + _needs_review(missing_records, "Model did not return a result for this product")

        for r in results:
            r["Source File"] = name
            r.update(refs_by_key.get(
                _product_key(r.get("Manufacturer", ""), r.get("Model", ""), r.get("Product Name", "")), {}))
        file_results.extend(results)

        needs_review = sum(1 for r in results if r.get("Verified Status") == NEEDS_REVIEW_STATUS)
        if needs_review:
            log_callback(f"{len(results) - needs_review} verified, {needs_review} flagged for human review", "warn")
        elif results:
            log_callback(f"{len(results)} results received", "ok")

        progress_callback(int(bn / num_batches * 100), bn, num_batches)

        if bn < num_batches:
            for s in range(batch_pause, 0, -1):
                if stop is not None and stop.is_set():
                    break
                status_callback(f"Pausing {s}s before next batch...")
                time.sleep(1)

    lost = total - len(file_results)
    if lost:
        log_callback(f"{lost} products lost to API errors — re-run with recovery mode on to pick them up", "warn")

    return file_results, total, skipped


# ── Pipeline entry point ────────────────────────────────────
# Same engine contract as the other QA tools (see product_name_qa/core.py),
# used by the Full QA Pipeline on its combined DataFrame. The standalone
# screen keeps using check_file() for its per-file + recovery workflow.

def missing_columns(df) -> list:
    cols = Columns(df)
    missing = [] if cols.mfr else ["mfr"]
    if find_col(df, "product_status", "status") is None:
        missing.append("product_status")
    return missing


def _pipeline_result(r: dict) -> dict:
    verified = str(r.get("Verified Status", "")).strip().lower()
    match = str(r.get("Match", "")).strip()
    if verified == NEEDS_REVIEW_STATUS or match == "Needs Review":
        r["qa_status"] = claude.NEEDS_REVIEW
    elif match.lower() == "mismatch":
        r["qa_status"] = "Mismatch"
    elif verified == "unknown":
        r["qa_status"] = "Unknown"
    else:
        r["qa_status"] = "Confirmed"
    r["qa_note"] = str(r.get("Reason", "")).strip()
    return r


def run(df, client, model, batch_size, batch_pause, log, progress=None, status=None, stop=None) -> dict:
    cols = Columns(df)
    status_col = find_col(df, "product_status", "status")
    results, records = {}, []
    for _, row in df.iterrows():
        p = cols.product(row)
        db_status = _clean(row.get(status_col))
        base = {"Manufacturer": p["mfr"], "Model": p["model"], "Product Name": p["product_name"],
                "DB Status": db_status, "Suggested Status": ""}
        if not db_status:
            results[row[ROW_COL]] = {**base, "Verified Status": "", "Match": "",
                                     "qa_status": "Skip", "qa_note": "No product_status recorded"}
            continue
        if not p["model"] and not p["product_name"]:
            results[row[ROW_COL]] = _pipeline_result({
                **base, "Verified Status": NEEDS_REVIEW_STATUS, "Match": "Needs Review",
                "Reason": "No model number or product name given — nothing to search for"})
            continue
        rec = {"mfr": p["mfr"], "model": p["model"], "product name": p["product_name"],
               "product_status": db_status, "_row": row[ROW_COL]}
        if cols.url:
            rec[URL_COL] = _clean(row.get(cols.url))
        records.append(rec)

    log(f"{len(records)} product(s) to verify", "dim")
    answered = claude.run_batches(
        client, model, records, SYSTEM_PROMPT, use_search=True,
        batch_size=batch_size, batch_pause=batch_pause, log=log,
        progress=progress, status=status, label=NAME, stop=stop,
        review_result=lambda rec, why: _needs_review([rec], why)[0],
    )
    for row, r in answered.items():
        results[row] = _pipeline_result(_ensure_mismatch_reason([r])[0])
    return results


def summarize(results: dict) -> list:
    values = list(results.values())

    def count(s):
        return sum(1 for r in values if r["qa_status"] == s)

    return [
        ("Total products checked", len(values), None),
        ("Confirmed correct", count("Confirmed"), excel.GOOD),
        ("Mismatches found", count("Mismatch"), excel.WARN),
        ("Unable to verify", count("Unknown"), excel.INFO),
        ("Needs human review (API couldn't process)", count(claude.NEEDS_REVIEW), excel.REVIEW),
    ]

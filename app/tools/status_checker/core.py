"""
Product status verification engine — pure Python, no UI dependency.

Calls the Claude API (with the web search tool) to verify whether products
in a CSV are still supported, discontinued, or unknown. Kept separate from
the Flet view so the verification logic can be tested and reused on its own.
"""

import json
import os
import re
import time
from pathlib import Path

import anthropic
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Haiku only vs. Sonnet — Opus is left out on purpose, it's not worth the
# extra cost for this kind of lookup/classification task.
MODELS = {
    "Claude Haiku 4.5 (cheapest)": "claude-haiku-4-5-20251001",
    "Claude Sonnet 4.6 (more thorough)": "claude-sonnet-4-6",
}
DEFAULT_MODEL_LABEL = "Claude Haiku 4.5 (cheapest)"

CONFIG_DIR = Path(os.getenv("APPDATA", Path.home())) / "Toolbox" / "StatusChecker"
CONFIG_FILE = CONFIG_DIR / "config.json"

REQUIRED_COLS = ["mfr", "product_status"]
BASE_COLS = ["mfr", "model", "product name", "product_status"]
URL_COL = "product_url"

NEEDS_REVIEW_STATUS = "could not check"

# Status codes where every remaining call is guaranteed to fail the same
# way (bad/expired key, no access, wrong model id) — not worth flagging one
# batch at a time, the whole run should stop with one clear message.
FATAL_STATUS_CODES = {400, 401, 403, 404}


class FatalAPIError(Exception):
    """Raised instead of returning needs-review placeholders when the error
    means the run itself can't succeed, not just this one batch."""

SYSTEM_PROMPT = """
You are a product lifecycle status verification agent for a professional broadcast equipment database.

VALID STATUS VALUES (only three accepted):
- supported    → currently sold OR under active manufacturer support
- discontinued → manufacturer stopped producing/selling it, OR ended all support / EOL
- unknown      → status genuinely cannot be determined from available online sources

IDENTIFYING THE PRODUCT:
Some products have no model number. If Model is blank, search using the manufacturer and
Product Name instead — a missing model number is not by itself a reason to return unknown.

VERIFICATION STRATEGY — TWO TIER:

TIER 1 — URL CHECK FIRST (when product_url is provided):
Search for or visit the product_url directly. Look for clear signals:
  CLEAR SUPPORTED: active product page, pricing shown, add to cart, current specs → supported, no further search needed.
  CLEAR DISCONTINUED: 404, "discontinued", "replaced by", "no longer available", "end of life" → discontinued, no further search needed.
  AMBIGUOUS: page loads but no clear status, generic page, no pricing → proceed to Tier 2.

TIER 2 — FULL SEARCH (when URL is ambiguous, missing, or Tier 1 inconclusive):
1. Search the manufacturer's official website for the model number, or the product name if no
   model number was given.
2. Look for official EOL / discontinuation announcements.
3. Check authorised resellers (B&H Photo, Markertek, Sweetwater, Adorama, CDW).
   Active new/in-stock listing at authorised dealer = supported.
4. Do NOT count eBay or secondhand marketplaces as evidence of supported.

KEY RULES:
- EOL / end of life / end of sale / no longer available / discontinued → all map to: discontinued
- Product with active successor = the old model is still discontinued
- Acquired manufacturer: check if acquiring company explicitly continues support
- Never guess — if inconclusive after both tiers, return unknown
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


def load_cfg() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_cfg(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def load_df(path: str) -> pd.DataFrame:
    """Load a product CSV, trying multiple encodings."""
    for enc in ["utf-8", "latin-1", "cp1252", "iso-8859-1"]:
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not read file — check its encoding")


def parse_response(response) -> str:
    """Extract text from Claude response content blocks."""
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
    return text


def extract_json(raw: str):
    """Robustly extract a JSON array from Claude's response."""
    raw = raw.strip().replace("```json", "").replace("```", "").strip()
    raw = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", raw)
    s, e = raw.find("["), raw.rfind("]")
    if s == -1 or e == -1:
        raise ValueError("No JSON array found in response")
    return json.loads(raw[s:e + 1])


def _hex_fill(h):
    return PatternFill("solid", fgColor=h.lstrip("#"))


def _thin_border():
    side = Side(style="thin", color="DDDDDD")
    return Border(left=side, right=side, top=side, bottom=side)


def save_excel(results: list, output_path: str) -> None:
    """Write results to a formatted Excel workbook with a Results + Summary sheet."""
    cols = ["Source File", "Manufacturer", "Model", "Product Name",
            "DB Status", "Verified Status", "Match", "Suggested Status", "Reason"]
    df = pd.DataFrame(results)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df[cols].to_excel(output_path, index=False, sheet_name="Results")

    wb = load_workbook(output_path)
    ws = wb["Results"]

    widths = [18, 20, 24, 44, 14, 16, 16, 16, 55]
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
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
        return _ensure_mismatch_reason(extract_json(parse_response(resp)))

    except (ValueError, json.JSONDecodeError):
        log_callback("Malformed response from the model — flagging batch for human review", "warn")
        return _needs_review(batch_records, "Model returned an unparseable response")

    except anthropic.RateLimitError:
        log_callback("Rate limited — flagging batch for human review", "warn")
        return _needs_review(batch_records, "Rate limited by the API")

    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            log_callback("API overloaded (529) — flagging batch for human review", "warn")
            return _needs_review(batch_records, "API was overloaded (529)")
        if e.status_code in FATAL_STATUS_CODES:
            raise FatalAPIError(f"API error {e.status_code} — check your API key and model selection") from e
        log_callback(f"API error ({e.status_code}) — flagging batch for human review", "bad")
        return _needs_review(batch_records, f"API error ({e.status_code})")

    except Exception as e:
        log_callback(f"Unexpected error: {str(e)[:100]} — flagging batch for human review", "bad")
        return _needs_review(batch_records, f"Unexpected error: {str(e)[:100]}")


def check_file(path: str, client: anthropic.Anthropic, model: str, batch_size: int,
                batch_pause: int, skip_keys: set, progress_callback, log_callback,
                status_callback):
    """
    Verify all rows of one CSV against Claude. Returns
    (file_results, total_rows, skipped_already_verified_count).

    progress_callback(pct, done_batches, total_batches) and log_callback(msg, tag)
    follow the same shape as the Link Checker's core.check_file callbacks.
    """
    df = load_df(path)

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

    for bn, start in enumerate(range(0, total_searchable, batch_size), 1):
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
        file_results.extend(results)

        needs_review = sum(1 for r in results if r.get("Verified Status") == NEEDS_REVIEW_STATUS)
        if needs_review:
            log_callback(f"{len(results) - needs_review} verified, {needs_review} flagged for human review", "warn")
        elif results:
            log_callback(f"{len(results)} results received", "ok")

        progress_callback(int(bn / num_batches * 100), bn, num_batches)

        if bn < num_batches:
            for s in range(batch_pause, 0, -1):
                status_callback(f"Pausing {s}s before next batch...")
                time.sleep(1)

    lost = total - len(file_results)
    if lost:
        log_callback(f"{lost} products lost to API errors — re-run with recovery mode on to pick them up", "warn")

    return file_results, total, skipped

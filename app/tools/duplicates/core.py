"""
Duplicate Detection engine — pure Python, no UI dependency, no API.

Finds three kinds of duplicate pairs across every row of the loaded files:
  Exact Duplicate — same manufacturer and model (after normalising)
  Cross-Brand     — same model under different manufacturers
  Near Match      — similar model/manufacturer: 0.75 × model similarity +
                    0.25 × manufacturer similarity ≥ threshold
Rows with a blank model are never compared.

The original compared every pair with SequenceMatcher (O(n²) slow ratios).
Results are identical here, but exact/cross-brand pairs come from grouping,
and near matches only run ratio() on model pairs whose cheap upper bound
(shared-character count, the same bound as quick_ratio) can still reach the
threshold — vectorised with numpy over the unique model strings.

Each row's result carries the pairs it's in under "pairs", so the full
pair list is never lost: pairs_from_results() rebuilds it for the report.
"""

import re
from difflib import SequenceMatcher

import numpy as np
from openpyxl import Workbook

from tools.common import excel
from tools.common.data import ROW_COL, SOURCE_COL, Columns

NAME = "Duplicate Detection"
DEFAULT_THRESHOLD = 0.85
LIKELY_SCORE = 0.92

EXACT, CROSS, NEAR = "Exact Duplicate", "Cross-Brand", "Near Match"
# Most severe first — a row in several pairs reports the worst type.
SEVERITY = [EXACT, CROSS, NEAR]

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def normalise(s: str) -> str:
    """Lowercase and drop everything that isn't a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def missing_columns(df) -> list:
    cols = Columns(df)
    return [name for name, col in (("mfr", cols.mfr), ("model", cols.model)) if col is None]


def _pair(r1, r2, dup_type, confidence, score, note) -> dict:
    return {"row1": r1, "row2": r2, "type": dup_type, "confidence": confidence,
            "score": score, "note": note}


def _candidate_models(models: list, need: float, progress=None) -> list:
    """Index pairs (a, b) of distinct unique models whose similarity could
    reach `need`. Uses 2·shared_chars/(len_a+len_b), an upper bound on
    SequenceMatcher.ratio(), so no qualifying pair is ever skipped."""
    n = len(models)
    if n < 2:
        return []
    counts = np.zeros((n, len(_ALPHABET)), dtype=np.int16)
    idx = {c: i for i, c in enumerate(_ALPHABET)}
    for r, m in enumerate(models):
        for ch in m:
            counts[r, idx[ch]] += 1
    lengths = counts.sum(axis=1).astype(np.float64)

    out = []
    step = max(1, n // 50)
    for i in range(n - 1):
        shared = np.minimum(counts[i + 1:], counts[i]).sum(axis=1)
        bound = 2.0 * shared / (lengths[i] + lengths[i + 1:])
        for j in np.nonzero(bound >= need - 1e-9)[0]:
            out.append((i, i + 1 + int(j)))
        if progress and i % step == 0:
            progress(i, n)
    return out


def find_pairs(df, threshold: float = DEFAULT_THRESHOLD, progress=None) -> list:
    """All duplicate pairs in df, ordered by (row1, row2) with row1 < row2.
    progress(done, total) is called as the near-match scan advances."""
    cols = Columns(df)
    rows = []  # (row id, norm mfr, norm model)
    for _, row in df.iterrows():
        p = cols.product(row)
        nmod = normalise(p["model"])
        if nmod:
            rows.append((row[ROW_COL], normalise(p["mfr"]), nmod))

    pairs = []
    by_model = {}
    for rid, nm, nmod in rows:
        by_model.setdefault(nmod, []).append((rid, nm))

    # Exact / cross-brand: every pair of rows sharing a normalised model.
    for members in by_model.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                (r1, m1), (r2, m2) = sorted((members[a], members[b]))
                if m1 == m2:
                    pairs.append(_pair(r1, r2, EXACT, "Definite", 1.0,
                                       "Identical manufacturer and model number."))
                else:
                    pairs.append(_pair(r1, r2, CROSS, "Review", 1.0,
                                       "Identical model number under different manufacturers. "
                                       "May be a mfr spelling error or a legitimately dual-branded product."))

    # Near matches: only between different models. Manufacturer similarity
    # is at most 1, so the model must reach (threshold - 0.25) / 0.75.
    models = list(by_model)
    need = max(0.0, (threshold - 0.25) / 0.75)
    sim_cache = {}

    def sim(a, b):
        key = (a, b)
        if key not in sim_cache:
            sim_cache[key] = similarity(a, b)
        return sim_cache[key]

    for i, j in _candidate_models(models, need, progress):
        for r1, m1 in by_model[models[i]]:
            for r2, m2 in by_model[models[j]]:
                # Same argument order as the original (earlier row first) —
                # SequenceMatcher isn't always symmetric.
                (ra, ma, da), (rb, mb, db) = sorted(((r1, m1, models[i]), (r2, m2, models[j])))
                model_sim = sim(da, db)
                if model_sim * 0.75 + 0.25 < threshold:
                    continue
                mfr_sim = sim(ma, mb)
                combined = model_sim * 0.75 + mfr_sim * 0.25
                if combined >= threshold:
                    pairs.append(_pair(
                        ra, rb, NEAR, "Likely" if combined >= LIKELY_SCORE else "Possible",
                        round(combined, 3),
                        f"Model similarity: {round(model_sim * 100)}%  |  "
                        f"Manufacturer similarity: {round(mfr_sim * 100)}%"))
    if progress:
        progress(1, 1)

    pairs.sort(key=lambda p: (p["row1"], p["row2"]))
    return pairs


def _row_labels(df) -> dict:
    """'file.csv row N' for every row — N is the 1-based data row within
    its own source file (the header isn't counted)."""
    pos = df.groupby(SOURCE_COL).cumcount() + 1 if SOURCE_COL in df.columns else df.index.to_series() + 1
    return {rid: f"{src} row {n}" for rid, src, n in
            zip(df[ROW_COL], df.get(SOURCE_COL, [""] * len(df)), pos)}


def run(df, log, progress=None, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """{row: result} for every row. Each result has qa_status (the most
    severe pair type, or Pass), qa_note listing every row it pairs with,
    and "pairs" (the pair dicts it's part of)."""
    pairs = find_pairs(df, threshold, progress)
    labels = _row_labels(df)

    results = {rid: {"pairs": []} for rid in df[ROW_COL]}
    for p in pairs:
        results[p["row1"]]["pairs"].append(p)
        results[p["row2"]]["pairs"].append(p)

    for rid, r in results.items():
        if not r["pairs"]:
            r["qa_status"], r["qa_note"] = "Pass", ""
            continue
        r["qa_status"] = min((p["type"] for p in r["pairs"]), key=SEVERITY.index)
        r["qa_note"] = "; ".join(
            f"{p['type']} ({p['confidence']}, {round(p['score'] * 100)}%) with "
            f"{labels[p['row2'] if p['row1'] == rid else p['row1']]}"
            for p in r["pairs"])

    flagged = sum(1 for r in results.values() if r["qa_status"] != "Pass")
    log(f"{len(pairs)} duplicate pair(s) found, involving {flagged} product(s)",
        "warn" if pairs else "ok")
    return results


def pairs_from_results(results: dict) -> list:
    seen, out = set(), []
    for r in results.values():
        for p in r.get("pairs", []):
            key = (p["row1"], p["row2"])
            if key not in seen:
                seen.add(key)
                out.append(p)
    out.sort(key=lambda p: (p["row1"], p["row2"]))
    return out


def summarize(results: dict, pairs: list = None) -> list:
    pairs = pairs_from_results(results) if pairs is None else pairs
    by = lambda field, value: sum(1 for p in pairs if p[field] == value)
    rows = lambda status: sum(1 for r in results.values() if r["qa_status"] == status)
    return [
        ("Total products checked", len(results), None),
        ("Products in at least one pair", sum(1 for r in results.values() if r["qa_status"] != "Pass"), excel.WARN),
        ("Total duplicate pairs found", len(pairs), None),
        ("", "", None),
        ("Pairs by type:", "", None),
        ("  Exact Duplicates", by("type", EXACT), excel.BAD),
        ("  Cross-Brand", by("type", CROSS), excel.WARN),
        ("  Near Matches", by("type", NEAR), excel.WARN),
        ("", "", None),
        ("Pairs by confidence:", "", None),
        ("  Definite", by("confidence", "Definite"), excel.BAD),
        ("  Likely", by("confidence", "Likely"), excel.WARN),
        ("  Possible", by("confidence", "Possible"), excel.WARN),
        ("  Review", by("confidence", "Review"), excel.INFO),
        ("", "", None),
        ("Products by worst match:", "", None),
        ("  Pass", rows("Pass"), excel.GOOD),
    ]


def write_pairs_sheet(wb: Workbook, df, pairs: list, title: str = "Duplicate Pairs") -> None:
    cols = Columns(df)
    info = {}
    for _, row in df.iterrows():
        info[row[ROW_COL]] = {**cols.product(row), "src": row.get(SOURCE_COL, "")}
    labels = _row_labels(df)

    columns = ["Type", "Confidence", "Similarity", "Note",
               "Row 1", "MFR 1", "Model 1", "Product Name 1",
               "Row 2", "MFR 2", "Model 2", "Product Name 2"]
    rows = []
    for p in pairs:
        a, b = info[p["row1"]], info[p["row2"]]
        rows.append({
            "Type": p["type"], "Confidence": p["confidence"],
            "Similarity": f"{round(p['score'] * 100)}%", "Note": p["note"],
            "Row 1": labels[p["row1"]], "MFR 1": a["mfr"], "Model 1": a["model"],
            "Product Name 1": a["product_name"],
            "Row 2": labels[p["row2"]], "MFR 2": b["mfr"], "Model 2": b["model"],
            "Product Name 2": b["product_name"],
        })
    excel.write_sheet(wb, title, columns, rows,
                      widths=[16, 12, 11, 48, 22, 18, 20, 38, 22, 18, 20, 38],
                      status_cols=["Type", "Confidence"], row_height=40,
                      rules=[("exact", excel.BAD), ("definite", excel.BAD), ("cross", excel.WARN),
                             ("near", excel.WARN), ("likely", excel.WARN), ("possible", excel.WARN),
                             ("review", excel.INFO)])


def save_excel(df, results: dict, path: str, pairs: list = None) -> None:
    pairs = pairs_from_results(results) if pairs is None else pairs
    cols = Columns(df)
    columns = ["Source File", "Manufacturer", "Model", "Product Name", "Duplicate Status", "Matches"]
    rows = []
    for _, row in df.iterrows():
        r = results.get(row[ROW_COL])
        if r is None:
            continue
        p = cols.product(row)
        rows.append({"Source File": row.get(SOURCE_COL, ""), "Manufacturer": p["mfr"],
                     "Model": p["model"], "Product Name": p["product_name"],
                     "Duplicate Status": r["qa_status"], "Matches": r["qa_note"]})
    wb = Workbook()
    excel.write_sheet(wb, "Products", columns, rows, widths=[18, 20, 24, 44, 16, 70],
                      status_cols=["Duplicate Status"], row_height=30)
    write_pairs_sheet(wb, df, pairs)
    excel.write_summary(wb, "Summary", summarize(results, pairs), heading=f"{NAME} — Summary")
    wb.save(path)

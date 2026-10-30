"""
Full QA Pipeline engine — pure Python, no UI dependency.

Runs the selected QA tools in order on one combined DataFrame (every tool's
core.py follows the same engine contract — see product_name_qa/core.py),
merges their per-row results, scores each product's concern level and
writes one Master QA Report. Results are merged by row, never by
mfr/model, so duplicate products can't overwrite each other's findings.
"""

import importlib
from dataclasses import dataclass

from openpyxl import Workbook

from tools.common import claude, excel
from tools.common.data import ROW_COL, SOURCE_COL, Columns


@dataclass
class Stage:
    key: str
    label: str
    module: str
    uses_api: bool
    uses_search: bool = False


STAGES = [
    Stage("duplicates", "Duplicates", "tools.duplicates.core", uses_api=False),
    Stage("oem_url", "OEM URL", "tools.oem_url.core", uses_api=False),
    Stage("links", "Links", "tools.link_checker.core", uses_api=False),
    Stage("name_qa", "Product Name", "tools.product_name_qa.core", uses_api=True),
    Stage("status", "Status", "tools.status_checker.core", uses_api=True, uses_search=True),
    Stage("firmware", "Firmware & Docs", "tools.firmware_docs.core", uses_api=True, uses_search=True),
    Stage("warranty", "Warranty", "tools.warranty.core", uses_api=True, uses_search=True),
    Stage("dimensions", "Dimensions", "tools.dimensions.core", uses_api=True, uses_search=True),
]

CONCERN_ORDER = ["High", "Medium", "Low", "Pass"]
CONCERN_COLORS = {"High": excel.BAD, "Medium": excel.WARN, "Low": excel.INFO, "Pass": excel.GOOD}


def concern(status) -> str:
    """Map one stage's qa_status to a concern level."""
    v = str(status or "").lower().strip()
    if any(k in v for k in ("fail", "broken", "exact duplicate", "cross-brand")):
        return "High"
    if any(k in v for k in ("flag", "mismatch", "near match", "warning", "review")):
        return "Medium"
    if any(k in v for k in ("unknown", "error")):
        return "Low"
    return "Pass"


def run_pipeline(df, stage_keys, *, client=None, model="", batch_size=5, batch_pause=10,
                 log, progress=None, status=None, options=None, stop=None):
    """
    Returns (stage_results, stage_notes): stage_results maps stage key to
    {row: result}; stage_notes maps stage key to a one-line outcome
    ("12 issues", "skipped — missing columns: ...", "error: ...").
    progress(fraction 0..1) covers the whole pipeline. When `stop` (a
    threading.Event) is set, the running stage finishes its current batch
    and every later stage is skipped — the report still gets written.
    """
    options = options or {}
    chosen = [s for s in STAGES if s.key in stage_keys]
    stage_results, stage_notes = {}, {}
    api_stopped = None

    for i, stage in enumerate(chosen):
        share = 1 / len(chosen)

        def stage_progress(done, total, i=i, share=share):
            if progress:
                progress(i * share + share * done / max(total, 1))

        log(f"── [{i + 1}/{len(chosen)}] {stage.label} ──", "info")
        if status:
            status(f"Stage {i + 1}/{len(chosen)}: {stage.label}")

        if stop is not None and stop.is_set():
            stage_notes[stage.key] = "skipped — run stopped"
            log("Skipped — run stopped", "warn")
            continue

        if stage.uses_api and api_stopped:
            stage_notes[stage.key] = f"skipped — {api_stopped}"
            log(f"Skipped — {api_stopped}", "warn")
            continue

        mod = importlib.import_module(stage.module)
        missing = mod.missing_columns(df)
        if missing:
            stage_notes[stage.key] = f"skipped — missing columns: {', '.join(missing)}"
            log(f"Skipped — file has no {', '.join(missing)} column", "dim")
            continue

        try:
            if stage.uses_api:
                res = mod.run(df, client, model, batch_size, batch_pause, log,
                              progress=stage_progress, status=status, stop=stop)
            elif stage.key == "duplicates":
                res = mod.run(df, log, progress=stage_progress,
                              threshold=options.get("dup_threshold", 0.85), stop=stop)
            else:
                res = mod.run(df, log, progress=stage_progress, stop=stop)
        except claude.FatalAPIError as ex:
            api_stopped = str(ex)
            stage_notes[stage.key] = f"stopped — {ex}"
            log(f"Stopping AI checks — {ex}", "bad")
            continue
        except Exception as ex:
            stage_notes[stage.key] = f"error: {str(ex)[:120]}"
            log(f"Error in {stage.label}: {ex}", "bad")
            continue

        stage_results[stage.key] = res
        issues = sum(1 for r in res.values() if concern(r.get("qa_status")) != "Pass")
        stage_notes[stage.key] = f"{issues} issue(s)"
        log(f"{stage.label}: {issues} product(s) flagged", "warn" if issues else "ok")
        stage_progress(1, 1)

    return stage_results, stage_notes


def build_master_rows(df, stage_results) -> list:
    """One merged row per product, sorted by concern level."""
    cols = Columns(df)
    file_row = df.groupby(SOURCE_COL).cumcount() + 1
    rows = []
    for idx, row in df.iterrows():
        rn = row[ROW_COL]
        p = cols.product(row)
        merged = {"Manufacturer": p["mfr"], "Model": p["model"], "Product Name": p["product_name"],
                  "Source File": row.get(SOURCE_COL, ""), "File Row": int(file_row[idx]),
                  **cols.refs(row)}
        levels, summary = [], []
        for stage in STAGES:
            if stage.key not in stage_results:
                continue
            r = stage_results[stage.key].get(rn, {})
            st, note = r.get("qa_status", ""), r.get("qa_note", "")
            merged[stage.label] = st
            merged[f"{stage.label} Note"] = note
            lvl = concern(st)
            levels.append(lvl)
            if lvl != "Pass":
                summary.append(f"{stage.label}: {st}")
        merged["Concern Level"] = next((c for c in CONCERN_ORDER if c in levels), "Pass")
        merged["Concern Summary"] = " | ".join(summary) or "No issues found"
        rows.append(merged)
    rows.sort(key=lambda r: CONCERN_ORDER.index(r["Concern Level"]))
    return rows


def save_master_report(df, stage_results, stage_notes, path: str) -> list:
    """Write the Master QA Report; returns the master rows."""
    master = build_master_rows(df, stage_results)
    ran = [s for s in STAGES if s.key in stage_results]

    columns = ["Manufacturer", "Model", "Product Name", "Source File", "File Row", "Product ID",
               "Concern Level", "Concern Summary"]
    widths = [18, 20, 38, 18, 8, 38, 13, 55]
    for s in ran:
        columns += [s.label, f"{s.label} Note"]
        widths += [14, 45]
    columns.append("Beam Insights URL")
    widths.append(45)

    wb = Workbook()
    excel.write_sheet(wb, "Master QA", columns, master, widths=widths,
                      status_cols=["Concern Level"] + [s.label for s in ran],
                      rules=[("high", excel.BAD), ("medium", excel.WARN), ("low", excel.INFO)]
                      + excel.DEFAULT_STATUS_RULES, row_height=48)

    counts = {c: sum(1 for r in master if r["Concern Level"] == c) for c in CONCERN_ORDER}
    items = [
        ("Total products checked", len(master), None),
        ("", "", None),
        ("By concern level:", "", None),
        ("  High — immediate action needed", counts["High"], excel.BAD),
        ("  Medium — review recommended", counts["Medium"], excel.WARN),
        ("  Low — verify when possible", counts["Low"], excel.INFO),
        ("  Pass — no issues found", counts["Pass"], excel.GOOD),
        ("", "", None),
        ("By check (products flagged):", "", None),
    ]
    for s in STAGES:
        if s.key in stage_results:
            n = sum(1 for r in stage_results[s.key].values() if concern(r.get("qa_status")) != "Pass")
            items.append((f"  {s.label}", n, excel.WARN if n else excel.GOOD))
        elif s.key in stage_notes:
            items.append((f"  {s.label} — {stage_notes[s.key]}", "-", excel.INFO))
    excel.write_summary(wb, "Summary", items, heading="Beam Dynamics QA Suite — Master Report")

    if "duplicates" in stage_results:
        dup = importlib.import_module("tools.duplicates.core")
        pairs = dup.pairs_from_results(stage_results["duplicates"])
        if pairs:
            dup.write_pairs_sheet(wb, df, pairs)

    wb.save(path)
    return master

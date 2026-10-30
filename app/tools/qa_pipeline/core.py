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
from . import todo


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
        issues = sum(1 for r in res.values() if todo.items_for(stage.key, r))
        stage_notes[stage.key] = f"{issues} issue(s)"
        log(f"{stage.label}: {issues} product(s) flagged", "warn" if issues else "ok")
        stage_progress(1, 1)

    return stage_results, stage_notes


def build_master_rows(df, stage_results) -> list:
    """One merged row per product with every check's status and note,
    sorted by concern level. A product's concern level is its highest
    to-do priority, so the To-Do and All Products sheets always agree.
    Each row keeps its "_row" id (not written to the sheet)."""
    cols = Columns(df)
    file_row = df.groupby(SOURCE_COL).cumcount() + 1
    rows = []
    for idx, row in df.iterrows():
        rn = row[ROW_COL]
        p = cols.product(row)
        merged = {"_row": rn, "Manufacturer": p["mfr"], "Model": p["model"],
                  "Product Name": p["product_name"], "Source File": row.get(SOURCE_COL, ""),
                  "File Row": int(file_row[idx]), **cols.refs(row)}
        levels, problems = [], []
        for stage in STAGES:
            if stage.key not in stage_results:
                continue
            r = stage_results[stage.key].get(rn, {})
            merged[stage.label] = r.get("qa_status", "")
            merged[f"{stage.label} Note"] = r.get("qa_note", "")
            for priority, problem, _rule, _fix in todo.items_for(stage.key, r):
                levels.append(priority)
                problems.append(problem)
        merged["Concern Level"] = next((c for c in todo.PRIORITY_ORDER if c in levels), "Pass")
        merged["Concern Summary"] = " | ".join(problems) or "No issues found"
        rows.append(merged)
    rows.sort(key=lambda r: CONCERN_ORDER.index(r["Concern Level"]))
    return rows


def save_master_report(df, stage_results, stage_notes, path: str) -> list:
    """Write the Master QA Report — To-Do list first, then every product in
    detail — and return the master rows."""
    master = build_master_rows(df, stage_results)
    ran = [s for s in STAGES if s.key in stage_results]
    todo_rows = todo.build_rows(master, stage_results, ran)
    priority_rules = [("high", excel.BAD), ("medium", excel.WARN), ("low", excel.INFO)]

    wb = Workbook()
    excel.write_sheet(wb, "To-Do", todo.TODO_COLS, todo_rows,
                      widths=[9, 16, 20, 34, 14, 36, 40, 55, 45, 38, 16, 8],
                      status_cols=["Priority"], rules=priority_rules, row_height=40)

    counts = {c: sum(1 for r in todo_rows if r["Priority"] == c) for c in todo.PRIORITY_ORDER}
    products = {c: sum(1 for r in master if r["Concern Level"] == c) for c in CONCERN_ORDER}
    items = [
        ("Products checked", len(master), None),
        ("Products with no problems", products["Pass"], excel.GOOD),
        ("Products with something to fix", len(master) - products["Pass"], excel.WARN),
        ("", "", None),
        ("To-do items by priority:", "", None),
        ("  High — fix first", counts["High"], excel.BAD),
        ("  Medium — fix soon", counts["Medium"], excel.WARN),
        ("  Low — needs a person to look", counts["Low"], excel.INFO),
        ("", "", None),
        ("To-do items by check:", "", None),
    ]
    for s in STAGES:
        if s.key in stage_results:
            n = sum(1 for r in todo_rows if r["Check"] == s.label)
            items.append((f"  {s.label}", n, excel.WARN if n else excel.GOOD))
        elif s.key in stage_notes:
            items.append((f"  {s.label} — {stage_notes[s.key]}", "-", excel.INFO))
    excel.write_summary(wb, "Summary", items, heading="Beam Dynamics QA — Summary")

    columns = ["Manufacturer", "Model", "Product Name", "Source File", "File Row", "Product ID",
               "Concern Level", "Concern Summary"]
    widths = [18, 20, 38, 18, 8, 38, 13, 55]
    for s in ran:
        columns += [s.label, f"{s.label} Note"]
        widths += [14, 45]
    columns.append("Beam Insights URL")
    widths.append(45)
    excel.write_sheet(wb, "All Products", columns, master, widths=widths,
                      status_cols=["Concern Level"] + [s.label for s in ran],
                      rules=priority_rules + excel.DEFAULT_STATUS_RULES, row_height=48)

    if "duplicates" in stage_results:
        dup = importlib.import_module("tools.duplicates.core")
        pairs = dup.pairs_from_results(stage_results["duplicates"])
        if pairs:
            dup.write_pairs_sheet(wb, df, pairs)

    wb.save(path)
    return master



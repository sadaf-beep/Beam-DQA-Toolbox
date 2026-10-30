"""Flet UI for the Dimensions Validation tool."""

import os
from datetime import datetime

import flet as ft

import theme
from tools.common import claude, data, ui
from . import core


def _count(pred):
    return lambda results: sum(1 for r in results if pred(r["qa_status"].lower()))


def run(ctx: ui.RunContext):
    df = data.load_files(ctx.files, ctx.log)
    missing = core.missing_columns(df)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    results = core.run(df, ctx.client, ctx.model, ctx.batch_size, ctx.batch_pause, ctx.log,
                       progress=lambda done, total: ctx.set_progress(done / total),
                       status=ctx.set_status, stop=ctx.stop)
    out_path = os.path.join(ctx.out_dir, f"Dimensions_Validation_{datetime.now():%Y%m%d_%H%M}.xlsx")
    core.save_excel(df, results, out_path)
    values = list(results.values())
    issues = _count(lambda s: s.startswith(("fail", "flag")))(values)
    return values, out_path, f"{len(values)} products checked, {issues} issue(s) to fix."


def build(page: ft.Page) -> ft.Control:
    return ui.tool_screen(
        page,
        run_label="Run Dimensions Check",
        caption="Needs mfr, model and product_dimension and/or package_dimensions columns "
                "(Page URL or product_url optional; unitless Studio JSON is supported). Checks unit consistency, fills gaps and compares against "
                "manufacturer specs using Claude with web search.",
        needs_api=True,
        default_batch="5", default_pause="10s",
        stats=[
            ui.Stat("Total checked", theme.TEXT, len),
            ui.Stat("Pass", theme.GOOD, _count(lambda s: s.startswith("pass"))),
            ui.Stat("Fail", theme.BAD, _count(lambda s: s.startswith("fail"))),
            ui.Stat("Missing data", theme.WARN, _count(lambda s: "missing" in s)),
            ui.Stat("Warning / unknown", theme.WARN, _count(lambda s: s.startswith("warning") or s == "unknown")),
            ui.Stat("Needs review", theme.WARN, _count(lambda s: s == claude.NEEDS_REVIEW.lower())),
        ],
        run=run,
    )

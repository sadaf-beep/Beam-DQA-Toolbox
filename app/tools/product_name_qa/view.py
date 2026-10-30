"""Flet UI for the Product Name QA tool."""

import os
from datetime import datetime

import flet as ft

import theme
from tools.common import claude, data, ui
from . import core


def _count(status):
    return lambda results: sum(1 for r in results if r["qa_status"].lower() == status.lower())


def run(ctx: ui.RunContext):
    df = data.load_files(ctx.files, ctx.log)
    missing = core.missing_columns(df)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    results = core.run(df, ctx.client, ctx.model, ctx.batch_size, ctx.batch_pause, ctx.log,
                       progress=lambda done, total: ctx.set_progress(done / total),
                       status=ctx.set_status)
    out_path = os.path.join(ctx.out_dir, f"Product_Name_QA_{datetime.now():%Y%m%d_%H%M}.xlsx")
    core.save_excel(df, results, out_path)
    values = list(results.values())
    fails = _count("fail")(values)
    return values, out_path, f"Done — {len(values)} products checked, {fails} fail(s)."


def build(page: ft.Page) -> ft.Control:
    return ui.tool_screen(
        page,
        run_label="Run Name QA",
        caption="Needs mfr and model columns (product name recommended). Checks that manufacturer, "
                "model and name are consistent using Claude's knowledge — no web search, so it's cheap.",
        needs_api=True,
        default_batch="10", default_pause="5s",
        stats=[
            ui.Stat("Total checked", theme.TEXT, len),
            ui.Stat("Pass", theme.GOOD, _count("pass")),
            ui.Stat("Warning", theme.WARN, _count("warning")),
            ui.Stat("Fail", theme.BAD, _count("fail")),
            ui.Stat("Needs review", theme.WARN, _count(claude.NEEDS_REVIEW)),
        ],
        run=run,
    )

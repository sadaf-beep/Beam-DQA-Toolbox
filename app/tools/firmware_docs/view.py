"""Flet UI for the Firmware & Docs tool."""

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
    out_path = os.path.join(ctx.out_dir, f"Firmware_Docs_{datetime.now():%Y%m%d_%H%M}.xlsx")
    core.save_excel(df, results, out_path)
    values = list(results.values())
    flags = _count("flag")(values)
    return values, out_path, f"Done — {len(values)} products checked, {flags} flagged."


def build(page: ft.Page) -> ft.Control:
    return ui.tool_screen(
        page,
        run_label="Run Firmware & Docs Check",
        caption="Needs mfr and model, plus at least one of has_firmware, Firmware Added, documents added "
                "(TRUE/FALSE). product_url is optional — it's checked first for downloads. Uses web search.",
        needs_api=True,
        default_batch="5", default_pause="10s",
        stats=[
            ui.Stat("Total checked", theme.TEXT, len),
            ui.Stat("Pass", theme.GOOD, _count("pass")),
            ui.Stat("Flag", theme.BAD, _count("flag")),
            ui.Stat("Warning", theme.WARN, _count("warning")),
            ui.Stat("Unknown", theme.INFO, _count("unknown")),
            ui.Stat("Needs review", theme.WARN, _count(claude.NEEDS_REVIEW)),
        ],
        run=run,
    )

"""Flet UI for the Warranty Validation tool."""

import os
from datetime import datetime

import flet as ft

import theme
from tools.common import claude, data, ui
from . import core


def _count(*statuses):
    wanted = {s.lower() for s in statuses}
    return lambda results: sum(1 for r in results if r["qa_status"].lower() in wanted)


def run(ctx: ui.RunContext):
    df = data.load_files(ctx.files, ctx.log)
    missing = core.missing_columns(df)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    results = core.run(df, ctx.client, ctx.model, ctx.batch_size, ctx.batch_pause, ctx.log,
                       progress=lambda done, total: ctx.set_progress(done / total),
                       status=ctx.set_status, stop=ctx.stop)
    out_path = os.path.join(ctx.out_dir, f"Warranty_Validation_{datetime.now():%Y%m%d_%H%M}.xlsx")
    core.save_excel(df, results, out_path)
    values = list(results.values())
    fails = _count("fail")(values)
    return values, out_path, f"{len(values)} products checked, {fails} warranty mismatch(es)."


def build(page: ft.Page) -> ft.Control:
    return ui.tool_screen(
        page,
        run_label="Run Warranty Check",
        caption="Needs mfr, model and warranty_length (in days; 36500 or -1 = lifetime)."
                " Page URL (or product_url) is optional — it's checked first for warranty "
                "terms. Uses web search.",
        needs_api=True,
        default_batch="5", default_pause="10s",
        stats=[
            ui.Stat("Total checked", theme.TEXT, len),
            ui.Stat("Pass", theme.GOOD, _count("pass", "pass (lifetime)")),
            ui.Stat("Fail", theme.BAD, _count("fail")),
            ui.Stat("Warning", theme.WARN, _count("warning")),
            ui.Stat("Unknown / Error", theme.INFO, _count("unknown", "error")),
            ui.Stat("Needs review", theme.WARN, _count(claude.NEEDS_REVIEW)),
        ],
        run=run,
    )

"""Flet UI for the OEM URL Check tool."""

import os
from datetime import datetime

import flet as ft

import theme
from tools.common import data, ui
from . import core


def _count(status):
    return lambda results: sum(1 for r in results if r["qa_status"] == status)


def run(ctx: ui.RunContext):
    df = data.load_files(ctx.files, ctx.log)
    missing = core.missing_columns(df)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    ctx.set_status("Checking URLs...")
    results = core.run(df, ctx.log, progress=lambda done, total: ctx.set_progress(done / max(total, 1)))
    out_path = os.path.join(ctx.out_dir, f"OEM_URL_Check_{datetime.now():%Y%m%d_%H%M}.xlsx")
    core.save_excel(df, results, out_path)
    values = list(results.values())
    fails = _count("Fail")(values)
    return values, out_path, f"Done — {len(values)} URLs checked, {fails} flag mismatch(es)."


def build(page: ft.Page) -> ft.Control:
    return ui.tool_screen(
        page,
        run_label="Check OEM URLs",
        caption="Needs a product_url column (mfr and is_oem_url recommended). Classifies each URL as the "
                "manufacturer's own site, a retailer or unknown, and checks it against is_oem_url — no API, "
                "no web requests.",
        stats=[
            ui.Stat("Total checked", theme.TEXT, len),
            ui.Stat("Pass", theme.GOOD, _count("Pass")),
            ui.Stat("Fail", theme.BAD, _count("Fail")),
            ui.Stat("Review", theme.WARN, _count("Review")),
            ui.Stat("Error", theme.INFO, _count("Error")),
        ],
        run=run,
    )

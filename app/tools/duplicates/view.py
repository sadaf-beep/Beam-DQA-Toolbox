"""Flet UI for the Duplicate Detection tool."""

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

    threshold = int(ctx.options["threshold"].replace("%", "")) / 100
    ctx.log(f"Comparing {len(df)} products (near-match threshold {int(threshold * 100)}%)...", "dim")
    ctx.set_status("Comparing products...")
    results = core.run(df, ctx.log, progress=lambda done, total: ctx.set_progress(done / max(total, 1)),
                       threshold=threshold)
    out_path = os.path.join(ctx.out_dir, f"Duplicates_{datetime.now():%Y%m%d_%H%M}.xlsx")
    core.save_excel(df, results, out_path)
    values = list(results.values())
    pairs = len(core.pairs_from_results(results))
    return values, out_path, f"Done — {len(values)} products checked, {pairs} duplicate pair(s)."


def build(page: ft.Page) -> ft.Control:
    threshold_dd = ft.Dropdown(
        label="Near-match threshold", width=200, value="85%",
        options=[ft.dropdown.Option(v) for v in ["75%", "80%", "85%", "90%", "95%"]],
    )
    return ui.tool_screen(
        page,
        run_label="Find Duplicates",
        caption="Needs mfr and model columns. Finds exact duplicates, the same model under different "
                "manufacturers, and near matches across all files together — no API, runs locally.",
        extra_settings={"threshold": threshold_dd},
        stats=[
            ui.Stat("Total checked", theme.TEXT, len),
            ui.Stat("Exact duplicate", theme.BAD, _count(core.EXACT)),
            ui.Stat("Cross-brand", theme.WARN, _count(core.CROSS)),
            ui.Stat("Near match", theme.WARN, _count(core.NEAR)),
            ui.Stat("Pass", theme.GOOD, _count("Pass")),
        ],
        run=run,
    )

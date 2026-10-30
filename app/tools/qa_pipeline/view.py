"""Flet UI for the Full QA Pipeline."""

import os
from datetime import datetime

import flet as ft

import theme
from tools.common import data, ui
from . import core


def _level(level):
    return lambda rows: sum(1 for r in rows if r["Concern Level"] == level)


def build(page: ft.Page) -> ft.Control:
    stage_boxes = {
        s.key: ft.Checkbox(
            label=f"{s.label}" + (" (Claude + web search)" if s.uses_search
                                  else " (Claude)" if s.uses_api else " (free)"),
            value=True,
        )
        for s in core.STAGES
    }
    threshold_dd = ft.Dropdown(
        label="Duplicate similarity", width=180, value="85%",
        options=[ft.dropdown.Option(v) for v in ["75%", "80%", "85%", "90%", "95%"]],
    )

    def run(ctx: ui.RunContext):
        keys = [k for k in stage_boxes if ctx.options.get(f"stage_{k}")]
        if not keys:
            raise ValueError("No checks selected")
        df = data.load_files(ctx.files, ctx.log)
        ctx.log(f"{len(df)} products across {df[data.SOURCE_COL].nunique()} file(s)", "info")

        stage_results, stage_notes = core.run_pipeline(
            df, keys, client=ctx.client, model=ctx.model, batch_size=ctx.batch_size,
            batch_pause=ctx.batch_pause, log=ctx.log, progress=ctx.set_progress,
            status=ctx.set_status, stop=ctx.stop,
            options={"dup_threshold": int(ctx.options["dup_threshold"].rstrip("%")) / 100},
        )

        ctx.set_status("Building master report...")
        out_path = os.path.join(ctx.out_dir, f"Master_QA_Report_{datetime.now():%Y%m%d_%H%M}.xlsx")
        master = core.save_master_report(df, stage_results, stage_notes, out_path)
        high = _level("High")(master)
        return master, out_path, f"Pipeline complete — {len(master)} products, {high} high concern."

    return ui.tool_screen(
        page,
        run_label="Run Full QA Pipeline",
        caption="Runs every selected check on all files together and writes one Master QA Report "
                "sorted by concern level. Checks skip themselves when the files lack their columns "
                "(mfr, model, product name, Page URL, Image URL, is_oem_url, product_status, "
                "has_firmware, Firmware Added, documents added, warranty_length, product_dimension, "
                "package_dimensions). Test on a small file first — web-search checks cost the most.",
        needs_api=True,
        extra_settings={"dup_threshold": threshold_dd,
                        **{f"stage_{k}": box for k, box in stage_boxes.items()}},
        stats=[
            ui.Stat("Products", theme.TEXT, len),
            ui.Stat("High", theme.BAD, _level("High")),
            ui.Stat("Medium", theme.WARN, _level("Medium")),
            ui.Stat("Low", theme.INFO, _level("Low")),
            ui.Stat("Pass", theme.GOOD, _level("Pass")),
        ],
        run=run,
    )

"""Flet UI for the File Splitter tool."""

from pathlib import Path

import flet as ft

import theme
from tools.common import ui
from . import core


def run(ctx: ui.RunContext):
    rows_per_file = int(ctx.options["rows_per_file"])
    prefix = (ctx.options["prefix"] or "").strip()
    written = []
    total_files = len(ctx.files)
    for fi, path in enumerate(ctx.files, 1):
        # One shared prefix for several inputs would overwrite each other's
        # batches, so the input name is appended to keep them apart.
        file_prefix = prefix
        if prefix and total_files > 1:
            file_prefix = f"{prefix}_{Path(path).stem.replace(' ', '_')}_"
        ctx.set_status(f"Splitting file {fi}/{total_files}...")
        try:
            written += core.split_file(
                path, ctx.out_dir, rows_per_file, file_prefix, log=ctx.log,
                progress=lambda done, total, fi=fi: ctx.set_progress(((fi - 1) + done / total) / total_files),
            )
        except Exception as ex:
            ctx.log(f"Cannot split {Path(path).name}: {ex}", "bad")
        ctx.show_results(written)
    if not written:
        raise ValueError("No files could be split")
    rows = sum(w["rows"] for w in written)
    # Returning the folder lets "Open Results File" open the output folder.
    return written, ctx.out_dir, f"Done — {len(written)} file(s) written, {rows} rows."


def build(page: ft.Page) -> ft.Control:
    rows_dd = ft.Dropdown(
        label="Rows per file", width=160, value="50",
        options=[ft.dropdown.Option(v) for v in ["10", "25", "50", "100", "150", "200", "500", "1000"]],
    )
    prefix_field = ft.TextField(
        label="Output file prefix", width=240, hint_text="blank = input file name",
        helper_text="→ Prefix1.csv, Prefix2.csv ...",
    )
    return ui.tool_screen(
        page,
        run_label="Split Files",
        caption="Splits large CSV/Excel files into smaller CSV batches (each keeps the header row). "
                "No API key needed.",
        extra_settings={"rows_per_file": rows_dd, "prefix": prefix_field},
        stats=[
            ui.Stat("Files written", theme.TEXT, len),
            ui.Stat("Rows", theme.GOOD, lambda written: sum(w["rows"] for w in written)),
        ],
        run=run,
    )

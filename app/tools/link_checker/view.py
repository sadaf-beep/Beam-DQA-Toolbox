"""Flet UI for the Link Checker tool."""

import os
import threading
from datetime import datetime

import flet as ft

import theme
from . import core


class FileRow:
    """One card in the file list — filename, info, progress bar, status."""

    def __init__(self, path: str, on_remove, on_change):
        self.path = path
        self.df = None
        self.error = None
        self.done = False
        self.broken_count = 0
        self.on_change = on_change

        self.name_text = ft.Text(
            os.path.basename(path), weight=ft.FontWeight.BOLD,
            color=theme.INFO, font_family=theme.MONO_FONT, size=13,
        )
        self.remove_btn = ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=16, icon_color=theme.MUTED,
            on_click=lambda e: on_remove(self),
        )
        self.info_text = ft.Text("Loading…", size=12, color=theme.MUTED)
        self.progress = ft.ProgressBar(value=0, color=theme.ACCENT, bgcolor=theme.BORDER)
        self.status_text = ft.Text(
            "Queued", size=12, color=theme.MUTED, font_family=theme.MONO_FONT,
        )

        self.container = theme.card_container(
            ft.Column(
                [
                    ft.Row([self.name_text, self.remove_btn],
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    self.info_text,
                    ft.Row([self.progress, self.status_text], spacing=12,
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ],
                spacing=6,
            ),
            margin=ft.Margin(0, 0, 0, 8),
        )

    def set_info(self, text, color=theme.MUTED):
        self.info_text.value = text
        self.info_text.color = color

    def set_status(self, text, color=theme.MUTED):
        self.status_text.value = text
        self.status_text.color = color

    def set_progress(self, pct):
        self.progress.value = pct / 100


def build(page: ft.Page) -> ft.Control:
    save_dir = {"path": None}
    rows: list[FileRow] = []
    running = {"value": False}

    session = core.new_session()
    executor = core.new_executor()

    file_list = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    placeholder = ft.Container(
        content=ft.Text(
            'No files added yet.\n\nClick "+ Add Files" to select CSV or Excel files.',
            color=theme.MUTED, text_align=ft.TextAlign.CENTER,
        ),
        alignment=ft.Alignment(0, 0),
        padding=40,
    )
    file_list.controls.append(placeholder)

    log_view = ft.ListView(spacing=2, height=140, auto_scroll=True)

    def stat_box(label, color):
        value_text = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color=color,
                              font_family=theme.MONO_FONT)
        box = theme.card_container(
            ft.Column(
                [ft.Text(label, size=10, color=theme.MUTED, font_family=theme.MONO_FONT),
                 value_text],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2,
            ),
            padding=10,
        )
        return box, value_text

    files_box, files_val = stat_box("FILES", theme.ACCENT)
    rows_box, rows_val = stat_box("TOTAL ROWS", theme.TEXT)
    broken_box, broken_val = stat_box("BROKEN LINKS", theme.BAD)
    done_box, done_val = stat_box("FILES DONE", theme.GOOD)

    save_label = ft.Text("No folder selected", size=12, color=theme.MUTED,
                          font_family=theme.MONO_FONT)
    run_button = ft.Button(
        "Step 3 — Run Checker", icon=ft.Icons.PLAY_ARROW,
        bgcolor=theme.ACCENT, color="white", disabled=True,
    )

    def log(msg, tag="info"):
        color = {"ok": theme.GOOD, "bad": theme.BAD, "info": theme.INFO,
                 "dim": theme.MUTED}.get(tag, theme.TEXT)
        log_view.controls.append(ft.Text(msg, size=11, color=color, font_family=theme.MONO_FONT))
        page.update()

    def refresh_summary():
        valid = [r for r in rows if r.df is not None]
        files_val.value = str(len(valid))
        rows_val.value = str(sum(len(r.df) for r in valid))
        done_val.value = str(sum(1 for r in valid if r.done))
        broken_val.value = str(sum(r.broken_count for r in valid))
        page.update()

    def check_ready():
        valid = any(r.df is not None for r in rows)
        run_button.disabled = not (valid and save_dir["path"] and not running["value"])
        page.update()

    def remove_row(row: FileRow):
        rows.remove(row)
        file_list.controls.remove(row.container)
        if not rows:
            file_list.controls.append(placeholder)
        refresh_summary()
        check_ready()

    async def add_files_click(e=None):
        picked = await file_picker.pick_files(
            allow_multiple=True,
            allowed_extensions=["csv", "xlsx", "xls"],
        )
        if not picked:
            return
        if placeholder in file_list.controls:
            file_list.controls.remove(placeholder)

        for f in picked:
            path = f.path
            if any(r.path == path for r in rows):
                continue
            row = FileRow(path, remove_row, refresh_summary)
            rows.append(row)
            file_list.controls.append(row.container)
            page.update()

            try:
                df = core.load_df(path)
                missing = [c for c in [core.IMAGE_COL, core.PRODUCT_COL] if c not in df.columns]
                if missing:
                    row.set_info(f'Missing columns: {", ".join(missing)}', theme.BAD)
                    row.set_status("Error", theme.BAD)
                else:
                    row.df = df
                    n_img = int(df[core.IMAGE_COL].notna().sum())
                    n_prod = int(df[core.PRODUCT_COL].notna().sum())
                    row.set_info(f"{len(df)} rows  •  {n_img} image URLs  •  {n_prod} product URLs", theme.GOOD)
                    row.set_status("Ready", theme.GOOD)
            except Exception as ex:
                row.set_info(f"Could not read: {str(ex)[:60]}", theme.BAD)
                row.set_status("Error", theme.BAD)

        refresh_summary()
        check_ready()
        page.update()

    async def choose_folder_click(e=None):
        path = await folder_picker.get_directory_path()
        if path:
            save_dir["path"] = path
            save_label.value = path
            save_label.color = theme.INFO
            check_ready()
            page.update()

    file_picker = ft.FilePicker()
    folder_picker = ft.FilePicker()
    page.overlay.extend([file_picker, folder_picker])

    def clear_all(e=None):
        rows.clear()
        file_list.controls.clear()
        file_list.controls.append(placeholder)
        log_view.controls.clear()
        refresh_summary()
        check_ready()
        page.update()

    def run_file(row: FileRow):
        fname = os.path.basename(row.path)
        log(f"  Checking: {fname}", "dim")

        def on_progress(pct, done, total):
            row.set_progress(pct)
            row.set_status(f"{done}/{total}  ({pct}%)", theme.ACCENT)
            page.update()

        def on_log(msg, tag):
            log(f"  {msg}", tag)

        try:
            broken_df, total_rows, broken_count = core.check_file(
                row.path, session, executor, on_progress, on_log)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            base_name = os.path.splitext(fname)[0]
            out_name = f"{base_name}_BROKEN_LINKS_{timestamp}.csv"
            out_path = os.path.join(save_dir["path"], out_name)
            broken_df.to_csv(out_path, index=False)

            row.broken_count = broken_count
            row.done = True
            if broken_count == 0:
                row.set_status("Done — 0 broken", theme.GOOD)
                log(f"  {fname} — No broken links found!", "ok")
            else:
                row.set_status(f"Done — {broken_count} broken", theme.BAD)
                log(f"  {fname} — {broken_count} broken links saved to: {os.path.basename(out_path)}", "bad")
            row.set_progress(100)

        except Exception as ex:
            row.error = str(ex)
            row.done = True
            row.set_status("Error", theme.BAD)
            row.set_progress(0)
            log(f"  {fname}: {ex}", "bad")

        refresh_summary()

        valid = [r for r in rows if r.df is not None]
        if all(r.done for r in valid):
            running["value"] = False
            check_ready()
            log(f"\nAll files complete! Results saved to: {save_dir['path']}\n", "ok")
            page.show_dialog(ft.SnackBar(ft.Text(f"All {len(valid)} file(s) checked — results in {save_dir['path']}")))
        page.update()

    def run_all(e=None):
        if running["value"]:
            return
        valid = [r for r in rows if r.df is not None]
        if not valid or not save_dir["path"]:
            return

        running["value"] = True
        run_button.disabled = True
        broken_val.value = "0"
        done_val.value = "0"
        log(f"\n▶ Starting check on {len(valid)} file(s)...\n", "info")
        page.update()

        for row in valid:
            row.done = False
            row.set_status("Running…", theme.ACCENT)
            row.set_progress(0)
            threading.Thread(target=run_file, args=(row,), daemon=True).start()
        page.update()

    run_button.on_click = run_all

    header = ft.Row(
        [
            ft.Text("Step 1 — Add your files", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            ft.Row(
                [
                    ft.Text("CSV or Excel  •  needs Image Url & product_url columns",
                            size=11, color=theme.MUTED),
                    ft.Button(
                        "+ Add Files", icon=ft.Icons.ADD, bgcolor=theme.ACCENT, color="white",
                        on_click=add_files_click,
                    ),
                ],
                spacing=12,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    summary_row = ft.Row([files_box, rows_box, broken_box, done_box], spacing=10)

    save_row = ft.Row(
        [
            ft.Text("Step 2 — Choose where to save results", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            save_label,
            ft.OutlinedButton(
                "Choose Folder", icon=ft.Icons.FOLDER_OPEN,
                on_click=choose_folder_click,
            ),
            ft.Container(expand=True),
            ft.OutlinedButton("Clear All", on_click=clear_all),
            run_button,
        ],
        spacing=10,
    )

    return ft.Container(
        content=ft.Column(
            [
                header,
                ft.Container(
                    content=file_list,
                    expand=True,
                    bgcolor=theme.BG,
                    padding=ft.Padding(0, 8, 0, 8),
                ),
                summary_row,
                save_row,
                theme.card_container(log_view, padding=8),
            ],
            spacing=14,
            expand=True,
        ),
        padding=20,
        expand=True,
    )

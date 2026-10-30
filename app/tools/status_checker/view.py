"""Flet UI for the Product Status Checker tool."""

import os
import threading
import time
from datetime import datetime

import anthropic
import flet as ft

import theme
from . import core


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build(page: ft.Page) -> ft.Control:
    cfg = core.load_cfg()
    out_dir = {"path": None}
    file_paths: list[str] = []
    running = {"value": False}
    result_path = {"path": None}

    # ── API key ─────────────────────────────────────────────
    api_key_field = ft.TextField(
        label="Anthropic API key", value=cfg.get("claude_api_key", ""),
        password=True, can_reveal_password=True, width=360,
        hint_text="sk-ant-...",
    )
    api_badge = ft.Text(
        "API key set" if cfg.get("claude_api_key") else "No API key",
        size=11, color=theme.GOOD if cfg.get("claude_api_key") else theme.BAD,
    )

    def save_key(e=None):
        key = api_key_field.value.strip()
        if not key.startswith("sk-ant-"):
            page.open(ft.SnackBar(ft.Text("Claude API keys start with 'sk-ant-' — check and try again.")))
            return
        cfg["claude_api_key"] = key
        core.save_cfg(cfg)
        api_badge.value = "API key set"
        api_badge.color = theme.GOOD
        page.update()

    # ── File list ───────────────────────────────────────────
    file_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=110)
    placeholder = ft.Container(
        content=ft.Text('No CSV files added yet. Click "+ Add Files" to select some.',
                         color=theme.MUTED, size=12),
        padding=10,
    )
    file_list.controls.append(placeholder)
    file_count_text = ft.Text("No files selected", size=11, color=theme.MUTED)

    def refresh_file_list():
        file_list.controls.clear()
        if file_paths:
            for p in file_paths:
                file_list.controls.append(
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.DESCRIPTION, size=14, color=theme.INFO),
                            ft.Text(os.path.basename(p), size=12, color=theme.TEXT,
                                    font_family=theme.MONO_FONT),
                            ft.IconButton(icon=ft.Icons.CLOSE, icon_size=14, icon_color=theme.MUTED,
                                          on_click=lambda e, path=p: remove_file(path)),
                        ],
                        spacing=6,
                    )
                )
        else:
            file_list.controls.append(placeholder)
        n = len(file_paths)
        file_count_text.value = f"{n} file{'s' if n != 1 else ''} selected" if n else "No files selected"
        page.update()

    def remove_file(path):
        if path in file_paths:
            file_paths.remove(path)
        refresh_file_list()
        check_ready()

    def on_files_picked(e: ft.FilePickerResultEvent):
        if not e.files:
            return
        for f in e.files:
            if f.path not in file_paths:
                file_paths.append(f.path)
        refresh_file_list()
        check_ready()

    def on_folder_picked(e: ft.FilePickerResultEvent):
        if e.path:
            out_dir["path"] = e.path
            out_label.value = e.path
            out_label.color = theme.INFO
            check_ready()
            page.update()

    file_picker = ft.FilePicker(on_result=on_files_picked)
    folder_picker = ft.FilePicker(on_result=on_folder_picked)
    page.overlay.extend([file_picker, folder_picker])

    def clear_files(e=None):
        file_paths.clear()
        refresh_file_list()
        check_ready()

    # ── Settings ────────────────────────────────────────────
    model_dd = ft.Dropdown(
        label="Model", width=240, value=core.DEFAULT_MODEL_LABEL,
        options=[ft.dropdown.Option(k) for k in core.MODELS],
    )
    batch_dd = ft.Dropdown(
        label="Products per call", width=160, value="3",
        options=[ft.dropdown.Option(v) for v in ["3", "5", "8", "10"]],
    )
    batch_pause_dd = ft.Dropdown(
        label="Pause between batches", width=180, value="15s",
        options=[ft.dropdown.Option(v) for v in ["5s", "10s", "15s", "20s", "30s"]],
    )
    recovery_cb = ft.Checkbox(
        label="Recovery mode — skip products already in the output file", value=False,
    )
    out_label = ft.Text("No output folder selected", size=12, color=theme.MUTED,
                         font_family=theme.MONO_FONT)

    # ── Stats + progress + log ──────────────────────────────
    def stat_box(label, color):
        value_text = ft.Text("0", size=20, weight=ft.FontWeight.BOLD, color=color,
                              font_family=theme.MONO_FONT)
        box = theme.card_container(
            ft.Column(
                [ft.Text(label, size=10, color=theme.MUTED, font_family=theme.MONO_FONT), value_text],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2,
            ),
            padding=10,
        )
        return box, value_text

    checked_box, checked_val = stat_box("TOTAL CHECKED", theme.TEXT)
    confirmed_box, confirmed_val = stat_box("CONFIRMED", theme.GOOD)
    mismatch_box, mismatch_val = stat_box("MISMATCHES", theme.BAD)
    unknown_box, unknown_val = stat_box("UNKNOWN", theme.WARN)
    review_box, review_val = stat_box("NEEDS REVIEW", theme.WARN)

    progress = ft.ProgressBar(value=0, color=theme.ACCENT, bgcolor=theme.BORDER)
    status_text = ft.Text("Ready", size=12, color=theme.MUTED)
    elapsed_text = ft.Text("", size=12, color=theme.MUTED, font_family=theme.MONO_FONT)
    log_view = ft.ListView(spacing=2, height=160, auto_scroll=True)

    run_button = ft.Button("Run Status Check", icon=ft.Icons.PLAY_ARROW,
                            bgcolor=theme.ACCENT, color="white", disabled=True)
    open_button = ft.OutlinedButton("Open Results File", icon=ft.Icons.FOLDER_OPEN, visible=False)
    stop_button = ft.OutlinedButton("Stop", icon=ft.Icons.STOP_CIRCLE, icon_color=theme.BAD,
                                     visible=False)
    stop_event = {"event": threading.Event()}

    def log(msg, tag="info"):
        color = {"ok": theme.GOOD, "bad": theme.BAD, "info": theme.INFO,
                 "dim": theme.MUTED, "warn": theme.WARN}.get(tag, theme.TEXT)
        log_view.controls.append(ft.Text(msg, size=11, color=color, font_family=theme.MONO_FONT))
        page.update()

    def set_status(text):
        status_text.value = text
        page.update()

    def check_ready():
        ready = bool(file_paths) and bool(out_dir["path"]) and not running["value"]
        run_button.disabled = not ready
        page.update()

    def open_results(e=None):
        if not (result_path["path"] and os.path.exists(result_path["path"])):
            return
        try:
            os.startfile(result_path["path"])
        except AttributeError:
            page.launch_url(f"file://{result_path['path']}")

    open_button.on_click = open_results

    def request_stop(e=None):
        stop_event["event"].set()
        stop_button.disabled = True
        log("Stopping after the current batch — results so far will be saved...", "warn")
        set_status("Stopping...")

    stop_button.on_click = request_stop

    # ── Run ─────────────────────────────────────────────────
    def run_worker(start_time):
        try:
            api_key = cfg.get("claude_api_key")
            if not api_key:
                log("No API key set — add one under Step 1 first.", "bad")
                return

            model = core.MODELS[model_dd.value]
            batch_size = int(batch_dd.value)
            batch_pause = int(batch_pause_dd.value.replace("s", ""))
            recovery = recovery_cb.value

            os.makedirs(out_dir["path"], exist_ok=True)
            out_path = os.path.join(out_dir["path"], "Verified_Results_Claude.xlsx")

            if not recovery and os.path.exists(out_path):
                log(f"Verified_Results_Claude.xlsx already exists in that folder. Turn on Recovery mode "
                    "to build on it, or choose a different/empty output folder — running now would "
                    "overwrite it.", "bad")
                return

            client = anthropic.Anthropic(api_key=api_key)

            all_results, skip_keys = [], set()
            if recovery:
                all_results, skip_keys = core.load_already_verified(out_path)
                if all_results:
                    log(f"Recovery mode: {len(all_results)} products already in output file", "dim")

            log(f"Model: {model}", "info")

            stopped_early = False
            total_files = len(file_paths)
            for fi, path in enumerate(file_paths, 1):
                name = os.path.basename(path)
                log(f"── File {fi}/{total_files}: {name} ──", "info")

                def on_progress(pct, done, total, fi=fi, total_files=total_files):
                    overall = ((fi - 1) + done / max(total, 1)) / total_files
                    progress.value = overall
                    page.update()

                try:
                    file_results, total_rows, _ = core.check_file(
                        path, client, model, batch_size, batch_pause, skip_keys,
                        on_progress, log, set_status, stop=stop_event["event"],
                    )
                except core.FatalAPIError as ex:
                    log(f"Stopping — {ex}", "bad")
                    stopped_early = True
                    break
                except Exception as ex:
                    log(f"Cannot process {name}: {ex}", "bad")
                    continue

                all_results.extend(file_results)

                try:
                    core.save_excel(all_results, out_path)
                    result_path["path"] = out_path
                except Exception as ex:
                    log(f"Auto-save failed: {ex}", "warn")

                checked_val.value = str(len(all_results))
                confirmed_val.value = str(sum(1 for r in all_results if "Confirmed" in str(r.get("Match", ""))))
                mismatch_val.value = str(sum(1 for r in all_results if "Mismatch" in str(r.get("Match", ""))))
                unknown_val.value = str(sum(1 for r in all_results if str(r.get("Verified Status", "")).lower() == "unknown"))
                review_val.value = str(sum(1 for r in all_results if str(r.get("Verified Status", "")).lower() == core.NEEDS_REVIEW_STATUS))
                page.update()

                if stop_event["event"].is_set():
                    stopped_early = True
                    break

            elapsed = time.time() - start_time
            duration_str = _format_duration(elapsed)

            if not all_results:
                log(f"No results — check your API key and files. ({duration_str} elapsed)", "bad")
                status_text.value = "Failed"
                page.update()
                return

            core.save_excel(all_results, out_path)
            result_path["path"] = out_path
            review_count = sum(1 for r in all_results if str(r.get("Verified Status", "")).lower() == core.NEEDS_REVIEW_STATUS)
            avg_str = f"{elapsed / len(all_results):.1f}s/product" if all_results else "n/a"

            if stopped_early:
                why = ("you stopped the run" if stop_event["event"].is_set()
                       else "the error — fix the issue and")
                log(f"Stopped early after {duration_str} — {len(all_results)} product(s) saved before "
                    f"{why} ({avg_str}). Re-run with Recovery mode on to pick up the rest. "
                    f"Saved to: {out_path}", "warn" if stop_event["event"].is_set() else "bad")
                status_text.value = "Stopped"
                open_button.visible = True
                page.update()
                return

            done_msg = f"Done in {duration_str} — {len(all_results)} products across {total_files} file(s) ({avg_str})."
            if review_count:
                done_msg += f" {review_count} need human review (API couldn't process them)."
            done_msg += f" Saved to: {out_path}"
            log(done_msg, "ok" if not review_count else "warn")
            status_text.value = "Complete"
            progress.value = 1.0
            open_button.visible = True
            page.update()

        finally:
            running["value"] = False
            stop_button.visible = False
            run_button.disabled = False
            run_button.text = "Run Status Check"
            check_ready()

    def run_all(e=None):
        if running["value"]:
            return
        running["value"] = True
        run_button.disabled = True
        run_button.text = "Running..."
        open_button.visible = False
        stop_event["event"] = threading.Event()
        stop_button.visible = True
        stop_button.disabled = False
        progress.value = 0
        log_view.controls.clear()
        log(f"Starting status check on {len(file_paths)} file(s)...", "info")
        page.update()

        start_time = time.time()

        def tick():
            while running["value"]:
                elapsed_text.value = f"Elapsed: {_format_duration(time.time() - start_time)}"
                page.update()
                time.sleep(1)

        threading.Thread(target=tick, daemon=True).start()
        threading.Thread(target=run_worker, args=(start_time,), daemon=True).start()

    run_button.on_click = run_all

    # ── Layout ──────────────────────────────────────────────
    key_row = ft.Row(
        [
            ft.Text("Step 1 — Claude API key", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            api_key_field,
            ft.Button("Save Key", bgcolor=theme.ACCENT, color="white", on_click=save_key),
            api_badge,
        ],
        spacing=10, wrap=True,
    )

    files_header = ft.Row(
        [
            ft.Text("Step 2 — Add CSV files", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            ft.Row(
                [
                    file_count_text,
                    ft.Button("+ Add Files", icon=ft.Icons.ADD, bgcolor=theme.ACCENT, color="white",
                               on_click=lambda e: file_picker.pick_files(
                                   allow_multiple=True, allowed_extensions=["csv"])),
                    ft.OutlinedButton("Clear All", on_click=clear_files),
                ],
                spacing=10,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    files_caption = ft.Text(
        "Needs mfr, model, and product_status columns (product name and Page URL are optional — "
        "a Page URL lets the tool check that page directly before falling back to a full web search).",
        size=10, color=theme.MUTED,
    )

    settings_row = ft.Row([model_dd, batch_dd, batch_pause_dd], spacing=12, wrap=True)

    out_row = ft.Row(
        [
            ft.Text("Step 3 — Choose output folder", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            out_label,
            ft.OutlinedButton("Choose Folder", icon=ft.Icons.FOLDER_OPEN,
                               on_click=lambda e: folder_picker.get_directory_path()),
        ],
        spacing=10,
    )

    run_row = ft.Row([run_button, stop_button, open_button, status_text, elapsed_text], spacing=12)
    stats_row = ft.Row([checked_box, confirmed_box, mismatch_box, unknown_box, review_box], spacing=10)

    return ft.Container(
        content=ft.Column(
            [
                theme.card_container(key_row),
                theme.card_container(
                    ft.Column([files_header, file_list, files_caption], spacing=8)
                ),
                theme.card_container(
                    ft.Column([ft.Text("Settings", weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                settings_row, recovery_cb], spacing=10)
                ),
                theme.card_container(out_row),
                theme.card_container(
                    ft.Column([run_row, progress, stats_row, theme.card_container(log_view, padding=8)],
                                spacing=10)
                ),
            ],
            spacing=14, scroll=ft.ScrollMode.AUTO, expand=True,
        ),
        padding=20, expand=True,
    )

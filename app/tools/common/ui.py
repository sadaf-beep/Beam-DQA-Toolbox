"""
Shared Flet screen for the QA tools.

Every check has the same shape — (API key) → files → settings → output
folder → run, with stat boxes, a progress bar and a log — so a tool's
view.py only describes what's different (its stats and its run function)
and tool_screen() builds the rest. The run function executes on a worker
thread and gets a RunContext with the inputs and UI callbacks.
"""

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import flet as ft

import theme
from . import claude


@dataclass
class Stat:
    label: str
    color: str
    count: Callable[[list], int]


@dataclass
class RunContext:
    files: list
    out_dir: str
    log: Callable
    set_status: Callable
    set_progress: Callable          # fraction 0..1
    show_results: Callable          # (results: list) -> refresh stat boxes
    client: object = None           # anthropic.Anthropic when needs_api
    model: str = ""
    batch_size: int = 5
    batch_pause: int = 10
    options: dict = field(default_factory=dict)  # values of extra settings, by key


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


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


def tool_screen(page: ft.Page, *, run_label: str, caption: str, stats: list,
                run: Callable[[RunContext], tuple], needs_api: bool = False,
                extensions=("csv", "xlsx", "xls"), extra_settings: dict = None,
                default_batch: str = "5", default_pause: str = "10s") -> ft.Control:
    """
    run(ctx) -> (results: list, out_path: str | None, done_message: str)
    extra_settings: {key: control} — each control's .value lands in ctx.options[key].
    """
    cfg = claude.load_cfg() if needs_api else {}
    out_dir = {"path": None}
    file_paths: list[str] = []
    running = {"value": False}
    result_path = {"path": None}
    extra_settings = extra_settings or {}
    steps = iter(range(1, 10))

    # ── API key ─────────────────────────────────────────────
    api_key_field = ft.TextField(
        label="Anthropic API key", value=cfg.get("claude_api_key", ""),
        password=True, can_reveal_password=True, width=360, hint_text="sk-ant-...",
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
        claude.save_cfg(cfg)
        api_badge.value = "API key set"
        api_badge.color = theme.GOOD
        page.update()

    # ── File list ───────────────────────────────────────────
    file_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=110)
    placeholder = ft.Container(
        content=ft.Text('No files added yet. Click "+ Add Files" to select some.',
                         color=theme.MUTED, size=12),
        padding=10,
    )
    file_list.controls.append(placeholder)
    file_count_text = ft.Text("No files selected", size=11, color=theme.MUTED)

    def refresh_file_list():
        file_list.controls.clear()
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
        if not file_paths:
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
        label="Model", width=260, value=claude.DEFAULT_MODEL_LABEL,
        options=[ft.dropdown.Option(k) for k in claude.MODELS],
    )
    batch_dd = ft.Dropdown(
        label="Products per call", width=160, value=default_batch,
        options=[ft.dropdown.Option(v) for v in ["3", "5", "8", "10"]],
    )
    batch_pause_dd = ft.Dropdown(
        label="Pause between batches", width=180, value=default_pause,
        options=[ft.dropdown.Option(v) for v in ["0s", "5s", "10s", "15s", "20s", "30s"]],
    )
    out_label = ft.Text("No output folder selected", size=12, color=theme.MUTED,
                         font_family=theme.MONO_FONT)

    # ── Stats + progress + log ──────────────────────────────
    stat_widgets = [(s, *stat_box(s.label.upper(), s.color)) for s in stats]

    progress = ft.ProgressBar(value=0, color=theme.ACCENT, bgcolor=theme.BORDER)
    status_text = ft.Text("Ready", size=12, color=theme.MUTED)
    elapsed_text = ft.Text("", size=12, color=theme.MUTED, font_family=theme.MONO_FONT)
    log_view = ft.ListView(spacing=2, height=160, auto_scroll=True)

    run_button = ft.Button(run_label, icon=ft.Icons.PLAY_ARROW,
                            bgcolor=theme.ACCENT, color="white", disabled=True)
    open_button = ft.OutlinedButton("Open Results File", icon=ft.Icons.FOLDER_OPEN, visible=False)

    def log(msg, tag="info"):
        color = {"ok": theme.GOOD, "bad": theme.BAD, "info": theme.INFO,
                 "dim": theme.MUTED, "warn": theme.WARN}.get(tag, theme.TEXT)
        log_view.controls.append(ft.Text(msg, size=11, color=color, font_family=theme.MONO_FONT))
        page.update()

    def set_status(text):
        status_text.value = text
        page.update()

    def set_progress(fraction):
        progress.value = max(0.0, min(1.0, fraction))
        page.update()

    def show_results(results):
        for s, _box, value_text in stat_widgets:
            value_text.value = str(s.count(results))
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

    # ── Run ─────────────────────────────────────────────────
    def run_worker(start_time):
        try:
            ctx = RunContext(
                files=list(file_paths), out_dir=out_dir["path"], log=log,
                set_status=set_status, set_progress=set_progress, show_results=show_results,
                options={k: c.value for k, c in extra_settings.items()},
            )
            if needs_api:
                api_key = cfg.get("claude_api_key")
                if not api_key:
                    log("No API key set — add one under Step 1 first.", "bad")
                    status_text.value = "Failed"
                    return
                ctx.client = claude.new_client(api_key)
                ctx.model = claude.MODELS[model_dd.value]
                ctx.batch_size = int(batch_dd.value)
                ctx.batch_pause = int(batch_pause_dd.value.replace("s", ""))
                log(f"Model: {ctx.model}", "info")

            os.makedirs(ctx.out_dir, exist_ok=True)
            try:
                results, out_path, done_msg = run(ctx)
            except claude.FatalAPIError as ex:
                log(f"Stopping — {ex}", "bad")
                status_text.value = "Stopped"
                return
            except Exception as ex:
                log(f"Failed: {ex}", "bad")
                status_text.value = "Failed"
                return

            show_results(results)
            elapsed = format_duration(time.time() - start_time)
            if out_path:
                result_path["path"] = out_path
                open_button.visible = True
                log(f"{done_msg} ({elapsed}) Saved to: {out_path}", "ok")
            else:
                log(f"{done_msg} ({elapsed})", "warn")
            status_text.value = "Complete"
            progress.value = 1.0
        finally:
            running["value"] = False
            run_button.text = run_label
            page.update()
            check_ready()

    def run_all(e=None):
        if running["value"]:
            return
        running["value"] = True
        run_button.disabled = True
        run_button.text = "Running..."
        open_button.visible = False
        progress.value = 0
        log_view.controls.clear()
        log(f"Starting on {len(file_paths)} file(s)...", "info")
        page.update()

        start_time = time.time()

        def tick():
            while running["value"]:
                elapsed_text.value = f"Elapsed: {format_duration(time.time() - start_time)}"
                page.update()
                time.sleep(1)

        threading.Thread(target=tick, daemon=True).start()
        threading.Thread(target=run_worker, args=(start_time,), daemon=True).start()

    run_button.on_click = run_all

    # ── Layout ──────────────────────────────────────────────
    sections = []
    if needs_api:
        sections.append(theme.card_container(ft.Row(
            [
                ft.Text(f"Step {next(steps)} — Claude API key", weight=ft.FontWeight.BOLD, color=theme.TEXT),
                api_key_field,
                ft.Button("Save Key", bgcolor=theme.ACCENT, color="white", on_click=save_key),
                api_badge,
            ],
            spacing=10, wrap=True,
        )))

    files_header = ft.Row(
        [
            ft.Text(f"Step {next(steps)} — Add files", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            ft.Row(
                [
                    file_count_text,
                    ft.Button("+ Add Files", icon=ft.Icons.ADD, bgcolor=theme.ACCENT, color="white",
                               on_click=lambda e: file_picker.pick_files(
                                   allow_multiple=True, allowed_extensions=list(extensions))),
                    ft.OutlinedButton("Clear All", on_click=clear_files),
                ],
                spacing=10,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    sections.append(theme.card_container(ft.Column(
        [files_header, file_list, ft.Text(caption, size=10, color=theme.MUTED)], spacing=8)))

    setting_controls = ([model_dd, batch_dd, batch_pause_dd] if needs_api else []) + \
        [c for c in extra_settings.values() if not isinstance(c, ft.Checkbox)]
    checkboxes = [c for c in extra_settings.values() if isinstance(c, ft.Checkbox)]
    if setting_controls or checkboxes:
        sections.append(theme.card_container(ft.Column(
            [ft.Text("Settings", weight=ft.FontWeight.BOLD, color=theme.TEXT),
             ft.Row(setting_controls, spacing=12, wrap=True), *checkboxes],
            spacing=10)))

    sections.append(theme.card_container(ft.Row(
        [
            ft.Text(f"Step {next(steps)} — Choose output folder", weight=ft.FontWeight.BOLD, color=theme.TEXT),
            out_label,
            ft.OutlinedButton("Choose Folder", icon=ft.Icons.FOLDER_OPEN,
                               on_click=lambda e: folder_picker.get_directory_path()),
        ],
        spacing=10,
    )))

    sections.append(theme.card_container(ft.Column(
        [
            ft.Row([run_button, open_button, status_text, elapsed_text], spacing=12),
            progress,
            ft.Row([box for _s, box, _v in stat_widgets], spacing=10, wrap=True),
            theme.card_container(log_view, padding=8),
        ],
        spacing=10,
    )))

    return ft.Container(
        content=ft.Column(sections, spacing=14, scroll=ft.ScrollMode.AUTO, expand=True),
        padding=20, expand=True,
    )

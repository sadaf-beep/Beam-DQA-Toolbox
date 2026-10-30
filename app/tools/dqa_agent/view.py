"""Flet UI for the DQA Agent — a chat screen over the loaded product files."""

import os
import threading

import flet as ft

import theme
from tools.common import claude, data
from . import core

SUGGESTIONS = [
    "What's in this file?",
    "Find duplicate products",
    "Check the product pages and links",
    "Check the status of every product",
    "What should I fix first?",
]


def build(page: ft.Page) -> ft.Control:
    cfg = claude.load_cfg()
    state = {"files": [], "out_dir": None, "session": None, "running": False,
             "stop": threading.Event(), "report": None}

    # ── Setup row: key, files, output folder, check model ───
    api_key_field = ft.TextField(label="Anthropic API key", password=True, can_reveal_password=True,
                                 width=300, hint_text="sk-ant-...", visible=not cfg.get("claude_api_key"))
    files_text = ft.Text("No files loaded", size=12, color=theme.MUTED, font_family=theme.MONO_FONT)
    out_text = ft.Text("No output folder", size=12, color=theme.MUTED, font_family=theme.MONO_FONT)
    model_dd = ft.Dropdown(label="Model for checks", width=240, value=claude.DEFAULT_MODEL_LABEL,
                           options=[ft.dropdown.Option(k) for k in claude.MODELS])

    def save_key(e=None):
        key = (api_key_field.value or "").strip()
        if key.startswith("sk-ant-"):
            cfg["claude_api_key"] = key
            claude.save_cfg(cfg)
            api_key_field.visible = False
            page.update()

    api_key_field.on_submit = save_key

    # ── Chat area ───────────────────────────────────────────
    chat = ft.ListView(expand=True, spacing=10, auto_scroll=True, padding=10)
    activity = ft.ListView(height=90, spacing=1, auto_scroll=True)
    status_text = ft.Text("", size=12, color=theme.MUTED)
    progress = ft.ProgressBar(value=0, color=theme.ACCENT, bgcolor=theme.BORDER, visible=False)
    input_box = ft.TextField(hint_text="Ask the DQA agent… e.g. “Check warranty for all Sony products”",
                             expand=True, multiline=False, disabled=True)
    send_btn = ft.Button("Send", icon=ft.Icons.SEND, bgcolor=theme.ACCENT, color="white", disabled=True)
    stop_btn = ft.OutlinedButton("Stop", icon=ft.Icons.STOP_CIRCLE, icon_color=theme.BAD, visible=False)
    report_btn = ft.OutlinedButton("Open last report", icon=ft.Icons.FOLDER_OPEN, visible=False)

    def bubble(text, mine: bool):
        body = ft.Text(text, color=theme.TEXT, size=13, selectable=True) if mine else \
            ft.Markdown(text, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        auto_follow_links=True)
        chat.controls.append(ft.Row(
            [ft.Container(body, bgcolor=theme.ACCENT if mine else theme.SURFACE, padding=12,
                          border_radius=10, width=620)],
            alignment=ft.MainAxisAlignment.END if mine else ft.MainAxisAlignment.START,
        ))
        page.update()

    def log(msg, tag="info"):
        color = {"ok": theme.GOOD, "bad": theme.BAD, "info": theme.INFO,
                 "dim": theme.MUTED, "warn": theme.WARN}.get(tag, theme.TEXT)
        activity.controls.append(ft.Text(msg, size=10, color=color, font_family=theme.MONO_FONT))
        page.update()

    def set_status(text):
        status_text.value = text
        page.update()

    def set_progress(fraction):
        progress.visible = True
        progress.value = max(0.0, min(1.0, fraction))
        page.update()

    def ready():
        ok = bool(state["files"]) and bool(state["out_dir"]) and not state["running"]
        input_box.disabled = send_btn.disabled = not ok
        for c in suggestion_row.controls:
            c.disabled = not ok
        page.update()

    # ── Files ───────────────────────────────────────────────
    def on_files(e: ft.FilePickerResultEvent):
        if not e.files:
            return
        state["files"] = [f.path for f in e.files]
        state["session"] = None  # new files → new conversation
        chat.controls.clear()
        names = ", ".join(os.path.basename(p) for p in state["files"])
        files_text.value = names
        files_text.color = theme.INFO
        bubble(f"Loaded {names}. Ask me anything about these products, or pick a suggestion below.", False)
        ready()

    def on_folder(e: ft.FilePickerResultEvent):
        if e.path:
            state["out_dir"] = e.path
            out_text.value = e.path
            out_text.color = theme.INFO
            ready()

    file_picker = ft.FilePicker(on_result=on_files)
    folder_picker = ft.FilePicker(on_result=on_folder)
    page.overlay.extend([file_picker, folder_picker])

    # ── Sending ─────────────────────────────────────────────
    def get_session():
        if state["session"] is None:
            df = data.load_files(state["files"], log)
            state["session"] = core.AgentSession(
                df, claude.new_client(cfg["claude_api_key"]),
                check_model=claude.MODELS[model_dd.value], out_dir=state["out_dir"],
                log=log, status=set_status, progress=set_progress, stop=state["stop"])
        s = state["session"]
        s.check_model = claude.MODELS[model_dd.value]
        s.out_dir = state["out_dir"]
        return s

    def worker(text):
        try:
            session = get_session()
            session.stop = state["stop"]
            session.chat(text, lambda reply: bubble(reply, False))
            if getattr(session, "last_report", None):
                state["report"] = session.last_report
                report_btn.visible = True
        except claude.FatalAPIError as ex:
            bubble(f"I had to stop: {ex}", False)
        except Exception as ex:
            bubble(f"Something went wrong: {str(ex)[:300]}", False)
        finally:
            state["running"] = False
            stop_btn.visible = False
            progress.visible = False
            status_text.value = ""
            ready()

    def send(text=None):
        text = (text or input_box.value or "").strip()
        if not text or state["running"]:
            return
        if not cfg.get("claude_api_key"):
            bubble("Please enter your Anthropic API key at the top first.", False)
            return
        input_box.value = ""
        bubble(text, True)
        state["running"] = True
        state["stop"] = threading.Event()
        stop_btn.visible = True
        stop_btn.disabled = False
        ready()
        threading.Thread(target=worker, args=(text,), daemon=True).start()

    def request_stop(e=None):
        state["stop"].set()
        stop_btn.disabled = True
        set_status("Stopping after the current step...")

    def open_report(e=None):
        if state["report"] and os.path.exists(state["report"]):
            os.startfile(state["report"])

    input_box.on_submit = lambda e: send()
    send_btn.on_click = lambda e: send()
    stop_btn.on_click = request_stop
    report_btn.on_click = open_report

    suggestion_row = ft.Row(
        [ft.OutlinedButton(s, on_click=lambda e, s=s: send(s), disabled=True) for s in SUGGESTIONS],
        wrap=True, spacing=8,
    )

    setup = theme.card_container(ft.Column([
        ft.Row([
            ft.Button("Load Files", icon=ft.Icons.UPLOAD_FILE, bgcolor=theme.ACCENT, color="white",
                      on_click=lambda e: file_picker.pick_files(
                          allow_multiple=True, allowed_extensions=["csv", "xlsx", "xls"])),
            files_text,
        ], spacing=10),
        ft.Row([
            ft.OutlinedButton("Output Folder", icon=ft.Icons.FOLDER_OPEN,
                              on_click=lambda e: folder_picker.get_directory_path()),
            out_text,
        ], spacing=10),
        ft.Row([model_dd, api_key_field,
                ft.Text("The agent only reports — it never changes your file.", size=11,
                        color=theme.MUTED)], spacing=12, wrap=True),
    ], spacing=8), padding=12)

    return ft.Container(
        content=ft.Column([
            setup,
            theme.card_container(chat, padding=0, expand=True),
            ft.Row([status_text, progress], spacing=10),
            suggestion_row,
            ft.Row([input_box, send_btn, stop_btn, report_btn], spacing=8),
            ft.ExpansionTile(title=ft.Text("Activity", size=11, color=theme.MUTED),
                             controls=[activity], collapsed_icon_color=theme.MUTED),
        ], spacing=10, expand=True),
        padding=16, expand=True,
    )

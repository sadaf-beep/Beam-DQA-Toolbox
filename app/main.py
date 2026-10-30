"""Toolbox — launcher shell for the app suite. Run with: python app/main.py"""

import threading

import flet as ft

from tools.base import Tool
import theme

TOOLS = [
    Tool(
        key="dqa_agent",
        name="DQA Agent",
        description="Chat with an assistant that checks your product file like a DQA team member.",
        icon=ft.Icons.SUPPORT_AGENT,
        view_module="tools.dqa_agent.view",
    ),
    Tool(
        key="qa_pipeline",
        name="Full QA Pipeline",
        description="Run every QA check on product files and get one Master QA Report ranked by concern.",
        icon=ft.Icons.PLAYLIST_ADD_CHECK,
        view_module="tools.qa_pipeline.view",
    ),
    Tool(
        key="duplicates",
        name="Duplicate Detection",
        description="Find exact, cross-brand and near-match duplicate products. No API needed.",
        icon=ft.Icons.CONTENT_COPY,
        view_module="tools.duplicates.view",
    ),
    Tool(
        key="oem_url",
        name="OEM URL Check",
        description="Check product_url domains against the is_oem_url flag. No API needed.",
        icon=ft.Icons.DOMAIN_VERIFICATION,
        view_module="tools.oem_url.view",
    ),
    Tool(
        key="link_checker",
        name="Link Checker",
        description="Scan CSV/Excel files for broken image and product links.",
        icon=ft.Icons.LINK,
        view_module="tools.link_checker.view",
    ),
    Tool(
        key="status_checker",
        name="Status Checker",
        description="Verify product lifecycle status (supported/discontinued) via the Claude API.",
        icon=ft.Icons.FACT_CHECK,
        view_module="tools.status_checker.view",
    ),
    Tool(
        key="product_name_qa",
        name="Product Name QA",
        description="Check manufacturer, model and product name are consistent (Claude, no web search).",
        icon=ft.Icons.SPELLCHECK,
        view_module="tools.product_name_qa.view",
    ),
    Tool(
        key="firmware_docs",
        name="Firmware & Docs",
        description="Verify firmware and documentation flags against the manufacturer's site (Claude).",
        icon=ft.Icons.SYSTEM_UPDATE_ALT,
        view_module="tools.firmware_docs.view",
    ),
    Tool(
        key="warranty",
        name="Warranty Validation",
        description="Verify warranty_length against the manufacturer's stated warranty (Claude).",
        icon=ft.Icons.VERIFIED_USER,
        view_module="tools.warranty.view",
    ),
    Tool(
        key="dimensions",
        name="Dimensions Validation",
        description="Check product and package dimensions for unit consistency and accuracy (Claude).",
        icon=ft.Icons.STRAIGHTEN,
        view_module="tools.dimensions.view",
    ),
    Tool(
        key="file_splitter",
        name="File Splitter",
        description="Split a large CSV/Excel file into smaller CSVs.",
        icon=ft.Icons.CALL_SPLIT,
        view_module="tools.file_splitter.view",
    ),
]


def main(page: ft.Page):
    page.title = "Toolbox"
    page.bgcolor = theme.BG
    page.window.width = 1000
    page.window.height = 720
    page.window.min_width = 760
    page.window.min_height = 560
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.fonts = {}

    content_area = ft.Container(expand=True, bgcolor=theme.BG)

    def tool_card(tool: Tool):
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(tool.icon, color=theme.ACCENT, size=32),
                    ft.Text(tool.name, size=16, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                    ft.Text(tool.description, size=12, color=theme.MUTED),
                ],
                spacing=8,
            ),
            bgcolor=theme.SURFACE,
            border=theme.border_all(1, theme.BORDER),
            border_radius=12,
            padding=20,
            width=260,
            ink=True,
            on_click=lambda e, t=tool: open_tool(t),
        )

    def show_home(e=None):
        content_area.content = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Toolbox", size=26, weight=ft.FontWeight.BOLD, color=theme.ACCENT),
                    ft.Text("Pick a tool to get started.", color=theme.MUTED),
                    ft.Row([tool_card(t) for t in TOOLS], wrap=True, spacing=16, run_spacing=16),
                ],
                spacing=16,
            ),
            padding=30,
            expand=True,
        )
        page.update()

    def open_tool(tool: Tool):
        header = ft.Container(
            content=ft.Row(
                [
                    ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=theme.TEXT, on_click=show_home),
                    ft.Text(tool.name, size=18, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                ]
            ),
            bgcolor=theme.HEADER_BG,
            padding=ft.Padding(10, 10, 10, 10),
        )
        # The tool's module (and its heavy deps) may still be importing, so
        # show the header with a spinner first instead of a frozen screen.
        loading = ft.Container(
            content=ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2),
                            ft.Text(f"Loading {tool.name}...", color=theme.MUTED)], spacing=10),
            padding=30,
        )
        content_area.content = ft.Column([header, loading], spacing=0, expand=True)
        page.update()
        try:
            body = tool.build(page)
        except Exception as ex:
            body = ft.Container(ft.Text(f"Could not open {tool.name}: {ex}", color=theme.BAD), padding=30)
        content_area.content = ft.Column([header, body], spacing=0, expand=True)
        page.update()

    header_bar = ft.Container(
        content=ft.Row(
            [
                ft.Text("  Toolbox", size=18, weight=ft.FontWeight.BOLD, color=theme.ACCENT,
                        font_family=theme.MONO_FONT),
                ft.Text("app suite", size=11, color=theme.MUTED),
            ],
            spacing=10,
        ),
        bgcolor=theme.HEADER_BG,
        padding=14,
    )

    page.add(ft.Column([header_bar, content_area], spacing=0, expand=True))
    show_home()

    # Warm up the shared heavy libraries in the background once the window
    # is showing, so the first tool opened usually doesn't wait on them.
    def preload():
        try:
            import pandas, requests, openpyxl, anthropic  # noqa: F401
        except Exception:
            pass

    threading.Thread(target=preload, daemon=True).start()


if __name__ == "__main__":
    ft.app(target=main)

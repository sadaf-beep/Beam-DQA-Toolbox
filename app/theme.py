"""Shared dark theme constants and small layout helpers used across every tool."""

import flet as ft

BG = "#1a1d27"
SURFACE = "#242838"
HEADER_BG = "#141720"
CARD_BG = "#242838"
BORDER = "#2d3348"
ACCENT = "#3b82f6"
TEXT = "#e2e8f0"
MUTED = "#64748b"
GOOD = "#4ade80"
BAD = "#f87171"
INFO = "#93c5fd"

MONO_FONT = "Consolas, 'Courier New', monospace"


def border_all(width: float, color: str) -> ft.Border:
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, right=side, bottom=side, left=side)


def card_container(content: ft.Control, **kwargs) -> ft.Container:
    defaults = dict(
        bgcolor=CARD_BG,
        border=border_all(1, BORDER),
        border_radius=10,
        padding=16,
    )
    defaults.update(kwargs)
    return ft.Container(content=content, **defaults)

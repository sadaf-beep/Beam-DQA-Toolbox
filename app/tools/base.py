"""Interface every tool in the suite implements so the launcher can list and open it."""

import importlib
from dataclasses import dataclass

import flet as ft


@dataclass
class Tool:
    key: str
    name: str
    description: str
    icon: str
    # Dotted path of the tool's view module (e.g. "tools.link_checker.view"),
    # imported only when the tool is opened — so heavy dependencies like
    # pandas/anthropic don't load at startup and delay the window appearing.
    view_module: str

    def build(self, page: ft.Page) -> ft.Control:
        return importlib.import_module(self.view_module).build(page)

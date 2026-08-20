"""Interface every tool in the suite implements so the launcher can list and open it."""

from dataclasses import dataclass
from typing import Callable

import flet as ft


@dataclass
class Tool:
    key: str
    name: str
    description: str
    icon: str
    build: Callable[[ft.Page], ft.Control]

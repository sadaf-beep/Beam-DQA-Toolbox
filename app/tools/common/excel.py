"""
Formatted Excel output shared by every QA tool — pure Python, no UI.

Same house style as the Status Checker report: navy header row, small
Calibri text, light row fills, and status cells coloured by value.
"""

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAV = "0D1B2A"
ROW_BG = "FAFAFA"

# (fill, font colour) for status cells, picked by keyword in the value —
# first match wins, so the more specific words come first.
GOOD = ("C8E6C9", "1B5E20")
WARN = ("FFE082", "7A5000")
BAD = ("FFCDD2", "B71C1C")
INFO = ("E3F2FD", "0D47A1")
REVIEW = ("E1BEE7", "4A148C")

DEFAULT_STATUS_RULES = [
    ("needs review", REVIEW),
    ("could not check", REVIEW),
    ("mismatch", WARN),
    ("fail", BAD),
    ("broken", BAD),
    ("exact duplicate", BAD),
    ("cross-brand", WARN),
    ("near match", WARN),
    ("flag", WARN),
    ("warning", WARN),
    ("review", WARN),
    ("unknown", INFO),
    ("error", INFO),
    ("skip", INFO),
    ("n/a", INFO),
    ("pass", GOOD),
    ("confirmed", GOOD),
    ("ok", GOOD),
]


def fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color.lstrip("#"))


def thin_border() -> Border:
    side = Side(style="thin", color="DDDDDD")
    return Border(left=side, right=side, top=side, bottom=side)


def status_colors(value, rules=DEFAULT_STATUS_RULES):
    v = str(value or "").strip().lower()
    if not v:
        return None
    for keyword, colors in rules:
        if keyword in v:
            return colors
    return None


def write_sheet(wb: Workbook, title: str, columns: list, rows: list, *,
                widths=None, status_cols=(), rules=DEFAULT_STATUS_RULES,
                row_height: int = 36) -> None:
    """Append a styled sheet. `rows` is a list of dicts keyed by column
    name; `status_cols` are columns whose cells get coloured by value."""
    if wb.worksheets and wb.active.title == "Sheet" and wb.active.max_row == 1:
        ws = wb.active
        ws.title = title
    else:
        ws = wb.create_sheet(title)

    ws.append(columns)
    for cell in ws[1]:
        cell.fill = fill(NAV)
        cell.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border()
    ws.row_dimensions[1].height = 24

    for i, col in enumerate(columns, 1):
        w = widths[i - 1] if widths and i - 1 < len(widths) else 18
        ws.column_dimensions[get_column_letter(i)].width = w

    # Style objects are built once and shared — creating new ones per cell
    # (and fetching rows via ws[rn]) made large reports take minutes.
    row_fill, row_font = fill(ROW_BG), Font(name="Calibri", size=9)
    row_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    border = thin_border()
    status_styles = {}

    status_idx = {columns.index(c) for c in status_cols if c in columns}
    ncols = len(columns)
    for rn, r in enumerate(rows, 2):
        for ci in range(ncols):
            cell = ws.cell(rn, ci + 1, _cell_value(r.get(columns[ci], "")))
            cell.fill, cell.font, cell.alignment, cell.border = row_fill, row_font, row_align, border
            if ci in status_idx:
                colors = status_colors(cell.value, rules)
                if colors:
                    if colors not in status_styles:
                        status_styles[colors] = (fill(colors[0]),
                                                 Font(name="Calibri", size=9, bold=True, color=colors[1]))
                    cell.fill, cell.font = status_styles[colors]
        ws.row_dimensions[rn].height = row_height

    ws.freeze_panes = "A2"


def write_summary(wb: Workbook, title: str, items: list, heading: str = "Summary") -> None:
    """A two-column label/count sheet. `items` are (label, count, colors)
    tuples; colors is a (fill, font) pair or None. A ("", "", None) row
    leaves a gap; a label with count "" is a sub-heading."""
    ws = wb.create_sheet(title)
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 12
    ws["A1"] = heading
    ws["A1"].font = Font(bold=True, color="FFFFFF", name="Calibri", size=12)
    ws["A1"].fill = fill(NAV)
    ws["B1"].fill = fill(NAV)
    ws.row_dimensions[1].height = 24
    for i, (label, count, colors) in enumerate(items, 2):
        bg = colors[0] if colors else "F5F5F5"
        c1 = ws.cell(i, 1, label)
        c1.font = Font(name="Calibri", size=10, bold=bool(label) and count == "")
        if label:
            c1.fill = fill(bg)
        if count != "":
            c2 = ws.cell(i, 2, count)
            c2.font = Font(name="Calibri", size=10, bold=True)
            c2.alignment = Alignment(horizontal="center")
            c2.fill = fill(bg)
        ws.row_dimensions[i].height = 18


def _cell_value(v):
    if isinstance(v, (list, tuple)):
        return " | ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if v is None:
        return ""
    try:
        if v != v:  # NaN
            return ""
    except Exception:
        pass
    return v

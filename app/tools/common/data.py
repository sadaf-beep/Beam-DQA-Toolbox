"""
Loading product files and reading their columns — pure Python, no UI.

Product feeds come from different exports, so column names vary in case,
spacing and underscores ("Product Name", "product_name", "product name").
Every lookup goes through find_col() so tools don't each guess.
"""

import os
import re
from pathlib import Path

import pandas as pd

SOURCE_COL = "Source File"
# Position of the row in the combined DataFrame — the identity every check
# reports back against, so two rows with the same mfr/model never collide.
ROW_COL = "_row"


def load_df(path: str) -> pd.DataFrame:
    """Load a CSV or Excel file, trying multiple encodings for CSV."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        for enc in ["utf-8", "latin-1", "cp1252", "iso-8859-1"]:
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not read file — check its encoding or save it as Excel")
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    raise ValueError(f"Unsupported file type: {ext}")


def load_files(paths: list, log=None) -> pd.DataFrame:
    """Load several files into one DataFrame, tagging each row with its
    source file name and a unique ROW_COL index. Unreadable files are
    logged and skipped rather than stopping the whole run."""
    frames = []
    for p in paths:
        name = Path(p).name
        try:
            df = load_df(p)
        except Exception as ex:
            if log:
                log(f"Cannot read {name}: {ex}", "bad")
            continue
        df[SOURCE_COL] = name
        frames.append(df)
        if log:
            log(f"Loaded {name} ({len(df)} rows)", "dim")
    if not frames:
        raise ValueError("None of the selected files could be read")
    combined = pd.concat(frames, ignore_index=True)
    combined[ROW_COL] = range(len(combined))
    return combined


def _norm(name) -> str:
    return re.sub(r"[\s_]+", " ", str(name).strip().lower())


def find_col(df: pd.DataFrame, *names):
    """Return the actual column name matching any of `names`, ignoring
    case and treating spaces/underscores alike — or None."""
    lookup = {_norm(c): c for c in df.columns}
    for n in names:
        hit = lookup.get(_norm(n))
        if hit is not None:
            return hit
    return None


def clean(val) -> str:
    """Turn a possibly-NaN/None cell value into a plain, trimmed string —
    pandas represents a blank cell as float('nan'), and str(nan) == 'nan'."""
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    # A numeric column with blanks loads as float, so 730 arrives as 730.0.
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


# Reference columns from the Studio export, carried into every report so a
# reviewer can jump straight from a flagged row to the product in Insights.
REF_COLS = ["Product ID", "Beam Insights URL"]


class Columns:
    """Resolved names of the standard product columns for one DataFrame.
    Attributes are None when the file doesn't have that column. Names cover
    the current Studio export ("Page URL", "Image URL") and older exports
    ("product_url", "Image Url")."""

    def __init__(self, df: pd.DataFrame):
        self.mfr = find_col(df, "mfr", "manufacturer")
        self.model = find_col(df, "model", "model number")
        self.name = find_col(df, "product name", "name")
        self.url = find_col(df, "page url", "product_url", "product url")
        self.image = find_col(df, "image url", "image_url")
        self.product_id = find_col(df, "product id", "product_id")
        self.insights_url = find_col(df, "beam insights url", "insights url")
        self.doc_urls = find_col(df, "document urls")
        self.resource_urls = find_col(df, "resource urls")
        self.update_urls = find_col(df, "update urls")

    def refs(self, row) -> dict:
        """REF_COLS values for a row (blank when the file lacks them)."""
        return {
            "Product ID": clean(row.get(self.product_id)) if self.product_id else "",
            "Beam Insights URL": clean(row.get(self.insights_url)) if self.insights_url else "",
        }

    def product(self, row) -> dict:
        """The identity fields of a row, as cleaned strings."""
        return {
            "mfr": clean(row.get(self.mfr)) if self.mfr else "",
            "model": clean(row.get(self.model)) if self.model else "",
            "product_name": clean(row.get(self.name)) if self.name else "",
        }

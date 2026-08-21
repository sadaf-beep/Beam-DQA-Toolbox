"""
Link-checking engine — pure Python, no UI dependency.

Kept separate from the Flet view so the checking logic can be tested and
reused on its own.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

IMAGE_COL = "Image Url"
PRODUCT_COL = "product_url"
WORKERS = 20
TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; LinkChecker/2.0)"


def new_session(pool_size: int = WORKERS) -> requests.Session:
    """A shared, connection-pooled Session — reused across every request in a run."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def new_executor(max_workers: int = WORKERS) -> ThreadPoolExecutor:
    """One executor shared across ALL files in a run, so total concurrent
    requests stays capped at max_workers regardless of how many files are
    being checked at once."""
    return ThreadPoolExecutor(max_workers=max_workers)


def check_url(session: requests.Session, url: str, timeout: int = TIMEOUT):
    """Returns (status_label, is_broken). Tries HEAD first (cheaper), falls
    back to GET if the server rejects HEAD or the HEAD request itself fails."""
    if not isinstance(url, str) or not url.strip():
        return ("--", False)

    url = url.strip()
    if url.startswith("data:"):
        return ("OK (embedded image)", False)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {"User-Agent": USER_AGENT}

    try:
        try:
            r = session.head(url, timeout=timeout, allow_redirects=True, headers=headers)
            if r.status_code in (405, 501):
                r = session.get(url, timeout=timeout, allow_redirects=True,
                                 headers=headers, stream=True)
                r.close()
        except requests.exceptions.RequestException:
            r = session.get(url, timeout=timeout, allow_redirects=True,
                             headers=headers, stream=True)
            r.close()

        code = r.status_code
        if code == 403:
            return ("OK (403 - blocked bot)", False)
        if 200 <= code < 400:
            return (f"OK ({code})", False)
        return (f"BROKEN ({code})", True)

    except requests.exceptions.ConnectionError:
        return ("BROKEN - Cannot connect", True)
    except requests.exceptions.Timeout:
        return ("BROKEN - Timed out", True)
    except requests.exceptions.RequestException as e:
        return (f"BROKEN - {str(e)[:40]}", True)


def load_df(path: str) -> pd.DataFrame:
    """Load CSV or Excel, trying multiple encodings for CSV."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        for enc in ["utf-8", "latin-1", "cp1252", "iso-8859-1"]:
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not read file — try saving as Excel instead")
    elif ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def check_file(path: str, session: requests.Session, executor: ThreadPoolExecutor,
                progress_callback, log_callback):
    """
    Checks all Image Url + product_url links in a file using the shared
    session/executor passed in.

    Returns (broken_df, total_rows, total_broken). broken_df contains ONLY
    the broken rows, plus two extra columns: 'Image 404' and 'Product 404'.
    """
    df = load_df(path)

    missing = [c for c in [IMAGE_COL, PRODUCT_COL] if c not in df.columns]
    if missing:
        raise ValueError(f'Missing columns: {", ".join(missing)}\nFound: {", ".join(df.columns)}')

    total = len(df)
    img_res = [""] * total
    prod_res = [""] * total
    done_count = 0

    tasks = []
    for i in range(total):
        img = str(df[IMAGE_COL].iloc[i]) if pd.notna(df[IMAGE_COL].iloc[i]) else ""
        prod = str(df[PRODUCT_COL].iloc[i]) if pd.notna(df[PRODUCT_COL].iloc[i]) else ""
        tasks.append((i, "image", img))
        tasks.append((i, "product", prod))

    futures = {executor.submit(check_url, session, t[2]): t for t in tasks}
    for fut in as_completed(futures):
        idx, ctype, _ = futures[fut]
        label, _broken = fut.result()

        if ctype == "image":
            img_res[idx] = label
        else:
            prod_res[idx] = label

        if img_res[idx] != "" and prod_res[idx] != "":
            done_count += 1
            pct = int(done_count / total * 100)
            progress_callback(pct, done_count, total)

            name = str(df["product name"].iloc[idx])[:40] if "product name" in df.columns else f"Row {idx + 1}"
            if "BROKEN" in img_res[idx]:
                log_callback(f"[{idx + 1}] {name} — Image: {img_res[idx]}", "bad")
            if "BROKEN" in prod_res[idx]:
                log_callback(f"[{idx + 1}] {name} — Product: {prod_res[idx]}", "bad")

    df_out = df.copy()
    df_out["Image 404"] = img_res
    df_out["Product 404"] = prod_res

    broken_mask = (
        df_out["Image 404"].str.contains("BROKEN", na=False)
        | df_out["Product 404"].str.contains("BROKEN", na=False)
    )
    broken_df = df_out[broken_mask].copy()
    total_broken = int(broken_mask.sum())
    return broken_df, total, total_broken

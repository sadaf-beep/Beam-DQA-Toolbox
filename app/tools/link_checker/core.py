"""
Link-checking engine — pure Python, no UI dependency.

Kept separate from the Flet view so the checking logic can be tested and
reused on its own.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from tools.common.data import ROW_COL, Columns, clean

IMAGE_COL = "Image Url"
PRODUCT_COL = "product_url"
WORKERS = 20
TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; LinkChecker/2.0)"

# Confirmed-dead responses — the resource is actually gone.
CONFIRMED_BROKEN_CODES = (404, 410)
# Transient responses worth one retry before judging them at all.
RETRYABLE_CODES = (429, 503)


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


def _retry_delay(response, default: float = 2.0, cap: float = 5.0) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    return default


def _classify(code: int):
    """Maps a status code to (label, is_broken).

    Only a confirmed-dead response counts as BROKEN. Everything else that
    isn't a clean success is REVIEW — the link might be fine (rate-limited,
    needs login, a flaky server error) but we can't be confident it's dead.
    """
    if code == 403:
        return ("OK (403 - blocked bot)", False)
    if 200 <= code < 400:
        return (f"OK ({code})", False)
    if code in CONFIRMED_BROKEN_CODES:
        return (f"BROKEN ({code})", True)
    return (f"REVIEW ({code})", False)


def check_url(session: requests.Session, url: str, timeout: int = TIMEOUT):
    """Returns (status_label, is_broken). Tries HEAD first (cheaper), falls
    back to GET if the server rejects HEAD or the HEAD request itself fails.
    A 429/503 (rate-limited / temporarily unavailable) gets one retry after
    a short backoff before being judged, since those are about our request
    volume or a momentary blip, not about whether the link is dead."""
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

        if r.status_code in RETRYABLE_CODES:
            time.sleep(_retry_delay(r))
            try:
                r = session.get(url, timeout=timeout, allow_redirects=True,
                                 headers=headers, stream=True)
                r.close()
            except requests.exceptions.RequestException:
                pass

        return _classify(r.status_code)

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

    Returns (broken_df, review_df, total_rows, total_broken, total_review).
    broken_df contains only confirmed-dead rows (404/410/unreachable);
    review_df contains rows with an ambiguous response (needs login, still
    rate-limited after retry, server error, etc.) that isn't confidently
    broken. Both have two extra columns: 'Image 404' and 'Product 404'.
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
            for col_label, res in (("Image", img_res[idx]), ("Product", prod_res[idx])):
                if "BROKEN" in res:
                    log_callback(f"[{idx + 1}] {name} — {col_label}: {res}", "bad")
                elif "REVIEW" in res:
                    log_callback(f"[{idx + 1}] {name} — {col_label}: {res}", "warn")

    df_out = df.copy()
    df_out["Image 404"] = img_res
    df_out["Product 404"] = prod_res

    broken_mask = (
        df_out["Image 404"].str.contains("BROKEN", na=False)
        | df_out["Product 404"].str.contains("BROKEN", na=False)
    )
    review_mask = (
        df_out["Image 404"].str.contains("REVIEW", na=False)
        | df_out["Product 404"].str.contains("REVIEW", na=False)
    ) & ~broken_mask

    broken_df = df_out[broken_mask].copy()
    review_df = df_out[review_mask].copy()
    total_broken = int(broken_mask.sum())
    total_review = int(review_mask.sum())
    return broken_df, review_df, total, total_broken, total_review


# ── Pipeline entry point ────────────────────────────────────
# Same engine contract as the other QA tools (see product_name_qa/core.py),
# used by the Full QA Pipeline on its combined DataFrame.

NAME = "Link Check"


def missing_columns(df) -> list:
    cols = Columns(df)
    if cols.image is None and cols.url is None:
        return ["Image Url or product_url"]
    return []


def run(df, log, progress=None) -> dict:
    cols = Columns(df)
    rows = list(df[ROW_COL])
    img = {r: "" for r in rows}
    prod = {r: "" for r in rows}

    tasks = []
    for _, row in df.iterrows():
        if cols.image:
            tasks.append((row[ROW_COL], "image", clean(row.get(cols.image))))
        if cols.url:
            tasks.append((row[ROW_COL], "product", clean(row.get(cols.url))))

    session, executor = new_session(), new_executor()
    try:
        futures = {executor.submit(check_url, session, t[2]): t for t in tasks}
        for done, fut in enumerate(as_completed(futures), 1):
            row, kind, _ = futures[fut]
            label, _broken = fut.result()
            (img if kind == "image" else prod)[row] = label
            if progress and (done % 25 == 0 or done == len(tasks)):
                progress(done, len(tasks))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        session.close()

    results = {}
    for r in rows:
        parts = [f"{name}: {res}" for name, res in (("Image", img[r]), ("Product URL", prod[r]))
                 if "BROKEN" in res or "REVIEW" in res]
        labels = img[r] + prod[r]
        if "BROKEN" in labels:
            status = "Broken"
        elif "REVIEW" in labels:
            status = "Review"
        elif not img[r].strip("-") and not prod[r].strip("-"):
            status = "Skip"
            parts = ["No image or product URL"]
        else:
            status = "Pass"
        results[r] = {"image_status": img[r], "product_status": prod[r],
                      "qa_status": status, "qa_note": " | ".join(parts)}
    broken = sum(1 for v in results.values() if v["qa_status"] == "Broken")
    log(f"{broken} product(s) with broken links", "bad" if broken else "ok")
    return results

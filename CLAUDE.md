# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Toolbox — Beam Dynamics' Flet-based desktop product-data QA suite for Windows (it absorbed the old QA-APP repo). Tools: Full QA Pipeline, Duplicate Detection, OEM URL Check, Link Checker, Status Checker, Product Name QA, Firmware & Docs, Warranty Validation, Dimensions Validation, File Splitter.

### QA engine contract

Every QA tool's `core.py` exposes `NAME`, `missing_columns(df)`, `run(df, ...) -> {row: result}` (a result for EVERY row, keyed by `data.ROW_COL`, each with `qa_status` + `qa_note`), `summarize(results)` and `save_excel(...)`. Claude tools take `(df, client, model, batch_size, batch_pause, log, progress, status)`; free tools take `(df, log, progress)`. `qa_pipeline/core.py` runs them in order and merges by row — never by mfr/model. Shared code lives in `tools/common/` (data, claude, excel, ui); Claude calls never retry — failures become "Needs Review" rows. Views are thin wrappers around `common.ui.tool_screen`.

## Commands

```
pip install -r requirements.txt   # install deps (Flet, pandas, requests, openpyxl)
python app/main.py                # run the app
flet build windows                # package into a windows .exe (build/windows), Windows only
```

There is no test suite, linter, or formatter configured in this repo.

## Architecture

- `app/main.py` — launcher shell. Defines a `TOOLS` list of `Tool` entries and renders a home screen of cards; clicking one imports the tool's view module (`Tool.view_module`) on demand, calls its `build(page)` and swaps it into `content_area`. Tool modules must never be imported at startup — heavy deps (pandas, anthropic) on the launch path caused ~30s cold starts. **To add a new tool**: create `app/tools/<name>/view.py` with a `build(page) -> ft.Control` function and add a `Tool(..., view_module="tools.<name>.view")` entry to `TOOLS` — it appears on the home screen automatically, no other wiring needed.
- `app/tools/base.py` — the `Tool` dataclass interface (`key`, `name`, `description`, `icon`, `build`) every tool must satisfy.
- `app/theme.py` — shared dark color palette and layout helpers (`border_all`, `card_container`) used by every tool's UI for a consistent look.
- `app/tools/link_checker/core.py` — pure-Python link-checking engine, deliberately kept free of any Flet/UI dependency so it can be tested or reused standalone.
- `app/tools/link_checker/view.py` — the Flet screen for Link Checker; wires UI state (file rows, progress, run button) to `core.py` functions.

### Link Checker concurrency model

- One `requests.Session` (connection-pooled) and one `ThreadPoolExecutor` (`core.new_session()` / `core.new_executor()`, default 20 workers) are created once in `view.py` and shared across **all** files and **all** runs in the app's lifetime — this caps total concurrent HTTP requests at `WORKERS` regardless of how many files are checked at once.
- Each file is still run on its own `threading.Thread` (`run_file`) so multiple files check in parallel, but their individual URL checks (`core.check_url`) are all submitted to the same shared executor.
- `core.check_url` tries `HEAD` first and falls back to `GET` if the server rejects `HEAD` (405/501) or the `HEAD` request raises; a `403` is treated as "OK (blocked bot)" rather than broken, since many sites 403 on requests without a browser-like session.
- Input files must have `Image Url` and `product_url` columns (`core.IMAGE_COL` / `core.PRODUCT_COL`); `core.load_df` tries multiple encodings (`utf-8`, `latin-1`, `cp1252`, `iso-8859-1`) when reading CSV.
- A response is classified via `core._classify`: 403 and 2xx/3xx are OK, 404/410 are confirmed `BROKEN`, 429/503 get one retry before being judged, and anything else ambiguous (needs login, still rate-limited, server error) is `REVIEW` rather than broken.
- `core.check_file` returns one full-report `DataFrame` covering **every** row of the input (not just problem rows), with two added columns (`Image 404`, `Product 404`) holding each row's status label. `view.py` writes this as a single `<input_basename>_LINK_STATUS_<YYYYmmdd_HHMM>.csv` per input file — so row count in the output always equals row count in the input.

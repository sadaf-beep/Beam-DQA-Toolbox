# Toolbox

A small desktop app suite. Ships two tools:

- **Link Checker** — scans CSV/Excel product feeds for broken image and
  product page links, and saves a CSV of just the broken rows for each file.
- **Status Checker** — verifies whether products in a CSV are still
  supported or discontinued, using the Claude API with web search.

## First-time setup (Windows)

1. Install **Python 3.12** from [python.org/downloads](https://www.python.org/downloads/).
   On the first install screen, check **"Add python.exe to PATH"**.
2. Download/clone this project folder onto your computer.
3. Open Command Prompt in the project folder and run:
   ```
   pip install -r requirements.txt
   ```
   This installs everything the app needs (Flet for the UI, pandas/openpyxl
   for reading spreadsheets, requests for checking links, anthropic for the
   Status Checker).

## Running the app

From the project folder:

```
python app/main.py
```

A window opens with a "Toolbox" home screen.

### Link Checker

1. **Add Files** — pick one or more CSV/Excel files (must have `Image Url`
   and `product_url` columns).
2. **Choose Folder** — pick where the results should be saved.
3. **Run Checker** — checks all files in parallel; only broken links are
   logged and saved. Each input file gets its own
   `<name>_BROKEN_LINKS_<timestamp>.csv` in the folder you chose.

### Status Checker

1. **API key** — paste your Anthropic API key (get one at
   [console.anthropic.com](https://console.anthropic.com)) and save it; it's
   stored locally and only ever sent to the Claude API.
2. **Add CSV files** — needs `mfr`, `model`, and `product_status` columns.
   `product name` and `product_url` are optional — a `product_url` lets the
   tool check that page directly before falling back to a full web search.
3. **Choose output folder**, then **Run Status Check**. Results (and a
   Summary sheet) are written incrementally to
   `Verified_Results_Claude.xlsx` as each batch completes. "Recovery mode"
   lets you re-run a job and skip products already verified in a previous
   run.

   **Cost:** each batch call is billed for model tokens plus $10 per 1,000
   web searches (the bigger cost driver, since search-result content also
   counts as input tokens). The tool defaults to Claude Haiku 4.5, the
   cheapest of the two models offered — Sonnet 4.6 is the other option, for
   more thorough (but pricier) checks. Test on a small batch first to get a
   feel for real cost before running it on a large file.

## Building a real .exe (optional, do this later)

Once the app works well for you, you can package it into a double-click
`.exe` with no console window:

```
flet build windows
```

This must be run on Windows. The finished app appears under `build/windows`.

## Project layout

```
app/
  main.py                   — launcher: home screen + tool navigation
  theme.py                  — shared colors/styling
  tools/
    base.py                 — Tool interface every tool implements
    link_checker/
      core.py               — link-checking logic (no UI code, testable on its own)
      view.py                — the Link Checker's Flet screen
    status_checker/
      core.py               — Claude API verification logic (no UI code, testable on its own)
      view.py                — the Status Checker's Flet screen
```

To add another tool later: create `app/tools/<name>/` with a `build(page)`
function, then register a `Tool(...)` entry for it in `app/main.py`'s `TOOLS`
list — it'll show up on the home screen automatically.

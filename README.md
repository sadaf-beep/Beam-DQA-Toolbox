# Toolbox

Beam Dynamics' product data QA suite — a desktop app for checking product
feeds (CSV/Excel). Every tool can run on its own, or all together:

**Using the DQA Agent (Toolbox and Google Sheets): see the
[DQA Agent User Manual](docs/DQA_Agent_Manual.md).**

- **DQA Agent** — load a product file and chat: "what's in this file?",
  "check the status of the Sony products", "what should I fix first?". It
  runs the checks below for you, follows the DQA rulebook
  (`app/tools/dqa_agent/rulebook.md`, a copy of the Google Doc), asks before
  any paid web-search check on more than 25 products, and only reports — it
  never changes the file. Uses Claude Opus 5.5 to plan and explain; the
  checks themselves use the model you pick (Haiku 5.5 by default).
- **Full QA Pipeline** — runs any selection of the checks below on all your
  files at once and writes one `Master_QA_Report_<timestamp>.xlsx`. Its
  first sheet is a short **To-Do** list: only products with a problem, one
  line per problem, with priority (High / Medium / Low), the rule it breaks
  and a suggested fix, following the team's *DQA Product QA Rulebook*
  (Google Doc). Full detail for every product is in the "All Products"
  sheet. Checks skip themselves when the files don't have their columns.
- **Duplicate Detection** — exact, cross-brand and near-match duplicates. Free.
- **OEM URL Check** — is `Page URL` the manufacturer's site or a
  retailer, and does `is_oem_url` say the same? Free.
- **Link Checker** — broken image and product page links. Free.
- **Status Checker** — are products still supported or discontinued?
  (Claude + web search)
- **Product Name QA** — do manufacturer, model and product name agree?
  (Claude, no web search — cheap)
- **Firmware & Docs** — are the `has_firmware`, `Firmware Added` and
  `documents added` flags right? (Claude + web search)
- **Warranty Validation** — is `warranty_length` right? `36500` means
  lifetime. (Claude + web search)
- **Dimensions Validation** — are `product_dimension` /
  `package_dimensions` consistent and accurate? (Claude + web search)
- **File Splitter** — split a large file into smaller CSVs.

Files are expected in the **Studio export** format (`product id`, `mfr`,
`model`, `product name`, `Image URL`, `Page URL`, `is_oem_url`, …,
`Beam Insights URL`); older exports with `product_url` / `Image Url` still
work, since columns are matched by name regardless of case. Every report
includes each product's **Product ID** and **Beam Insights URL**, and every
run has a **Stop** button — it finishes the current batch, saves what's done,
and marks the rest Needs Review.

The Claude tools share one saved API key and let you pick Claude Haiku 5.5
(default, cheapest) or Claude Sonnet 5.5 (more thorough). A batch the API
can't process is never retried — those products are marked **Needs Review**
in the report instead, so a hiccup never costs more than one call.

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
   Claude-powered checks).

## Running the app

From the project folder:

```
python app/main.py
```

A window opens with a "Toolbox" home screen.

### Link Checker

1. **Add Files** — pick one or more CSV/Excel files (needs an `Image URL`
   and/or `Page URL` column).
2. **Choose Folder** — pick where the results should be saved.
3. **Run Checker** — checks all files in parallel; only broken links are
   logged and saved. Each input file gets its own
   `<name>_BROKEN_LINKS_<timestamp>.csv` in the folder you chose.

### Status Checker

1. **API key** — paste your Anthropic API key (get one at
   [console.anthropic.com](https://console.anthropic.com)) and save it; it's
   stored locally and only ever sent to the Claude API.
2. **Add CSV files** — needs `mfr`, `model`, and `product_status` columns.
   `product name` and `Page URL` are optional — a `Page URL` lets the
   tool check that page directly before falling back to a full web search.
3. **Choose output folder**, then **Run Status Check**. Results (and a
   Summary sheet) are written incrementally to
   `Verified_Results_Claude.xlsx` as each batch completes. "Recovery mode"
   lets you re-run a job and skip products already verified in a previous
   run.

   **Cost:** each batch call is billed for model tokens plus $10 per 1,000
   web searches (the bigger cost driver, since search-result content also
   counts as input tokens). The tool defaults to Claude Haiku 5.5, the
   cheapest of the two models offered — Sonnet 5.5 is the other option, for
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
    common/
      data.py               — loading files, finding columns by name
      claude.py             — shared API key, models, Claude batch runner
      excel.py              — formatted report sheets
      ui.py                 — the shared tool screen (files, settings, run, log)
    qa_pipeline/            — Full QA Pipeline + Master QA Report
    <tool>/                 — one folder per tool (duplicates, oem_url,
      core.py                 link_checker, status_checker, product_name_qa,
      view.py                 firmware_docs, warranty, dimensions, file_splitter)
```

Each tool's `core.py` is the checking logic with no UI code, so it can be
tested and reused on its own; `view.py` is its screen. QA tools' engines all
expose the same functions (`missing_columns`, `run`, `summarize`,
`save_excel`) — that's what lets the pipeline run them in sequence.

To add another tool later: create `app/tools/<name>/view.py` with a
`build(page)` function, then register a
`Tool(..., view_module="tools.<name>.view")` entry in `app/main.py`'s `TOOLS`
list (the module is only imported when the tool is opened, so startup stays fast) — it'll show up on the home screen automatically.

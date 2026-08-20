# Toolbox

A small desktop app suite. Currently ships one tool: **Link Checker** — scans
CSV/Excel product feeds for broken image and product page links, and saves a
CSV of just the broken rows for each file.

## First-time setup (Windows)

1. Install **Python 3.12** from [python.org/downloads](https://www.python.org/downloads/).
   On the first install screen, check **"Add python.exe to PATH"**.
2. Download/clone this project folder onto your computer.
3. Open Command Prompt in the project folder and run:
   ```
   pip install -r requirements.txt
   ```
   This installs everything the app needs (Flet for the UI, pandas/openpyxl
   for reading spreadsheets, requests for checking links).

## Running the app

From the project folder:

```
python app/main.py
```

A window opens with a "Toolbox" home screen. Click **Link Checker** to open
the tool, then:

1. **Add Files** — pick one or more CSV/Excel files (must have `Image Url`
   and `product_url` columns).
2. **Choose Folder** — pick where the results should be saved.
3. **Run Checker** — checks all files in parallel; only broken links are
   logged and saved. Each input file gets its own
   `<name>_BROKEN_LINKS_<timestamp>.csv` in the folder you chose.

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
```

To add another tool later: create `app/tools/<name>/` with a `build(page)`
function, then register a `Tool(...)` entry for it in `app/main.py`'s `TOOLS`
list — it'll show up on the home screen automatically.

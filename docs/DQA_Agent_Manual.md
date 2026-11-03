# DQA Agent: User Manual

Version 1.0, November 2026. Owner: Sadaf (Beam Dynamics Data QA).

## 1. What the DQA Agent is

The DQA Agent is an assistant that checks Beam product records the way an experienced DQA team member would. You ask it questions in plain English, such as "find duplicate products" or "what should I fix first?", and it runs the right checks, explains what it found and gives you a to-do list of fixes.

There are two versions. They follow the same rules and give the same kind of results:

| | DQA Agent in the Toolbox | DQA Agent in Google Sheets |
|---|---|---|
| Where it lives | A screen in the Toolbox desktop app on your computer | A sidebar inside a Google Sheet |
| Where the products come from | A Studio export file (CSV or Excel) that you load | A tab in the Google Sheet |
| Where results go | An Excel report in a folder you choose | New tabs in the same Google Sheet |
| Best for | Large files (thousands of products), working offline from files | Smaller batches, sharing results through Google Sheets |

Things that are true for both versions:

- **It only reports.** It never changes your product data, your file, or Insights. Every fix is a suggestion for a person to make.
- **It follows the DQA Product QA Rulebook** (the Google Doc in the DQA Drive folder).
- **It asks before spending money.** Checks that search the web cost the most, so before running one on more than 25 products the agent tells you how many products and asks for your yes.
- **You can stop it at any time** with the Stop button. Whatever was finished is kept.

## 2. Before you start

You need:

1. **An Anthropic API key.** This is the password that lets the agent use Claude. It starts with `sk-ant-`. Keys are created at console.anthropic.com. The same key works in both versions.
2. **Product data in the Studio export format.** The agent recognises these column names (capital letters and spaces/underscores don't matter):

| Column | Used by |
|---|---|
| `product id` | All results (to identify the product) |
| `mfr` | Required for almost every check |
| `model` | Duplicates, Product Name, Firmware, Warranty, Dimensions |
| `product name` | Most checks (recommended) |
| `Page URL` (older files: `product_url`) | OEM URL, Links; also helps the web-search checks |
| `Image URL` | Links |
| `is_oem_url` | OEM URL |
| `product_status` | Status |
| `has_firmware`, `Firmware Added`, `documents added` | Firmware & Docs |
| `warranty_length` | Warranty |
| `product_dimension`, `package_dimensions` | Dimensions |
| `Beam Insights URL` | Shown next to each problem so you can click straight to the product |

If a column is missing, the checks that need it are simply skipped and the agent tells you why.

## 3. The checks

The agent uses eight checks. You don't have to name them; just say what you want and it picks the right one.

| Check | What it looks at | Cost |
|---|---|---|
| **Duplicates** | Products with the same model number. Separates exact duplicates, the same model under different manufacturers, models written differently (LC60EQ10U vs LC-60EQ10U, which may be a model alias) and very similar models. | Free |
| **OEM URL** | Whether the Page URL is the manufacturer's own site or a third party, and whether `is_oem_url` says the same. A third-party page marked FALSE is fine. | Free |
| **Links** | Whether the product page and image links work. | Free |
| **Product Name** | Whether manufacturer, model and product name agree, using Claude's knowledge (no web search). | Cheap |
| **Status** | Whether supported/discontinued is right. A product that still gets firmware or software support is supported. | Web search (costs the most) |
| **Firmware & Docs** | Whether `has_firmware` is right and whether firmware and documents were added. | Web search |
| **Warranty** | Whether `warranty_length` is right. `-1` and `36500` both mean lifetime. A blank warranty is flagged with the manufacturer's standard warranty as the suggested fix. | Web search |
| **Dimensions** | Whether dimensions are complete, consistent and match the manufacturer. | Web search |

### Priorities

Every problem gets a priority, taken from the rulebook:

- **High (fix first):** wrong manufacturer or model, wrong status, duplicate product, broken product page.
- **Medium (fix soon):** wrong warranty, firmware or document flags, description, category or dimension errors.
- **Low (needs a person to look):** something that couldn't be confirmed automatically.

### Results you might see

- **Needs Review**: the agent couldn't check this product (for example the API was busy, or you pressed Stop). Nothing is wrong with the product as far as anyone knows; it just needs another try or a manual look. The agent never retries on its own, so a hiccup never costs more than one call.
- **Skip**: there was nothing to check (for example no model number or product name).

### The two models

The agent uses two Claude models:

- **Claude Opus 5.5** understands your questions and explains the results. This part is cheap, because the agent only reads short summaries, never the whole file.
- **The model for checks** does the product-by-product work. You choose it: **Claude Haiku 5.5** (cheapest, the default) or **Claude Sonnet 5.5** (more thorough, costs more).

---

## Part A: The DQA Agent in the Toolbox

### A1. Opening the Toolbox

**If the Toolbox is already installed** (there is a **Toolbox** shortcut on your desktop): double-click it. The window opens in about a second.

**First-time installation on a new computer:**

1. Install **Python 3.12** from python.org/downloads. On the first install screen, tick **"Add python.exe to PATH"**.
2. Get the project folder (from GitHub: `sadaf-beep/Beam-DQA-Toolbox`) onto the computer.
3. Open Command Prompt in the project folder and run: `pip install -r requirements.txt`
4. Start the app with: `python app/main.py`

### A2. Opening the agent

On the Toolbox home screen, click the first card, **DQA Agent**.

### A3. Setting up a session

At the top of the screen:

1. **Load Files**: pick one or more Studio export files (CSV or Excel). The chat shows "Loaded …" when they're in. Loading new files starts a new conversation.
2. **Output Folder**: pick where reports should be saved.
3. **Model for checks**: leave it on **Claude Haiku 5.5 (cheapest)** unless you want more thorough checks.
4. **API key**: if no key is saved yet, a key box appears. Paste your key and press Enter. It's saved on this computer and shared with the other Toolbox tools, so you only do this once.

The chat box and suggestion buttons become active once files and an output folder are chosen.

### A4. Asking questions

Type in the box at the bottom and press **Enter** or click **Send**. Or click a suggestion:

- What's in this file?
- Find duplicate products
- Check the product pages and links
- Check the status of every product
- What should I fix first?

More examples of what you can ask:

| You ask | What the agent does |
|---|---|
| "What's in this file?" | Counts products, lists the top manufacturers and which checks the columns allow. |
| "Check warranty for all the Sony products" | Runs the Warranty check only on Sony products. |
| "Check status for these: 001251fa-…, 0056db9f-…" | Runs Status on just those product ids. |
| "Run all the free checks" | Runs Duplicates, OEM URL and Links. |
| "What should I fix first?" | Lists the High-priority items from everything checked so far. |
| "Why was the Surface Duo 2 flagged?" | Shows that product's results and explains them; may look it up online. |
| "Show me only the warranty problems" | Filters the to-do list. |
| "Save a report" | Writes the Excel report to your output folder. |

The agent answers in short plain sentences, names products by manufacturer and model, and keeps chat lists to about 10 items. The full list is always in the report.

### A5. When the agent asks for your OK

If a web-search check (Status, Firmware & Docs, Warranty, Dimensions) or Product Name would run on more than 25 products, the agent stops and asks, for example: "That's 140 products for the warranty check, which uses web search. Shall I go ahead?" Reply **yes** to run it, or narrow it down ("just the Sony ones").

Tip: test on 10 to 20 products first to get a feel for cost and time.

### A6. Stopping

Click **Stop** (red) at any time. The agent finishes the step it's on and stops. Products that weren't reached are marked **Needs Review**, and everything finished so far is kept and can still be saved in a report.

### A7. Reports

Ask "save a report" (or the agent may offer). The file is called `DQA_Agent_Report_<date>_<time>.xlsx` and is saved in your output folder. Click **Open last report** to open it.

The report has these sheets:

1. **To-Do**: only products with a problem, one line per problem, most urgent first. Columns: Priority, Manufacturer, Model, Product Name, Check, Problem, Rule (the rulebook rule it breaks), Suggested Fix, Beam Insights URL, Product ID, Source File, File Row.
2. **Summary**: how many products are fine, how many need fixing, and counts by priority and by check.
3. **All Products**: every product with every check's result and note, for anyone who needs the detail.
4. **Duplicate Pairs** (only when duplicates were found): which products match which.

### A8. Activity panel

At the bottom, **Activity** expands to show what the agent is doing behind the scenes (which check is running, how many products, any warnings). You don't need it day to day; it helps when something looks wrong.

### A9. Good to know

- One conversation covers the files you loaded. Load new files to start over.
- Bigger files cost about the same for the agent to talk about, because it reads summaries. Only the checks themselves cost more on bigger files.
- The Toolbox also has each check as its own screen, and the **Full QA Pipeline** screen, which runs any set of checks at once without chatting.

---

## Part B: The DQA Agent in Google Sheets

### B1. Where it is

The agent is installed in the Google Sheet **"DQA Agent POC - Test Products"** in the DQA Drive folder. Opening that sheet adds a **DQA Agent** menu at the top (next to Help) after a few seconds.

### B2. First-time permission (once per Google account)

1. Click **DQA Agent → Open DQA Agent**.
2. Google asks for permission. Click **Continue** and choose your Beam account.
3. If you see **"Google hasn't verified this app"**: this is normal for a script made in-house. Click **Advanced**, then **Go to DQA Agent (unsafe)**.
4. Click **Allow**. The agent asks to: see and edit this spreadsheet, show the sidebar, connect to Claude, and read the rulebook Google Doc.
5. Click **DQA Agent → Open DQA Agent** again. The chat panel opens on the right.

### B3. Settings

Click **Settings** at the top of the panel (it opens by itself the first time):

- **Anthropic API key**: paste your key and click **Save**. It's stored privately for your Google account only; other people using the sheet need their own key.
- **Model for the checks**: Haiku 5.5 (cheapest) or Sonnet 5.5 (more thorough).

Under the settings, a line shows how many products the agent found and on which tab.

### B4. Putting your products in the sheet

The agent reads products from a tab that has a header row in row 1 with an **mfr** column (the Studio export headers). It uses the tab you're on if it qualifies, otherwise the first one that does.

To check a new Studio export:

1. Make your own copy so the original stays clean: **File → Make a copy** of the test sheet. The agent is copied with it.
2. In the copy, delete the old product rows (or add a new tab) and import your export: **File → Import → Upload**, choose the file, and pick **Replace current sheet** or **Insert new sheet(s)**.
3. If the copy already has **QA Results** and **QA To-Do** tabs from earlier runs, delete both tabs before checking new data. Results are matched to products by row number, so old results would point at the wrong products. The agent creates both tabs again on its first check.
4. Open **DQA Agent → Open DQA Agent** and start asking.

### B5. Asking questions

It works like the Toolbox version: type a question and press **Enter** (Shift+Enter for a new line), or click a suggestion:

- What's in this sheet?
- Find duplicate products
- Check the product pages and links
- Check the status of every product
- What should I fix first?

All the example questions in section A4 work here too. The agent names products by manufacturer, model and **sheet row**, so you can find them quickly.

While it works, a status line shows what it's doing, for example "Warranty: 12 of 40 products checked…". Google limits each step to 6 minutes, so long checks run in several steps automatically; you don't need to do anything.

The same rule applies to paid checks on more than 25 products: the agent asks first.

### B6. Results tabs

After each check the agent writes two tabs. Your product tab is never changed.

- **QA To-Do**: the short to-do list. Only products with a problem, one line per problem, most urgent first, colour-coded (red High, yellow Medium, blue Low). Columns: Priority, Manufacturer, Model, Product Name, Check, Problem, Rule, Suggested Fix, Beam Insights URL, Product ID, Sheet Row. It's rebuilt after every check, so it always reflects the latest results.
- **QA Results**: the full record, one line per product per check (status, note and details). If a check is run again, the newest line counts. You normally don't need to open this tab.

### B7. Stopping

Click **Stop** (red). The agent finishes the step it's on and stops. Products that weren't reached are marked **Needs Review**. Ask your next question as normal afterwards.

### B8. Good to know

- **The chat lives in the sidebar.** Closing the sidebar clears the conversation, but all results stay in the QA tabs.
- **The rulebook is read live** from the Google Doc. Change a rule in the doc and the agent uses the new rule within about 10 minutes.
- **Big files:** for thousands of products, the Toolbox version is faster. The Sheets version only looks for "very similar model" duplicates when the sheet has 2,000 products or fewer (exact duplicates are always found).

---

## 4. Troubleshooting

| What you see | What to do |
|---|---|
| No **DQA Agent** menu in the sheet | Refresh the page and wait 5 to 10 seconds. Make sure you're in the sheet that has the agent (or a copy of it). |
| "Add your API key in Settings" | Open Settings, paste your key, click Save. |
| "Claude API keys start with 'sk-ant-'" | The key was pasted incompletely. Copy it again in full. |
| A message mentioning "API error 401" or "403" | The API key is wrong or has been revoked. Get a new key from console.anthropic.com and save it again. |
| A message mentioning "API error 404" | The model name isn't available on your account. Tell the person who maintains the agent. |
| "No product tab found (needs an 'mfr' column)" | The product tab's first row must be the header row with an `mfr` column. |
| A check says "the sheet/file has no … column" | That check needs a column your data doesn't have (see section 2). The other checks still work. |
| Many products show **Needs Review** | The API was busy or rate-limited, or you pressed Stop. Ask the agent to run the check again on those products. |
| Results in the Sheets version point at the wrong products | Old QA tabs were left from different data. Delete **QA Results** and **QA To-Do** and run the checks again. |
| Toolbox takes long to open | It should open in about a second; the first open after a restart can take a little longer. |

## 5. Costs and safety

- Chatting with the agent costs a few cents per question.
- The free checks (Duplicates, OEM URL, Links) cost nothing.
- Product Name is cheap. Status, Firmware & Docs, Warranty and Dimensions search the web and cost the most, roughly in proportion to the number of products.
- The agent asks before any paid check on more than 25 products, never retries a failed batch, and has a Stop button.
- Your API key is stored privately: on your computer for the Toolbox, in your Google account for the Sheets version.
- The agent never edits product data, files or Insights.

## 6. For maintainers

This section is for whoever looks after the code.

- **Code:** GitHub repo `sadaf-beep/Beam-DQA-Toolbox`, branch `claude/qa-suite`.
  - Toolbox agent: `app/tools/dqa_agent/` (`core.py` engine, `view.py` screen, `rulebook.md` agent copy of the rulebook).
  - Checks: `app/tools/<check>/core.py`; shared code in `app/tools/common/`; Full QA Pipeline and to-do logic in `app/tools/qa_pipeline/`.
  - Google Sheets agent: `sheets_agent/` (`src/` is the Apps Script project).
- **Rulebook changes:** the Sheets agent reads the Google Doc live. The Toolbox agent reads `app/tools/dqa_agent/rulebook.md`, so copy rule changes there too.
- **Changing a check's prompt, the OEM/retailer lists or the standard warranties** (in the Python code or `app/tools/common/oem_standard_warranties.csv`): run `python sheets_agent/build_reference.py` to regenerate `sheets_agent/src/Reference.js`, then push it to the sheet.
- **Pushing Sheets code changes:** from the `sheets_agent` folder run `clasp push` (Google's clasp tool, signed in with the Beam account; the Apps Script API must be turned on at script.google.com/home/usersettings). `.clasp.json` binds the code to the test sheet.
- **Models:** the agents use `claude-opus-5-5` to plan and explain; checks use `claude-haiku-5-5` or `claude-sonnet-5-5`.

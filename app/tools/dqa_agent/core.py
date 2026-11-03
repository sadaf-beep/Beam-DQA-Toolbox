"""
DQA Agent engine — pure Python, no UI dependency.

A chat assistant that behaves like a DQA team member. Claude (the "brain")
reads the request and calls tools; the tools are the Toolbox's existing QA
engines, run on the loaded product file. The agent only reports — it never
changes the file. Rules come from rulebook.md (a copy of the team's DQA
Product QA Rulebook Google Doc).

Tool results are short summaries, never the whole file, so a 10,000-row
export never has to fit in the conversation.
"""

import importlib
import json
import os
from datetime import datetime
from pathlib import Path

from tools.common import claude
from tools.common.data import ROW_COL, SOURCE_COL, Columns, clean, find_col
from tools.qa_pipeline import core as pipeline
from tools.qa_pipeline import todo

AGENT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
# Web-search checks cost money per product, so bigger runs need a yes first.
CONFIRM_ABOVE = 25
# Safety net against a runaway tool loop within one user message.
MAX_STEPS = 25

RULEBOOK = (Path(__file__).with_name("rulebook.md")).read_text(encoding="utf-8")

SYSTEM_PROMPT = f"""You are the DQA Agent for Beam Dynamics' Data QA team. You check product
records from a Studio export the way an experienced DQA team member would, following the team's
rulebook below.

How you work:
- The user has loaded one or more product files. Use your tools to look at them; never ask the
  user to paste data.
- Start with summarize_file when you don't yet know what's in the file.
- Run checks with run_check. The free checks (duplicates, oem_url, links) are cheap. The other
  checks call Claude and the web-search ones (status, firmware, warranty, dimensions) cost the
  most. If run_check says confirmation is needed, tell the user how many products and which check,
  and ask before running it with confirm=true.
- Use todo_list and product_details to answer questions about results. You can use web search
  yourself to look into a specific flagged product when the user asks why.
- You only report and suggest. You never change the file or Insights.
- When the user wants the results as a file, call save_report.

How you talk:
- The team is not technical. Use plain, short sentences. No code, no JSON, no tool names.
- Lead with the answer: what's wrong and what to do, most urgent first. Name products by
  manufacturer and model.
- Don't list more than about 10 items in chat; offer the full list in the report.
- If something couldn't be checked, say so plainly.

{RULEBOOK}
"""

CHECK_NAMES = [s.key for s in pipeline.STAGES]
_STAGES = {s.key: s for s in pipeline.STAGES}

TOOLS = [
    {
        "name": "summarize_file",
        "description": "Overview of the loaded product file(s): number of products, files, which "
                       "QA checks can run on these columns, top manufacturers, recorded statuses, "
                       "and which checks have already been run in this session.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "find_products",
        "description": "Find products in the loaded file by manufacturer and/or text in the model or "
                       "product name. Returns up to `limit` matches with product id, file and row.",
        "input_schema": {
            "type": "object",
            "properties": {
                "manufacturer": {"type": "string", "description": "Manufacturer name or part of it."},
                "text": {"type": "string", "description": "Text to look for in model or product name."},
                "limit": {"type": "integer", "description": "Max results (default 20)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "run_check",
        "description": "Run one QA check on the loaded products, or on a subset (by manufacturer, "
                       "text, or product ids). Checks: duplicates, oem_url, links (free); name_qa "
                       "(Claude, cheap); status, firmware, warranty, dimensions (Claude + web search, "
                       "cost the most). Returns how many problems were found and the top to-do items. "
                       f"Paid checks on more than {CONFIRM_ABOVE} products need confirm=true, which you "
                       "only set after the user has agreed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "check": {"type": "string", "enum": CHECK_NAMES},
                "manufacturer": {"type": "string"},
                "text": {"type": "string"},
                "product_ids": {"type": "array", "items": {"type": "string"}},
                "confirm": {"type": "boolean"},
            },
            "required": ["check"],
            "additionalProperties": False,
        },
    },
    {
        "name": "todo_list",
        "description": "The to-do list from every check run so far: one line per problem with "
                       "priority, product, problem, rule and suggested fix. Optionally filter by "
                       "priority (High, Medium, Low), check, or manufacturer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "priority": {"type": "string", "enum": todo.PRIORITY_ORDER},
                "check": {"type": "string", "enum": CHECK_NAMES},
                "manufacturer": {"type": "string"},
                "limit": {"type": "integer", "description": "Max lines (default 15)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "product_details",
        "description": "Everything known about specific products: their recorded fields and the "
                       "result and note from every check run on them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Model, product id or name to look up."},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_report",
        "description": "Save the results so far as an Excel report (To-Do sheet first) in the "
                       "user's output folder. Returns the file path.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class StopRequested(Exception):
    pass


class AgentSession:
    """One conversation about one set of loaded files."""

    def __init__(self, df, client, *, check_model: str, out_dir: str, log, status=None,
                 progress=None, stop=None, batch_size: int = 5, batch_pause: int = 5):
        self.df = df
        self.client = client
        self.check_model = check_model
        self.out_dir = out_dir
        self.log = log
        self.status = status or (lambda s: None)
        self.progress = progress or (lambda f: None)
        self.stop = stop
        self.batch_size = batch_size
        self.batch_pause = batch_pause
        self.cols = Columns(df)
        self.results = {}       # check key -> {row: result}
        self.notes = {}         # check key -> one-line outcome
        self.messages = []

    # ── Conversation ────────────────────────────────────────

    def chat(self, user_text: str, on_reply) -> None:
        """Handle one user message. on_reply(text) is called for each piece
        of text Claude writes."""
        self.messages.append({"role": "user", "content": user_text})
        tools = TOOLS + [claude.web_search_tool(AGENT_MODEL)]
        for _ in range(MAX_STEPS):
            if self.stop is not None and self.stop.is_set():
                on_reply("Stopped.")
                return
            self.status("Thinking...")
            resp = self.client.beta.messages.create(
                model=AGENT_MODEL,
                max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=self.messages,
                output_config={"effort": "medium"},
                # On a safety decline, the API re-runs the turn on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            # The whole content goes back next turn (thinking, search results, tool calls).
            self.messages.append({"role": "assistant", "content": resp.content})
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    on_reply(block.text)

            if resp.stop_reason == "tool_use":
                results = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue
                    try:
                        out = self._run_tool(block.name, block.input or {})
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": json.dumps(out, default=str)})
                    except StopRequested:
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": "The user stopped this.", "is_error": True})
                    except Exception as ex:
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": f"Error: {str(ex)[:300]}", "is_error": True})
                self.messages.append({"role": "user", "content": results})
                continue
            if resp.stop_reason == "pause_turn":
                continue
            if resp.stop_reason == "refusal":
                on_reply("I can't help with that request.")
            return
        on_reply("I've stopped here because this took too many steps. Ask me to continue if needed.")

    # ── Tools ───────────────────────────────────────────────

    def _run_tool(self, name: str, args: dict):
        self.log(f"→ {name} {json.dumps(args) if args else ''}", "dim")
        fn = {"summarize_file": self.summarize_file, "find_products": self.find_products,
              "run_check": self.run_check, "todo_list": self.todo_list,
              "product_details": self.product_details, "save_report": self.save_report}[name]
        return fn(**args)

    def _product(self, row) -> dict:
        p = self.cols.product(row)
        return {"manufacturer": p["mfr"], "model": p["model"], "name": p["product_name"],
                "product_id": self.cols.refs(row)["Product ID"],
                "file": row.get(SOURCE_COL, ""), "row_in_file": self._file_row[row[ROW_COL]]}

    @property
    def _file_row(self) -> dict:
        if not hasattr(self, "_file_row_cache"):
            pos = self.df.groupby(SOURCE_COL).cumcount() + 1
            self._file_row_cache = dict(zip(self.df[ROW_COL], pos))
        return self._file_row_cache

    def _subset(self, manufacturer=None, text=None, product_ids=None):
        df = self.df
        if product_ids and self.cols.product_id:
            ids = {str(i).strip().lower() for i in product_ids}
            df = df[df[self.cols.product_id].astype(str).str.strip().str.lower().isin(ids)]
        if manufacturer and self.cols.mfr:
            df = df[df[self.cols.mfr].astype(str).str.contains(manufacturer, case=False, regex=False, na=False)]
        if text:
            mask = False
            for c in (self.cols.model, self.cols.name, self.cols.product_id):
                if c:
                    mask = mask | df[c].astype(str).str.contains(text, case=False, regex=False, na=False)
            df = df[mask]
        return df

    def summarize_file(self):
        df, cols = self.df, self.cols
        out = {
            "products": len(df),
            "files": sorted(df[SOURCE_COL].unique().tolist()),
            "checks_available": {},
            "top_manufacturers": df[cols.mfr].value_counts().head(10).to_dict() if cols.mfr else {},
            "checks_run_so_far": self.notes,
        }
        status_col = find_col(df, "product_status", "status")
        if status_col:
            out["recorded_status"] = df[status_col].fillna("blank").value_counts().to_dict()
        for key, stage in _STAGES.items():
            missing = importlib.import_module(stage.module).missing_columns(df)
            out["checks_available"][key] = "yes" if not missing else f"no — missing {', '.join(missing)}"
        return out

    def find_products(self, manufacturer=None, text=None, limit=20):
        df = self._subset(manufacturer, text)
        return {"matches": len(df), "products": [self._product(r) for _, r in df.head(limit or 20).iterrows()]}

    def run_check(self, check, manufacturer=None, text=None, product_ids=None, confirm=False):
        stage = _STAGES[check]
        df = self._subset(manufacturer, text, product_ids)
        if df.empty:
            return {"error": "No products match that selection."}
        mod = importlib.import_module(stage.module)
        missing = mod.missing_columns(df)
        if missing:
            return {"error": f"This file has no {', '.join(missing)} column, so {check} can't run."}
        if stage.uses_api and len(df) > CONFIRM_ABOVE and not confirm:
            return {"needs_confirmation": True, "check": check, "products": len(df),
                    "uses_web_search": stage.uses_search,
                    "message": f"{len(df)} products would be sent to Claude for {stage.label}. "
                               "Ask the user before running it."}

        self.log(f"Running {stage.label} on {len(df)} product(s)...", "info")
        self.status(f"Running {stage.label} on {len(df)} product(s)...")

        def progress(done, total):
            self.progress(done / max(total, 1))

        if stage.uses_api:
            res = mod.run(df, self.client, self.check_model, self.batch_size, self.batch_pause, self.log,
                          progress=progress, status=self.status, stop=self.stop)
        elif check == "duplicates":
            # Duplicates compare against the whole file, then report on the selection.
            res = {r: v for r, v in mod.run(self.df, self.log, progress=progress, stop=self.stop).items()
                   if r in set(df[ROW_COL])}
        else:
            res = mod.run(df, self.log, progress=progress, stop=self.stop)
        self.progress(1.0)
        # Keep what finished (unreached products come back as Needs Review)
        # even when the user stopped the run.
        self.results.setdefault(check, {}).update(res)
        if self.stop is not None and self.stop.is_set():
            raise StopRequested()

        flagged = sum(1 for r in res.values() if todo.items_for(check, r))
        lines = self._todo_lines(check=check, rows=set(res))
        self.notes[check] = f"{len(res)} checked, {flagged} with problems"
        return {"checked": len(res), "products_with_problems": flagged,
                "by_priority": _count_by(lines, "priority"), "top_items": lines[:10]}

    def _todo_lines(self, priority=None, check=None, manufacturer=None, rows=None) -> list:
        if not self.results:
            return []
        master = pipeline.build_master_rows(self.df, self.results)
        ran = [s for s in pipeline.STAGES if s.key in self.results and (check is None or s.key == check)]
        out = []
        for r in todo.build_rows(master, self.results, ran):
            if priority and r["Priority"] != priority:
                continue
            if manufacturer and manufacturer.lower() not in str(r["Manufacturer"]).lower():
                continue
            if rows is not None and r["_row"] not in rows:
                continue
            out.append({"priority": r["Priority"], "manufacturer": r["Manufacturer"], "model": r["Model"],
                        "check": r["Check"], "problem": r["Problem"], "fix": r["Suggested Fix"][:300],
                        "insights_link": r["Beam Insights URL"]})
        return out

    def todo_list(self, priority=None, check=None, manufacturer=None, limit=15):
        lines = self._todo_lines(priority, check, manufacturer)
        if not self.results:
            return {"message": "No checks have been run yet."}
        return {"total_items": len(lines), "by_priority": _count_by(lines, "priority"),
                "items": lines[:limit or 15]}

    def product_details(self, text):
        df = self._subset(text=text)
        if df.empty:
            return {"error": "No product matches that."}
        out = []
        for _, row in df.head(5).iterrows():
            rid = row[ROW_COL]
            fields = {c: clean(row.get(c)) for c in self.df.columns
                      if c not in (ROW_COL,) and clean(row.get(c)) and len(clean(row.get(c))) < 300}
            checks = {}
            for key, res in self.results.items():
                r = res.get(rid)
                if r:
                    checks[key] = {"status": r.get("qa_status"), "note": r.get("qa_note"),
                                   "todo": [i[1] + " — " + i[3] for i in todo.items_for(key, r)]}
            out.append({"product": self._product(row), "recorded_fields": fields, "check_results": checks})
        return {"matches": len(df), "products": out}

    def save_report(self):
        if not self.results:
            return {"error": "No checks have been run yet, so there's nothing to save."}
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, f"DQA_Agent_Report_{datetime.now():%Y%m%d_%H%M}.xlsx")
        pipeline.save_master_report(self.df, self.results, self.notes, path)
        self.last_report = path
        return {"saved_to": path}


def _count_by(items: list, key: str) -> dict:
    out = {}
    for i in items:
        out[i[key]] = out.get(i[key], 0) + 1
    return out

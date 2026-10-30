"""
Shared Claude API plumbing for the AI-backed QA tools — pure Python, no UI.

One saved API key for the whole Toolbox, the model choices, and a generic
batch runner: products go to Claude a few at a time, each tagged with an
`_id` so results are matched back to the exact input row even if the model
reorders or drops some. Failures are never retried — a batch the API can't
process comes back flagged for human review, so a rate limit or a bad
response doesn't spend more time/money chasing the same products.
"""

import json
import os
import re
import time
from pathlib import Path

import anthropic

# Haiku vs. Sonnet — Opus is left out on purpose, it's not worth the extra
# cost for this kind of lookup/classification task.
MODELS = {
    "Claude Haiku 5.5 (cheapest)": "claude-haiku-5-5",
    "Claude Sonnet 5.5 (more thorough)": "claude-sonnet-5-5",
}
DEFAULT_MODEL_LABEL = "Claude Haiku 5.5 (cheapest)"

# The dynamic-filtering web search variant is only available on Sonnet/Opus;
# Haiku keeps the basic one.
_SEARCH_TOOL_TYPE = {
    "claude-sonnet-5-5": "web_search_20260209",
}
_DEFAULT_SEARCH_TOOL_TYPE = "web_search_20250305"

MAX_TOKENS = 16000
# A web-search turn can pause mid-way (server-side iteration limit); resume
# it at most this many times before giving up on the batch.
MAX_CONTINUATIONS = 3

CONFIG_DIR = Path(os.getenv("APPDATA", Path.home())) / "Toolbox"
CONFIG_FILE = CONFIG_DIR / "config.json"
_LEGACY_CONFIG_FILE = CONFIG_DIR / "StatusChecker" / "config.json"

NEEDS_REVIEW = "Needs Review"

# Status codes where every remaining call is guaranteed to fail the same
# way (bad/expired key, no access, wrong model id) — the whole run should
# stop with one clear message instead of flagging batch after batch.
FATAL_STATUS_CODES = {400, 401, 403, 404}


class FatalAPIError(Exception):
    """The run itself can't succeed, not just this one batch."""


# ── Config ──────────────────────────────────────────────────

def load_cfg() -> dict:
    """Shared Toolbox config. Picks up a key saved by the older standalone
    Status Checker config the first time, so nobody has to re-enter it."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    if not cfg.get("claude_api_key") and _LEGACY_CONFIG_FILE.exists():
        try:
            legacy = json.loads(_LEGACY_CONFIG_FILE.read_text())
            if legacy.get("claude_api_key"):
                cfg["claude_api_key"] = legacy["claude_api_key"]
                save_cfg(cfg)
        except (OSError, ValueError):
            pass
    return cfg


def save_cfg(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def new_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


# ── Single call ─────────────────────────────────────────────

def web_search_tool(model: str) -> dict:
    return {"type": _SEARCH_TOOL_TYPE.get(model, _DEFAULT_SEARCH_TOOL_TYPE), "name": "web_search"}


def extract_json(raw: str):
    """Robustly extract a JSON array from a model response."""
    raw = raw.strip().replace("```json", "").replace("```", "").strip()
    raw = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", raw)  # stray citation markers like [1, 2]
    s, e = raw.find("["), raw.rfind("]")
    if s == -1 or e == -1:
        raise ValueError("No JSON array found in response")
    return json.loads(raw[s:e + 1])


def ask_json(client: anthropic.Anthropic, model: str, system_prompt: str,
             prompt: str, use_search: bool):
    """One request that must answer with a JSON array. Resumes paused
    web-search turns. Raises ValueError on an unusable answer (refusal,
    truncation, unparseable JSON) and lets API errors propagate."""
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
    )
    if use_search:
        kwargs["tools"] = [web_search_tool(model)]

    messages = [{"role": "user", "content": prompt}]
    resp = client.messages.create(messages=messages, **kwargs)
    for _ in range(MAX_CONTINUATIONS):
        if resp.stop_reason != "pause_turn":
            break
        messages = [{"role": "user", "content": prompt},
                    {"role": "assistant", "content": resp.content}]
        resp = client.messages.create(messages=messages, **kwargs)

    if resp.stop_reason == "refusal":
        raise ValueError("Model declined the request")
    if resp.stop_reason == "max_tokens":
        raise ValueError("Response was cut off (too long)")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return extract_json(text)


# ── Batch runner ────────────────────────────────────────────

def run_batches(client, model: str, records: list, system_prompt: str, *,
                use_search: bool, batch_size: int, batch_pause: int,
                review_result, log, progress=None, status=None,
                label: str = "") -> dict:
    """
    Send `records` to Claude `batch_size` at a time.

    Each record must carry an "_row" key (its position in the combined
    DataFrame); it's sent as "_id" and must be echoed back. Returns
    {row: result_dict} with an entry for EVERY input row — rows the model
    didn't answer, or whose batch failed, get review_result(record, reason).

    log(msg, tag), progress(done_batches, total_batches), status(text) are
    UI callbacks. Raises FatalAPIError when the run can't succeed at all.
    """
    results = {}
    total = len(records)
    if total == 0:
        return results
    num_batches = (total + batch_size - 1) // batch_size
    prefix = f"{label}  •  " if label else ""

    for bn, start in enumerate(range(0, total, batch_size), 1):
        batch = records[start:start + batch_size]
        by_id = {str(r["_row"]): r for r in batch}
        payload = []
        for r in batch:
            item = {k: v for k, v in r.items() if k != "_row"}
            item["_id"] = str(r["_row"])
            payload.append(item)

        log(f"Batch {bn}/{num_batches} ({len(batch)} products)...", "dim")
        if status:
            status(f"{prefix}batch {bn}/{num_batches}")

        prompt = (
            f"Check these {len(payload)} products.\n"
            "Every input object has an '_id' field. Echo '_id' back unchanged in "
            "every output object — that's how results are matched to inputs.\n\n"
            f"{json.dumps(payload, indent=2)}"
        )

        reason = None
        answered = []
        try:
            answered = ask_json(client, model, system_prompt, prompt, use_search)
        except (ValueError, json.JSONDecodeError) as e:
            reason = f"Unusable model response: {str(e)[:80]}"
            log(f"{reason} — flagging batch for human review", "warn")
        except anthropic.RateLimitError:
            reason = "Rate limited by the API"
            log("Rate limited — flagging batch for human review", "warn")
        except anthropic.APIStatusError as e:
            if e.status_code in FATAL_STATUS_CODES:
                raise FatalAPIError(
                    f"API error {e.status_code} — check your API key and model selection") from e
            reason = "API was overloaded (529)" if e.status_code == 529 else f"API error ({e.status_code})"
            log(f"{reason} — flagging batch for human review", "warn")
        except anthropic.APIConnectionError:
            reason = "Could not reach the API (network)"
            log(f"{reason} — flagging batch for human review", "warn")

        got = 0
        for r in answered if isinstance(answered, list) else []:
            if not isinstance(r, dict):
                continue
            rid = str(r.pop("_id", ""))
            if rid in by_id and int(rid) not in results:
                results[int(rid)] = r
                got += 1

        missing = [r for rid, r in by_id.items() if int(rid) not in results]
        if missing:
            why = reason or "Model did not return a result for this product"
            if not reason:
                log(f"{len(missing)} product(s) missing from the response — flagging for review", "warn")
            for r in missing:
                results[r["_row"]] = review_result(r, why)
        if got:
            log(f"{got} result(s) received", "ok")

        if progress:
            progress(bn, num_batches)
        if bn < num_batches and batch_pause > 0:
            for s in range(batch_pause, 0, -1):
                if status:
                    status(f"{prefix}pausing {s}s before next batch...")
                time.sleep(1)

    return results

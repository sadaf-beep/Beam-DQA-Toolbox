/**
 * The DQA Agent loop (port of app/tools/dqa_agent/core.py).
 *
 * Google stops a script call after 6 minutes, so the sidebar drives the
 * conversation one step at a time: each agentStep() call does either one
 * Claude turn or some tool work, then returns. The conversation lives in the
 * sidebar (state.messages); tool work that runs out of time is resumed on
 * the next step from what was already saved to the QA Results tab.
 */

var AGENT_MODEL = 'claude-opus-5-5';
var STEP_BUDGET_MS = 4.5 * 60 * 1000;
var CONFIRM_ABOVE = 25;
var CHECK_BATCH_SIZE = 3;

function systemPrompt_() {
  return [
    "You are the DQA Agent for Beam Dynamics' Data QA team, working inside a Google Sheet. You check",
    'product records the way an experienced DQA team member would, following the rulebook below.',
    '',
    'How you work:',
    '- The products are in the sheet. Use your tools to look at them; never ask the user to paste data.',
    "- Start with summarize_sheet when you don't yet know what's in the sheet.",
    '- Run checks with run_check. Free checks: duplicates, oem_url, links. name_qa uses Claude (cheap).',
    '  status, firmware, warranty and dimensions use Claude with web search and cost the most. If',
    '  run_check says confirmation is needed, tell the user how many products and which check, and ask',
    '  before running it with confirm=true.',
    '- Results are written to the "QA To-Do" tab (problems only, most urgent first) and the "QA Results"',
    '  tab automatically after each check. Point the user to the QA To-Do tab.',
    '- Use todo_list and product_details to answer questions. You may use web search yourself to look',
    '  into a specific product when asked why it was flagged.',
    '- You only report and suggest. You never change the product data.',
    '',
    'How you talk:',
    '- The user is not technical. Plain, short sentences. No code, no JSON, no tool names.',
    "- Lead with the answer: what's wrong and what to do, most urgent first. Name products by",
    '  manufacturer and model, and give the sheet row.',
    '- No more than about 10 items in chat; the full list is in the QA To-Do tab.',
    "- If something couldn't be checked, say so plainly.",
    '',
    '=== DQA PRODUCT QA RULEBOOK (live from the team Google Doc) ===',
    rulebook_()
  ].join('\n');
}

var AGENT_TOOLS = [
  {name: 'summarize_sheet',
   description: 'Overview of the product sheet: number of products, which checks can run on its columns, ' +
     'top manufacturers, recorded statuses, and which checks already have results.',
   input_schema: {type: 'object', properties: {}, additionalProperties: false}},
  {name: 'find_products',
   description: 'Find products by manufacturer and/or text in the model, product name or product id.',
   input_schema: {type: 'object', properties: {
     manufacturer: {type: 'string'}, text: {type: 'string'}, limit: {type: 'integer'}},
     additionalProperties: false}},
  {name: 'run_check',
   description: 'Run one QA check on all products or a selection (manufacturer, text, or product ids). ' +
     'Checks: duplicates, oem_url, links (free); name_qa (Claude, cheap); status, firmware, warranty, ' +
     'dimensions (Claude + web search, cost the most). Writes results to the QA To-Do and QA Results tabs ' +
     'and returns a summary. Paid checks on more than ' + CONFIRM_ABOVE + ' products need confirm=true, ' +
     'which you only set after the user agreed.',
   input_schema: {type: 'object', properties: {
     check: {type: 'string', enum: CHECKS}, manufacturer: {type: 'string'}, text: {type: 'string'},
     product_ids: {type: 'array', items: {type: 'string'}}, confirm: {type: 'boolean'}},
     required: ['check'], additionalProperties: false}},
  {name: 'todo_list',
   description: 'The current to-do list (all checks so far), optionally filtered by priority, check or manufacturer.',
   input_schema: {type: 'object', properties: {
     priority: {type: 'string', enum: ['High', 'Medium', 'Low']}, check: {type: 'string', enum: CHECKS},
     manufacturer: {type: 'string'}, limit: {type: 'integer'}}, additionalProperties: false}},
  {name: 'product_details',
   description: "Everything about specific products: their sheet values and every check's result and note.",
   input_schema: {type: 'object', properties: {text: {type: 'string'}}, required: ['text'],
     additionalProperties: false}}
];

/**
 * One step of the conversation. state = {messages, pending, input}; returns
 * {state, replies: [text], done, status}. The sidebar calls this until done.
 */
function agentStep(state) {
  var deadline = Date.now() + STEP_BUDGET_MS;
  var replies = [];
  state.messages = state.messages || [];

  if (state.input) {
    clearStop_();
    var content = [];
    // A stopped turn can leave tool calls without results; close them first.
    if (state.pending) {
      state.pending.uses.forEach(function (u) {
        if (!state.pending.results[u.id]) {
          state.pending.results[u.id] = {type: 'tool_result', tool_use_id: u.id, content: 'The user stopped this.', is_error: true};
        }
      });
      content = state.pending.uses.map(function (u) { return state.pending.results[u.id]; });
      state.pending = null;
    }
    content.push({type: 'text', text: state.input});
    state.messages.push({role: 'user', content: content});
    state.input = null;
  }

  // Tool work in progress: continue it.
  if (state.pending) {
    var p = state.pending;
    for (var i = 0; i < p.uses.length; i++) {
      var u = p.uses[i];
      if (p.results[u.id]) continue;
      var out;
      try {
        out = runTool_(u, deadline);
      } catch (e) {
        if (e instanceof FatalApiError) throw e;
        out = {done: true, output: 'Error: ' + String(e.message).slice(0, 300), isError: true};
      }
      if (!out.done) {
        return {state: state, replies: replies, done: false, status: out.status || 'Working...'};
      }
      p.results[u.id] = {type: 'tool_result', tool_use_id: u.id,
                         content: typeof out.output === 'string' ? out.output : JSON.stringify(out.output)};
      if (out.isError) p.results[u.id].is_error = true;
    }
    state.messages.push({role: 'user', content: p.uses.map(function (x) { return p.results[x.id]; })});
    state.pending = null;
    return {state: state, replies: replies, done: false, status: 'Thinking...'};
  }

  if (stopRequested_()) return {state: state, replies: ['Stopped.'], done: true};

  var resp = callClaude_({
    model: AGENT_MODEL, max_tokens: 16000,
    system: [{type: 'text', text: systemPrompt_(), cache_control: {type: 'ephemeral'}}],
    tools: AGENT_TOOLS.concat([webSearchTool_(AGENT_MODEL)]),
    messages: state.messages,
    output_config: {effort: 'medium'},
    fallbacks: 'default'
  }, ['server-side-fallback-2026-07-01']);
  state.messages.push({role: 'assistant', content: resp.content});
  (resp.content || []).forEach(function (b) { if (b.type === 'text' && b.text.trim()) replies.push(b.text); });

  if (resp.stop_reason === 'tool_use') {
    state.pending = {uses: resp.content.filter(function (b) { return b.type === 'tool_use'; })
      .map(function (b) { return {id: b.id, name: b.name, input: b.input || {}}; }), results: {}};
    return {state: state, replies: replies, done: false, status: describeTools_(state.pending.uses)};
  }
  if (resp.stop_reason === 'pause_turn') return {state: state, replies: replies, done: false, status: 'Searching...'};
  if (resp.stop_reason === 'refusal') replies.push("I can't help with that request.");
  return {state: state, replies: replies, done: true};
}

function describeTools_(uses) {
  var u = uses[0];
  if (u.name === 'run_check') return 'Running ' + (CHECK_LABELS[u.input.check] || u.input.check) + '...';
  return 'Looking at the sheet...';
}

// ── Tools ─────────────────────────────────────────────────

function select_(products, args) {
  var out = products;
  if (args.product_ids && args.product_ids.length) {
    var ids = args.product_ids.map(function (x) { return String(x).trim().toLowerCase(); });
    out = out.filter(function (p) { return ids.indexOf(p.productId.toLowerCase()) >= 0; });
  }
  if (args.manufacturer) {
    var m = args.manufacturer.toLowerCase();
    out = out.filter(function (p) { return p.mfr.toLowerCase().indexOf(m) >= 0; });
  }
  if (args.text) {
    var t = args.text.toLowerCase();
    out = out.filter(function (p) {
      return [p.model, p.name, p.productId].some(function (v) { return String(v).toLowerCase().indexOf(t) >= 0; });
    });
  }
  return out;
}

function brief_(p) {
  return {manufacturer: p.mfr, model: p.model, name: p.name, product_id: p.productId, sheet_row: p._row};
}

function todoLines_(products, filter) {
  filter = filter || {};
  return buildTodo_(products, loadResults_()).filter(function (t) {
    if (filter.priority && t.priority !== filter.priority) return false;
    if (filter.check && t.check !== filter.check) return false;
    if (filter.manufacturer && t.product.mfr.toLowerCase().indexOf(filter.manufacturer.toLowerCase()) < 0) return false;
    if (filter.rows && filter.rows.indexOf(t.product._row) < 0) return false;
    return true;
  }).map(function (t) {
    return {priority: t.priority, manufacturer: t.product.mfr, model: t.product.model, sheet_row: t.product._row,
            check: CHECK_LABELS[t.check], problem: t.problem, fix: String(t.fix).slice(0, 300)};
  });
}

function countBy_(items, key) {
  var out = {};
  items.forEach(function (i) { out[i[key]] = (out[i[key]] || 0) + 1; });
  return out;
}

/** Returns {done, output} or {done: false, status} when it needs another step. */
function runTool_(use, deadline) {
  var a = use.input || {};
  var data = readProducts_();
  var products = data.products, headers = data.headers;

  if (use.name === 'summarize_sheet') {
    var mfrs = countBy_(products.map(function (p) { return {m: p.mfr || '(blank)'}; }), 'm');
    var top = Object.keys(mfrs).sort(function (x, y) { return mfrs[y] - mfrs[x]; }).slice(0, 10);
    var available = {};
    CHECKS.forEach(function (c) {
      var miss = missingColumns_(c, headers);
      available[c] = miss.length ? 'no — missing ' + miss.join(', ') : 'yes';
    });
    var results = loadResults_(), ran = {};
    Object.keys(results).forEach(function (c) { ran[c] = Object.keys(results[c]).length + ' products have results'; });
    var sCol = statusCol_(headers), statuses = {};
    if (sCol >= 0) statuses = countBy_(products.map(function (p) { return {s: String(p.raw[headers[sCol]]).trim() || 'blank'}; }), 's');
    var topM = {};
    top.forEach(function (k) { topM[k] = mfrs[k]; });
    return {done: true, output: {sheet: data.sheet.getName(), products: products.length, checks_available: available,
                                  top_manufacturers: topM, recorded_status: statuses, checks_with_results: ran}};
  }

  if (use.name === 'find_products') {
    var found = select_(products, a);
    return {done: true, output: {matches: found.length, products: found.slice(0, a.limit || 20).map(brief_)}};
  }

  if (use.name === 'todo_list') {
    var lines = todoLines_(products, a);
    return {done: true, output: {total_items: lines.length, by_priority: countBy_(lines, 'priority'),
                                  items: lines.slice(0, a.limit || 15)}};
  }

  if (use.name === 'product_details') {
    var hits = select_(products, {text: a.text});
    if (!hits.length) return {done: true, output: {error: 'No product matches that.'}};
    var res = loadResults_();
    return {done: true, output: {matches: hits.length, products: hits.slice(0, 5).map(function (p) {
      var checks = {};
      CHECKS.forEach(function (c) {
        var r = (res[c] || {})[p._row];
        if (r) checks[c] = {status: r.qa_status, note: r.qa_note,
                            todo: todoItems_(c, r).map(function (t) { return t[1] + ' — ' + t[3]; })};
      });
      var fields = {};
      Object.keys(p.raw).forEach(function (k) { var v = String(p.raw[k]); if (v && v.length < 300) fields[k] = v; });
      return {product: brief_(p), sheet_values: fields, check_results: checks};
    })}};
  }

  if (use.name === 'run_check') {
    var check = a.check;
    var sel = select_(products, a);
    if (!sel.length) return {done: true, output: {error: 'No products match that selection.'}};
    var miss = missingColumns_(check, headers);
    if (miss.length) return {done: true, output: {error: 'The sheet has no ' + miss.join(', ') + ' column, so this check can\'t run.'}};
    if (check in AI_CHECKS && sel.length > CONFIRM_ABOVE && !a.confirm) {
      return {done: true, output: {needs_confirmation: true, check: check, products: sel.length,
                                    message: sel.length + ' products would be sent to Claude for ' + CHECK_LABELS[check] + '. Ask the user first.'}};
    }
    var outcome = runCheck_(check, sel, products, headers, {
      jobId: use.id, deadline: deadline, model: checkModel_(), batchSize: CHECK_BATCH_SIZE});
    if (!outcome.complete) {
      return {done: false, status: CHECK_LABELS[check] + ': ' + outcome.progress + ' of ' + sel.length + ' products checked...'};
    }
    writeTodo_();
    var rows = sel.map(function (p) { return p._row; });
    var flagged = rows.filter(function (r) { return todoItems_(check, outcome.results[r]).length; }).length;
    var items = todoLines_(products, {check: check, rows: rows});
    return {done: true, output: {checked: Object.keys(outcome.results).length, products_with_problems: flagged,
                                  stopped: stopRequested_(), by_priority: countBy_(items, 'priority'),
                                  top_items: items.slice(0, 10), written_to: 'QA To-Do tab'}};
  }
  return {done: true, output: 'Unknown tool ' + use.name, isError: true};
}

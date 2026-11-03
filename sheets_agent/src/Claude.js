/**
 * Claude API over UrlFetchApp (Apps Script has no Anthropic SDK), mirroring
 * the Toolbox's tools/common/claude.py: one JSON-array request per batch,
 * results matched back by _id, failures flagged "Needs Review" instead of
 * retried, and errors that doom the whole run raised as FatalApiError.
 */

var API_URL = 'https://api.anthropic.com/v1/messages';
var API_VERSION = '2026-10-21';
var NEEDS_REVIEW = 'Needs Review';
var STOPPED_NOTE = 'Run was stopped before this product was checked';
var FATAL_CODES = [400, 401, 403, 404];

function FatalApiError(message) { this.name = 'FatalApiError'; this.message = message; }
FatalApiError.prototype = Object.create(Error.prototype);

function ApiError(code, message) { this.name = 'ApiError'; this.code = code; this.message = message; }
ApiError.prototype = Object.create(Error.prototype);

function webSearchTool_(model) {
  var dynamic = model === 'claude-sonnet-5-5' || model === 'claude-opus-5-5';
  return {type: dynamic ? 'web_search_20260209' : 'web_search_20250305', name: 'web_search'};
}

/** POST one Messages request. Throws ApiError on an HTTP error. */
function callClaude_(body, betas) {
  var headers = {'x-api-key': apiKey_(), 'anthropic-version': API_VERSION};
  if (betas && betas.length) headers['anthropic-beta'] = betas.join(',');
  var resp = UrlFetchApp.fetch(API_URL, {
    method: 'post', contentType: 'application/json', headers: headers,
    payload: JSON.stringify(body), muteHttpExceptions: true
  });
  var code = resp.getResponseCode();
  var json = {};
  try { json = JSON.parse(resp.getContentText()); } catch (e) { json = {}; }
  if (code >= 400) {
    var msg = (json.error && json.error.message) || resp.getContentText().slice(0, 200);
    throw new ApiError(code, msg);
  }
  return json;
}

function extractJson_(raw) {
  raw = String(raw).replace(/```json/g, '').replace(/```/g, '').trim();
  raw = raw.replace(/\[\d+(?:,\s*\d+)*\]/g, '');
  var s = raw.indexOf('['), e = raw.lastIndexOf(']');
  if (s === -1 || e === -1) throw new Error('No JSON array found in response');
  return JSON.parse(raw.slice(s, e + 1));
}

/** One request that must answer with a JSON array. Resumes paused web-search turns. */
function askJson_(model, systemPrompt, prompt, useSearch) {
  var body = {
    model: model, max_tokens: 16000,
    system: [{type: 'text', text: systemPrompt, cache_control: {type: 'ephemeral'}}],
    messages: [{role: 'user', content: prompt}]
  };
  if (useSearch) body.tools = [webSearchTool_(model)];
  var resp = callClaude_(body);
  for (var i = 0; i < 3 && resp.stop_reason === 'pause_turn'; i++) {
    body.messages = [{role: 'user', content: prompt}, {role: 'assistant', content: resp.content}];
    resp = callClaude_(body);
  }
  if (resp.stop_reason === 'refusal') throw new Error('Model declined the request');
  if (resp.stop_reason === 'max_tokens') throw new Error('Response was cut off (too long)');
  var text = (resp.content || []).filter(function (b) { return b.type === 'text'; })
    .map(function (b) { return b.text; }).join('');
  return extractJson_(text);
}

/**
 * Send records (each with _row) in batches until done, out of time, or stopped.
 * After every batch, onBatch({row: result}) saves the answers. Returns
 * {complete: true|false}. reviewResult(record, reason) builds a placeholder.
 */
function runBatches_(records, systemPrompt, opts) {
  for (var start = 0; start < records.length; start += opts.batchSize) {
    var batch = records.slice(start, start + opts.batchSize);
    if (stopRequested_()) {
      var stopped = {};
      records.slice(start).forEach(function (r) { stopped[r._row] = opts.reviewResult(r, STOPPED_NOTE); });
      opts.onBatch(stopped);
      return {complete: true, stopped: true};
    }
    if (Date.now() > opts.deadline) return {complete: false};

    var byId = {}, payload = batch.map(function (r) {
      var item = {};
      Object.keys(r).forEach(function (k) { if (k !== '_row') item[k] = r[k]; });
      item._id = String(r._row);
      byId[item._id] = r;
      return item;
    });
    var prompt = 'Check these ' + payload.length + ' products.\n' +
      "Every input object has an '_id' field. Echo '_id' back unchanged in every output object — " +
      "that's how results are matched to inputs.\n\n" + JSON.stringify(payload, null, 2);

    var answered = [], reason = null;
    try {
      answered = askJson_(opts.model, systemPrompt, prompt, opts.useSearch);
    } catch (e) {
      if (e instanceof ApiError) {
        if (FATAL_CODES.indexOf(e.code) >= 0) {
          throw new FatalApiError('API error ' + e.code + ' — check your API key and model. ' + e.message);
        }
        reason = e.code === 429 ? 'Rate limited by the API'
          : e.code === 529 ? 'API was overloaded (529)' : 'API error (' + e.code + ')';
      } else {
        reason = 'Unusable model response: ' + String(e.message).slice(0, 80);
      }
    }
    var results = {};
    (Array.isArray(answered) ? answered : []).forEach(function (r) {
      if (!r || typeof r !== 'object') return;
      var id = String(r._id || '');
      delete r._id;
      if (byId[id] && !results[byId[id]._row]) results[byId[id]._row] = r;
    });
    batch.forEach(function (r) {
      if (!results[r._row]) {
        results[r._row] = opts.reviewResult(r, reason || 'Model did not return a result for this product');
      }
    });
    opts.onBatch(results);
  }
  return {complete: true};
}

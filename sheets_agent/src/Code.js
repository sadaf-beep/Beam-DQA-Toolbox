/**
 * DQA Agent for Google Sheets — menu, sidebar, settings and sheet I/O.
 *
 * The agent reads products from the product tab and never edits it. It
 * writes only to its own tabs: "QA Results" (one line per product per check,
 * append-only, the latest line wins) and "QA To-Do" (rebuilt after every
 * check: only products with a problem, highest priority first).
 */

var RULEBOOK_DOC_ID = '16PIEt4Zz0mUPnx85DBTwru-O0676c0HL_Zv44wuxm54';
var RESULTS_TAB = 'QA Results';
var TODO_TAB = 'QA To-Do';
var RESULTS_HEADER = ['Sheet Row', 'Product ID', 'Manufacturer', 'Model', 'Check', 'Status', 'Note',
                      'Detail', 'Job', 'Checked At'];
var CHECK_MODELS = {
  'Claude Haiku 5.5 (cheapest)': 'claude-haiku-5-5',
  'Claude Sonnet 5.5 (more thorough)': 'claude-sonnet-5-5'
};

function onOpen() {
  SpreadsheetApp.getUi().createMenu('DQA Agent')
    .addItem('Open DQA Agent', 'showSidebar')
    .addToUi();
}

function showSidebar() {
  var html = HtmlService.createHtmlOutputFromFile('Sidebar').setTitle('DQA Agent');
  SpreadsheetApp.getUi().showSidebar(html);
}

// ── Settings (private to the signed-in user) ──────────────

function getSettings() {
  var p = PropertiesService.getUserProperties();
  var sheet = productSheet_();
  return {
    hasKey: !!p.getProperty('API_KEY'),
    checkModel: p.getProperty('CHECK_MODEL') || Object.keys(CHECK_MODELS)[0],
    checkModels: Object.keys(CHECK_MODELS),
    sheetName: sheet ? sheet.getName() : null,
    products: sheet ? Math.max(sheet.getLastRow() - 1, 0) : 0
  };
}

function saveSettings(s) {
  var p = PropertiesService.getUserProperties();
  if (s.apiKey) {
    if (s.apiKey.indexOf('sk-ant-') !== 0) throw new Error("Claude API keys start with 'sk-ant-'.");
    p.setProperty('API_KEY', s.apiKey.trim());
  }
  if (s.checkModel && CHECK_MODELS[s.checkModel]) p.setProperty('CHECK_MODEL', s.checkModel);
  return getSettings();
}

function apiKey_() {
  var k = PropertiesService.getUserProperties().getProperty('API_KEY');
  if (!k) throw new Error('No API key set — open Settings in the sidebar first.');
  return k;
}

function checkModel_() {
  var label = PropertiesService.getUserProperties().getProperty('CHECK_MODEL');
  return CHECK_MODELS[label] || CHECK_MODELS[Object.keys(CHECK_MODELS)[0]];
}

// ── Stop flag (set from the sidebar while a step is running) ──

function requestStop() {
  CacheService.getUserCache().put('STOP', '1', 600);
}

function clearStop_() {
  CacheService.getUserCache().remove('STOP');
}

function stopRequested_() {
  return CacheService.getUserCache().get('STOP') === '1';
}

// ── Rulebook (read live from the Google Doc) ──────────────

function rulebook_() {
  var cache = CacheService.getScriptCache();
  var hit = cache.get('RULEBOOK');
  if (hit) return hit;
  var resp = UrlFetchApp.fetch(
    'https://www.googleapis.com/drive/v3/files/' + RULEBOOK_DOC_ID + '/export?mimeType=text/plain',
    {headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()}, muteHttpExceptions: true});
  if (resp.getResponseCode() !== 200) {
    return '(The DQA rulebook Google Doc could not be read — follow the rules you were given.)';
  }
  var text = resp.getContentText().slice(0, 90000);
  cache.put('RULEBOOK', text, 600);
  return text;
}

// ── Reading products ──────────────────────────────────────

function norm_(s) {
  return String(s).trim().toLowerCase().replace(/[\s_]+/g, ' ');
}

function findCol_(headers, names) {
  var lookup = {};
  headers.forEach(function (h, i) { lookup[norm_(h)] = i; });
  for (var i = 0; i < names.length; i++) {
    var hit = lookup[norm_(names[i])];
    if (hit !== undefined) return hit;
  }
  return -1;
}

/** The tab holding products: the active tab if it has an mfr column, else the first one that does. */
function productSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var candidates = [ss.getActiveSheet()].concat(ss.getSheets());
  for (var i = 0; i < candidates.length; i++) {
    var sh = candidates[i];
    if (sh.getName() === RESULTS_TAB || sh.getName() === TODO_TAB || sh.getLastColumn() === 0) continue;
    var headers = sh.getRange(1, 1, 1, sh.getLastColumn()).getDisplayValues()[0];
    if (findCol_(headers, ['mfr', 'manufacturer']) >= 0) return sh;
  }
  return null;
}

/**
 * Products as plain objects: {_row (sheet row number), mfr, model, name, url, image,
 * productId, insights, raw (header -> value)}. Values are as displayed in the sheet.
 */
function readProducts_() {
  var sh = productSheet_();
  if (!sh) throw new Error('No product tab found — it needs an "mfr" column.');
  var values = sh.getDataRange().getDisplayValues();
  var headers = values[0];
  var c = {
    mfr: findCol_(headers, ['mfr', 'manufacturer']),
    model: findCol_(headers, ['model', 'model number']),
    name: findCol_(headers, ['product name', 'name']),
    url: findCol_(headers, ['page url', 'product_url', 'product url']),
    image: findCol_(headers, ['image url', 'image_url']),
    productId: findCol_(headers, ['product id', 'product_id']),
    insights: findCol_(headers, ['beam insights url', 'insights url'])
  };
  var out = [];
  for (var r = 1; r < values.length; r++) {
    var row = values[r];
    if (row.join('').trim() === '') continue;
    var raw = {};
    headers.forEach(function (h, i) { raw[h] = row[i]; });
    var get = function (i) { return i >= 0 ? String(row[i]).trim() : ''; };
    out.push({_row: r + 1, mfr: get(c.mfr), model: get(c.model), name: get(c.name), url: get(c.url),
              image: get(c.image), productId: get(c.productId), insights: get(c.insights), raw: raw});
  }
  return {sheet: sh, headers: headers, products: out};
}

function rawValue_(product, headers, names) {
  var i = findCol_(headers, names);
  return i >= 0 ? String(product.raw[headers[i]]).trim() : null;
}

// ── QA Results tab ────────────────────────────────────────

function resultsSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(RESULTS_TAB);
  if (!sh) {
    sh = ss.insertSheet(RESULTS_TAB);
    sh.getRange(1, 1, 1, RESULTS_HEADER.length).setValues([RESULTS_HEADER])
      .setFontWeight('bold').setBackground('#0D1B2A').setFontColor('#FFFFFF');
    sh.setFrozenRows(1);
  }
  return sh;
}

/** Append results for one check. resultsByRow: {sheetRow: result}; products: {sheetRow: product}. */
function saveResults_(check, resultsByRow, productsByRow, jobId) {
  var rows = Object.keys(resultsByRow);
  if (!rows.length) return;
  var now = new Date();
  var data = rows.map(function (row) {
    var r = resultsByRow[row], p = productsByRow[row] || {};
    var detail = {};
    Object.keys(r).forEach(function (k) { if (k !== 'qa_status' && k !== 'qa_note') detail[k] = r[k]; });
    return [Number(row), p.productId || '', p.mfr || '', p.model || '', check, r.qa_status || '',
            r.qa_note || '', JSON.stringify(detail).slice(0, 45000), jobId || '', now];
  });
  var sh = resultsSheet_();
  sh.getRange(sh.getLastRow() + 1, 1, data.length, RESULTS_HEADER.length).setValues(data);
}

/** {check: {sheetRow: result}} — the latest line per product and check. Optionally only one job. */
function loadResults_(jobId) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(RESULTS_TAB);
  var out = {};
  if (!sh || sh.getLastRow() < 2) return out;
  var values = sh.getRange(2, 1, sh.getLastRow() - 1, RESULTS_HEADER.length).getValues();
  values.forEach(function (v) {
    if (jobId && v[8] !== jobId) return;
    var r = {};
    try { r = JSON.parse(v[7] || '{}'); } catch (e) { r = {}; }
    r.qa_status = v[5];
    r.qa_note = v[6];
    (out[v[4]] = out[v[4]] || {})[v[0]] = r;
  });
  return out;
}

// ── QA To-Do tab ──────────────────────────────────────────

var TODO_HEADER = ['Priority', 'Manufacturer', 'Model', 'Product Name', 'Check', 'Problem', 'Rule',
                   'Suggested Fix', 'Beam Insights URL', 'Product ID', 'Sheet Row'];
var PRIORITY_COLORS = {High: '#FFCDD2', Medium: '#FFE082', Low: '#E3F2FD'};

function buildTodo_(products, results) {
  var items = [];
  products.forEach(function (p) {
    CHECKS.forEach(function (check) {
      var r = (results[check] || {})[p._row];
      todoItems_(check, r).forEach(function (t) {
        items.push({priority: t[0], problem: t[1], rule: t[2], fix: t[3], check: check, product: p});
      });
    });
  });
  var order = {High: 0, Medium: 1, Low: 2};
  items.sort(function (a, b) { return order[a.priority] - order[b.priority]; });
  return items;
}

function writeTodo_() {
  var data = readProducts_();
  var items = buildTodo_(data.products, loadResults_());
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(TODO_TAB) || ss.insertSheet(TODO_TAB);
  sh.clear();
  var rows = [TODO_HEADER].concat(items.map(function (t) {
    var p = t.product;
    return [t.priority, p.mfr, p.model, p.name, CHECK_LABELS[t.check], t.problem, t.rule, t.fix,
            p.insights, p.productId, p._row];
  }));
  sh.getRange(1, 1, rows.length, TODO_HEADER.length).setValues(rows).setWrap(true).setVerticalAlignment('top');
  sh.getRange(1, 1, 1, TODO_HEADER.length).setFontWeight('bold').setBackground('#0D1B2A').setFontColor('#FFFFFF');
  sh.setFrozenRows(1);
  if (items.length) {
    sh.getRange(2, 1, items.length, 1).setBackgrounds(items.map(function (t) { return [PRIORITY_COLORS[t.priority]]; }))
      .setFontWeight('bold');
  }
  [70, 110, 140, 220, 110, 230, 250, 330, 230, 150, 70].forEach(function (w, i) { sh.setColumnWidth(i + 1, w); });
  return items;
}

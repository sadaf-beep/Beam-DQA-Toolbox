/**
 * The QA checks, ported from the Toolbox engines (app/tools/<check>/core.py).
 * Prompts, domain lists and standard warranties come from Reference.js,
 * generated from the Python code, so both versions apply the same rules.
 *
 * Every check returns a result per product with qa_status and qa_note, plus
 * the same detail fields as the Python engine (todoItems_ relies on them).
 */

var CHECKS = ['duplicates', 'oem_url', 'links', 'name_qa', 'status', 'firmware', 'warranty', 'dimensions'];
var CHECK_LABELS = {
  duplicates: 'Duplicates', oem_url: 'OEM URL', links: 'Links', name_qa: 'Product Name',
  status: 'Status', firmware: 'Firmware & Docs', warranty: 'Warranty', dimensions: 'Dimensions'
};
var AI_CHECKS = {name_qa: false, status: true, firmware: true, warranty: true, dimensions: true}; // value = uses web search
var LIFETIME_DAYS = '36500';
var BLANK_WARRANTY = 'Flag — blank warranty';

// ── Columns ───────────────────────────────────────────────

function statusCol_(h) { return findCol_(h, ['product_status', 'status']); }
function warrantyCol_(h) { return findCol_(h, ['warranty_length', 'warranty length', 'warranty']); }
function flagCols_(h) {
  var out = {};
  Object.keys(REF.firmwareFlags).forEach(function (f) {
    var i = findCol_(h, REF.firmwareFlags[f]);
    if (i >= 0) out[f] = h[i];
  });
  return out;
}

function missingColumns_(check, headers) {
  var miss = [];
  var need = function (label, names) { if (findCol_(headers, names) < 0) miss.push(label); };
  if (check !== 'links' && check !== 'oem_url') need('mfr', ['mfr', 'manufacturer']);
  if (['duplicates', 'name_qa', 'firmware', 'warranty', 'dimensions'].indexOf(check) >= 0) need('model', ['model', 'model number']);
  if (check === 'oem_url') need('Page URL', ['page url', 'product_url', 'product url']);
  if (check === 'links' && findCol_(headers, ['page url', 'product_url', 'product url']) < 0 &&
      findCol_(headers, ['image url', 'image_url']) < 0) miss.push('Image URL or Page URL');
  if (check === 'status' && statusCol_(headers) < 0) miss.push('product_status');
  if (check === 'warranty' && warrantyCol_(headers) < 0) miss.push('warranty_length');
  if (check === 'firmware' && !Object.keys(flagCols_(headers)).length) miss.push('has_firmware / Firmware Added / documents added');
  if (check === 'dimensions' && findCol_(headers, REF.productDimNames) < 0 && findCol_(headers, REF.packageDimNames) < 0) {
    miss.push('product_dimension or package_dimensions');
  }
  return miss;
}

// ── Duplicates ────────────────────────────────────────────

function alnum_(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }

/** difflib.SequenceMatcher(None, a, b).ratio() for short strings (no junk heuristic needed). */
function seqRatio_(a, b) {
  if (!a.length && !b.length) return 1;
  function matches(a0, a1, b0, b1) {
    var best = 0, bi = a0, bj = b0;
    for (var i = a0; i < a1; i++) {
      for (var j = b0; j < b1; j++) {
        var k = 0;
        while (i + k < a1 && j + k < b1 && a[i + k] === b[j + k]) k++;
        if (k > best) { best = k; bi = i; bj = j; }
      }
    }
    if (!best) return 0;
    return best + matches(a0, bi, b0, bj) + matches(bi + best, a1, bj + best, b1);
  }
  return 2 * matches(0, a.length, 0, b.length) / (a.length + b.length);
}

function duplicates_(all) {
  var THRESHOLD = 0.85, LIKELY = 0.92, NEAR_MAX_ROWS = 2000;
  var rows = all.filter(function (p) { return alnum_(p.model); });
  var byModel = {};
  rows.forEach(function (p) { (byModel[alnum_(p.model)] = byModel[alnum_(p.model)] || []).push(p); });
  var pairs = [];
  Object.keys(byModel).forEach(function (k) {
    var m = byModel[k];
    for (var i = 0; i < m.length; i++) for (var j = i + 1; j < m.length; j++) {
      var a = m[i], b = m[j];
      if (alnum_(a.mfr) === alnum_(b.mfr)) {
        if (a.model.trim().toLowerCase() !== b.model.trim().toLowerCase()) {
          pairs.push({a: a, b: b, type: 'Model Variant (Review)', conf: 'Review', score: 1,
                      note: 'Same model apart from formatting (' + a.model + ' vs ' + b.model + '). May be a model alias rather than a duplicate — check case by case.'});
        } else {
          pairs.push({a: a, b: b, type: 'Exact Duplicate', conf: 'Definite', score: 1, note: 'Identical manufacturer and model number.'});
        }
      } else {
        pairs.push({a: a, b: b, type: 'Cross-Brand', conf: 'Review', score: 1,
                    note: 'Identical model number under different manufacturers.'});
      }
    }
  });
  var keys = Object.keys(byModel);
  if (rows.length <= NEAR_MAX_ROWS) {
    for (var i = 0; i < keys.length; i++) for (var j = i + 1; j < keys.length; j++) {
      var ka = keys[i], kb = keys[j];
      var bound = 2 * Math.min(ka.length, kb.length) / (ka.length + kb.length);
      if (bound * 0.75 + 0.25 < THRESHOLD) continue;
      var ms = seqRatio_(ka, kb);
      if (ms * 0.75 + 0.25 < THRESHOLD) continue;
      byModel[ka].forEach(function (a) {
        byModel[kb].forEach(function (b) {
          var score = ms * 0.75 + seqRatio_(alnum_(a.mfr), alnum_(b.mfr)) * 0.25;
          if (score >= THRESHOLD) {
            pairs.push({a: a, b: b, type: 'Near Match', conf: score >= LIKELY ? 'Likely' : 'Possible',
                        score: Math.round(score * 1000) / 1000, note: 'Similar model number.'});
          }
        });
      });
    }
  }
  var severity = ['Exact Duplicate', 'Cross-Brand', 'Model Variant (Review)', 'Near Match'];
  var res = {};
  all.forEach(function (p) { res[p._row] = {qa_status: 'Pass', qa_note: '', pairs: []}; });
  pairs.forEach(function (pr) {
    [[pr.a, pr.b], [pr.b, pr.a]].forEach(function (x) {
      var r = res[x[0]._row];
      r.pairs.push({type: pr.type, other_row: x[1]._row});
      if (r.qa_status === 'Pass' || severity.indexOf(pr.type) < severity.indexOf(r.qa_status)) r.qa_status = pr.type;
      r.qa_note += (r.qa_note ? '; ' : '') + pr.type + ' (' + pr.conf + ', ' + Math.round(pr.score * 100) +
        '%) with sheet row ' + x[1]._row;
    });
  });
  return res;
}

// ── OEM URL ───────────────────────────────────────────────

var BLANK_URLS = ['', 'nan', 'none', '#n/a', 'n/a'];

function urlHost_(url) {
  var m = String(url || '').trim().match(/^(?:[a-z]+:\/\/)?([^\/?#:]+)/i);
  return m ? m[1].toLowerCase().replace(/\.$/, '').replace(/^www\./, '') : '';
}

function registrableDomain_(host) {
  var parts = host.split('.');
  if (parts.length >= 3 && REF.twoLevelSuffixes.indexOf(parts.slice(-2).join('.')) >= 0) return parts.slice(-3).join('.');
  return parts.length >= 2 ? parts.slice(-2).join('.') : host;
}

function hostMatches_(host, domains) {
  for (var i = 0; i < domains.length; i++) {
    var d = domains[i];
    if (host === d || host.slice(-(d.length + 1)) === '.' + d) return d;
  }
  return null;
}

function refKey_(name) { return alnum_(String(name || '').replace(/\(.*?\)/g, '')); }

function oemLookup_(mfr) {
  var k = refKey_(mfr);
  if (k.length < 2) return null;
  var rows = REF.oemStandard, i;
  for (i = 0; i < rows.length; i++) if (refKey_(rows[i].manufacturer) === k) return rows[i];
  for (i = 0; i < rows.length; i++) {
    var aliases = (rows[i].manufacturer.match(/\((.*?)\)/g) || []).map(function (a) { return refKey_(a.slice(1, -1)); });
    if (aliases.indexOf(k) >= 0) return rows[i];
  }
  var best = null;
  rows.forEach(function (r) {
    var rk = refKey_(r.manufacturer);
    if (rk.length >= 4 && (rk.indexOf(k) >= 0 || k.indexOf(rk) >= 0) && (!best || rk.length > refKey_(best.manufacturer).length)) best = r;
  });
  return best;
}

function mfrDomains_(mfr) {
  var name = String(mfr || '').toLowerCase().replace(/[^a-z0-9 &\/-]/g, '').trim();
  var found = [];
  if (!name) return found;
  var compact = alnum_(name);
  Object.keys(REF.mfrDomains).forEach(function (key) {
    var re = new RegExp('(^|[^a-z0-9])' + key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '($|[^a-z0-9])');
    if (re.test(name) || alnum_(key) === compact) found = found.concat(REF.mfrDomains[key]);
  });
  var ref = oemLookup_(mfr);
  if (ref && ref.domain) found.push(registrableDomain_(ref.domain));
  return found;
}

var ALL_OEM_DOMAINS_ = null;
function allOemDomains_() {
  if (!ALL_OEM_DOMAINS_) {
    ALL_OEM_DOMAINS_ = [];
    Object.keys(REF.mfrDomains).forEach(function (k) { ALL_OEM_DOMAINS_ = ALL_OEM_DOMAINS_.concat(REF.mfrDomains[k]); });
    REF.oemStandard.forEach(function (r) { if (r.domain) ALL_OEM_DOMAINS_.push(registrableDomain_(r.domain)); });
  }
  return ALL_OEM_DOMAINS_;
}

function looksLikeMfrSite_(domain, mfr) {
  var label = alnum_(domain.split('.')[0]);
  var mfrL = String(mfr || '').toLowerCase(), key = alnum_(mfrL);
  if (label.length < 2 || !key) return false;
  if (key.indexOf(label) >= 0 || label.indexOf(key) >= 0) return true;
  return mfrL.split(/[^a-z0-9]+/).some(function (w) {
    return w.length >= 3 && REF.genericWords.indexOf(w) < 0 && label.indexOf(w) >= 0;
  });
}

function oemRecord_(mfr, url, flag) {
  function res(s, d, t, n) { return {qa_status: s, domain: d, domain_type: t, qa_note: n}; }
  url = String(url || '').trim();
  if (BLANK_URLS.indexOf(url.toLowerCase()) >= 0) return res('Error', '', 'No URL', 'Page URL is blank or missing');
  var host = urlHost_(url);
  if (!host) return res('Error', '', 'Malformed URL', 'Cannot parse domain from: ' + url.slice(0, 60));
  var domain = registrableDomain_(host);
  var noFlag = ' (no is_oem_url column to compare against)';
  var retailer = hostMatches_(host, REF.knownRetailers);
  if (retailer) {
    if (flag === null) return res('Review', retailer, 'Retailer', 'URL is a retailer (' + retailer + ')' + noFlag);
    if (flag) return res('Fail', retailer, 'Retailer', 'URL is a retailer (' + retailer + ') but is_oem_url is marked TRUE');
    return res('Pass', retailer, 'Retailer', 'Correctly identified as retailer (' + retailer + ')');
  }
  var own = hostMatches_(host, mfrDomains_(mfr));
  if (own) {
    if (flag === false) return res('Fail', own, 'OEM', 'URL is manufacturer domain (' + own + ') but is_oem_url is marked FALSE');
    return res('Pass', own, 'OEM', 'Correctly identified as OEM (' + own + ')');
  }
  var other = hostMatches_(host, allOemDomains_());
  if (other) {
    if (flag) return res('Fail', other, 'OEM (different mfr)', "URL is another manufacturer's site (" + other + '), not ' + mfr + "'s, but is_oem_url is TRUE");
    return res('Review', other, 'OEM (different mfr)', "Page is on another manufacturer's site (" + other +
               ") — is_oem_url FALSE is right, but check the recorded manufacturer '" + mfr + "' is correct");
  }
  if (looksLikeMfrSite_(domain, mfr)) {
    if (flag === false) return res('Fail', domain, 'OEM (by name)', 'Domain ' + domain + ' looks like ' + mfr + "'s own site but is_oem_url is marked FALSE");
    return res('Pass', domain, 'OEM (by name)', 'Domain ' + domain + ' looks like ' + mfr + "'s own site");
  }
  if (flag) return res('Review', domain, 'Third party', 'is_oem_url is TRUE but ' + domain + " doesn't look like " + mfr + "'s site — verify");
  return res('Pass', domain, 'Third party', 'Third-party page (' + domain + ") — fine when the manufacturer doesn't list the product");
}

function oemUrl_(products, headers) {
  var flagI = findCol_(headers, ['is_oem_url', 'is oem url']);
  var res = {};
  products.forEach(function (p) {
    var flag = flagI < 0 ? null : ['TRUE', '1', 'YES'].indexOf(String(p.raw[headers[flagI]]).trim().toUpperCase()) >= 0;
    res[p._row] = oemRecord_(p.mfr, p.url, flag);
  });
  return res;
}

// ── Links ─────────────────────────────────────────────────

function classifyCode_(code) {
  if (code === 403) return 'OK (403 - blocked bot)';
  if (code >= 200 && code < 400) return 'OK (' + code + ')';
  if (code === 404 || code === 410) return 'BROKEN (' + code + ')';
  return 'REVIEW (' + code + ')';
}

function fetchStatuses_(urls) {
  var reqs = urls.map(function (u) {
    if (!/^https?:\/\//i.test(u)) u = 'https://' + u;
    return {url: u, method: 'get', muteHttpExceptions: true, followRedirects: true,
            validateHttpsCertificates: false, headers: {'User-Agent': 'Mozilla/5.0 (compatible; LinkChecker/2.0)'}};
  });
  try {
    return UrlFetchApp.fetchAll(reqs).map(function (r) { return classifyCode_(r.getResponseCode()); });
  } catch (e) {
    // One unreachable host fails the whole fetchAll — check them one by one instead.
    return reqs.map(function (r) {
      try { return classifyCode_(UrlFetchApp.fetch(r.url, r).getResponseCode()); }
      catch (err) { return 'BROKEN - Cannot connect'; }
    });
  }
}

function linkStatus_(img, page) {
  var parts = [], s = 'Pass';
  [['Image', img], ['Product URL', page]].forEach(function (x) {
    if (/BROKEN|REVIEW/.test(x[1])) parts.push(x[0] + ': ' + x[1]);
  });
  if (/BROKEN/.test(img + page)) s = 'Broken';
  else if (/REVIEW/.test(img + page)) s = 'Review';
  else if (img.replace(/-/g, '') === '' && page.replace(/-/g, '') === '') { s = 'Skip'; parts = ['No image or product URL']; }
  return {image_status: img, product_status: page, qa_status: s, qa_note: parts.join(' | ')};
}

/** Checks links in chunks until done or out of time; saves as it goes. */
function links_(products, ctx) {
  var CHUNK = 15;
  for (var i = 0; i < products.length; i += CHUNK) {
    if (Date.now() > ctx.deadline) return false;
    if (stopRequested_()) return true;
    var chunk = products.slice(i, i + CHUNK), urls = [], where = [];
    chunk.forEach(function (p, k) {
      [['img', p.image], ['page', p.url]].forEach(function (x) {
        var u = String(x[1] || '').trim();
        if (u && u.indexOf('data:') !== 0) { urls.push(u); where.push([k, x[0]]); }
      });
    });
    var got = urls.length ? fetchStatuses_(urls) : [];
    var labels = chunk.map(function (p) {
      return {img: !p.image ? '--' : p.image.indexOf('data:') === 0 ? 'OK (embedded image)' : '',
              page: !p.url ? '--' : ''};
    });
    where.forEach(function (w, n) { labels[w[0]][w[1]] = got[n]; });
    var res = {};
    chunk.forEach(function (p, k) { res[p._row] = linkStatus_(labels[k].img, labels[k].page); });
    ctx.save(res);
  }
  return true;
}

// ── Claude checks: records and results ────────────────────

function review_(check, why) {
  var r = {qa_status: NEEDS_REVIEW, qa_note: why};
  if (check === 'status') { r['Verified Status'] = 'could not check'; r.Match = 'Needs Review'; r.Reason = why; }
  return r;
}

function standardHint_(mfr, std) {
  if (std && std.days) return 'Suggested: ' + mfr + "'s standard warranty, " + std.standard + ' (' + std.days + ' days).';
  if (std && std.standard) return 'Team reference for ' + mfr + ': ' + std.standard + '.';
  return 'Suggested: add ' + (mfr || 'the manufacturer') + "'s standard warranty.";
}

/** {local: {row: result} decided without Claude, records: [...] to send}. */
function aiRecords_(check, products, headers) {
  var local = {}, records = [];
  var sCol = statusCol_(headers), wCol = warrantyCol_(headers), flags = flagCols_(headers);
  var pdI = findCol_(headers, REF.productDimNames), pkI = findCol_(headers, REF.packageDimNames);
  products.forEach(function (p) {
    var rec = {mfr: p.mfr, model: p.model};
    if (check === 'name_qa') {
      if (!p.mfr && !p.model) { local[p._row] = {qa_status: 'Fail', qa_note: 'Manufacturer and model are both blank.'}; return; }
      rec.product_name = p.name;
    } else if (check === 'status') {
      var db = sCol >= 0 ? String(p.raw[headers[sCol]]).trim() : '';
      if (!db) { local[p._row] = {qa_status: 'Skip', qa_note: 'No product_status recorded'}; return; }
      if (!p.model && !p.name) { local[p._row] = review_(check, 'No model number or product name given — nothing to search for'); return; }
      rec['product name'] = p.name; rec.product_status = db;
      if (p.url) rec.product_url = p.url;
    } else if (check === 'firmware') {
      if (!p.model && !p.name) { local[p._row] = {qa_status: 'Skip', qa_note: 'No model number or product name — nothing to search for.'}; return; }
      rec.product_name = p.name;
      Object.keys(flags).forEach(function (f) { rec[f] = String(p.raw[flags[f]]).trim(); });
      if (p.url) rec.product_url = p.url;
    } else if (check === 'warranty') {
      var days = wCol >= 0 ? String(p.raw[headers[wCol]]).trim().replace(/,/g, '') : '';
      if (days === '-1' || days === LIFETIME_DAYS) days = LIFETIME_DAYS;
      var std = oemLookup_(p.mfr);
      if (days === '' || Number(days) === 0) { local[p._row] = {qa_status: BLANK_WARRANTY, qa_note: 'Warranty is blank. ' + standardHint_(p.mfr, std)}; return; }
      if (!(Number(days) > 0)) { local[p._row] = {qa_status: 'Error', qa_note: 'warranty_length is not a number (' + days + ').'}; return; }
      if (!p.model && !p.name) { local[p._row] = {qa_status: 'Skip', qa_note: 'No model number or product name — nothing to search for.'}; return; }
      rec.product_name = p.name; rec.warranty_length = days;
      if (std && std.days) rec.mfr_standard_warranty = std.standard + ' (' + std.days + ' days)';
      if (p.url) rec.product_url = p.url;
    } else if (check === 'dimensions') {
      if (!p.mfr && !p.model) { local[p._row] = review_(check, 'Manufacturer and model are both blank — nothing to search for.'); return; }
      rec.product_name = p.name;
      rec.product_dimension = pdI >= 0 ? String(p.raw[headers[pdI]]).trim() : '';
      rec.package_dimensions = pkI >= 0 ? String(p.raw[headers[pkI]]).trim() : '';
      if (p.url) rec.product_url = p.url;
    }
    rec._row = p._row;
    records.push(rec);
  });
  return {local: local, records: records};
}

/** Claude's answer → result with qa_status/qa_note, like each Python engine's _normalise. */
function normalise_(check, r, rec) {
  if (check === 'name_qa') {
    var note = String(r.general_feedback || '').trim();
    if (r.issues) note += ' Issues: ' + r.issues;
    if (r.suggested_corrections) note += ' Suggested: ' + r.suggested_corrections;
    r.qa_status = String(r.overall_status || '').trim() || NEEDS_REVIEW; r.qa_note = note.trim();
  } else if (check === 'status') {
    r['DB Status'] = rec ? rec.product_status : r['DB Status'];
    var v = String(r['Verified Status'] || '').trim().toLowerCase(), m = String(r.Match || '').trim();
    if (m.toLowerCase() === 'mismatch' && !String(r.Reason || '').trim()) {
      r.Reason = "Flagged as a mismatch but the model didn't provide an explanation — verify manually.";
    }
    r.qa_status = v === 'could not check' || m === 'Needs Review' ? NEEDS_REVIEW
      : m.toLowerCase() === 'mismatch' ? 'Mismatch' : v === 'unknown' ? 'Unknown' : 'Confirmed';
    r.qa_note = String(r.Reason || '').trim();
  } else if (check === 'firmware') {
    r.qa_status = String(r.overall_status || '').trim() || NEEDS_REVIEW; r.qa_note = String(r.note || '').trim();
  } else {
    r.qa_status = String(r.status || '').trim() || NEEDS_REVIEW; r.qa_note = String(r.note || '').trim();
  }
  return r;
}

// ── Running a check ───────────────────────────────────────

/**
 * Run one check on `products` (a selection) as job ctx.jobId, resuming
 * where an earlier call for the same job stopped. Saves to QA Results as it
 * goes. Returns {complete, results} — results only when complete.
 */
function runCheck_(check, products, all, headers, ctx) {
  var done = (loadResults_(ctx.jobId)[check]) || {};
  var byRow = {};
  all.forEach(function (p) { byRow[p._row] = p; });
  var save = function (res) { saveResults_(check, res, byRow, ctx.jobId); Object.keys(res).forEach(function (k) { done[k] = res[k]; }); };
  var todo = products.filter(function (p) { return !done[p._row]; });

  if (todo.length) {
    if (check === 'duplicates') {
      var dup = duplicates_(all), sel = {};
      todo.forEach(function (p) { sel[p._row] = dup[p._row]; });
      save(sel);
    } else if (check === 'oem_url') {
      save(oemUrl_(todo, headers));
    } else if (check === 'links') {
      if (!links_(todo, {deadline: ctx.deadline, save: save})) return {complete: false, progress: Object.keys(done).length};
    } else {
      var prep = aiRecords_(check, todo, headers);
      if (Object.keys(prep.local).length) save(prep.local);
      var recByRow = {};
      prep.records.forEach(function (r) { recByRow[r._row] = r; });
      var out = runBatches_(prep.records, REF.prompts[check], {
        model: ctx.model, useSearch: AI_CHECKS[check], batchSize: ctx.batchSize, deadline: ctx.deadline,
        reviewResult: function (rec, why) { return review_(check, why); },
        onBatch: function (res) {
          Object.keys(res).forEach(function (row) {
            if (res[row].qa_status !== NEEDS_REVIEW) res[row] = normalise_(check, res[row], recByRow[row]);
          });
          save(res);
        }
      });
      if (!out.complete) return {complete: false, progress: Object.keys(done).length};
    }
  }
  var results = {};
  products.forEach(function (p) { if (done[p._row]) results[p._row] = done[p._row]; });
  return {complete: true, results: results};
}

// ── To-do items (port of app/tools/qa_pipeline/todo.py) ───

function todoItems_(check, r) {
  if (!r) return [];
  var s = String(r.qa_status || '').trim().toLowerCase(), note = String(r.qa_note || '').trim();
  if (s === NEEDS_REVIEW.toLowerCase()) {
    return [['Low', "Couldn't be checked automatically", 'Every product is checked', 'Check by hand, or re-run the check for this product']];
  }
  var out = [];
  if (check === 'duplicates') {
    var types = (r.pairs || []).map(function (p) { return p.type; });
    if (types.indexOf('Exact Duplicate') >= 0) out.push(['High', 'Duplicate product', 'No duplicate products in Insights', 'Tell the team lead which record to keep. ' + note]);
    if (types.indexOf('Cross-Brand') >= 0) out.push(['High', 'Same model listed under different manufacturers', 'Manufacturer and model must belong together', 'Check which manufacturer is correct. ' + note]);
    if (types.indexOf('Model Variant (Review)') >= 0 && !out.length) out.push(['Medium', 'Same model written differently (hyphens/spaces)', 'Formatting differences: check case by case, often a model alias', 'Check whether this is a duplicate or a model alias. ' + note]);
    if (types.indexOf('Near Match') >= 0 && !out.length) out.push(['Low', 'Very similar model to another product', 'No duplicate products in Insights', 'Check whether these are the same product. ' + note]);
  } else if (check === 'oem_url') {
    if (s === 'error') out.push(['High', 'No product page (Page URL)', 'Every product needs a product page', note]);
    else if (s === 'fail') out.push(['Medium', 'is_oem_url is wrong', "is_oem_url is TRUE only on the manufacturer's own site", note]);
    else if (s === 'review') out.push(['Low', 'Product page needs a look', "is_oem_url is TRUE only on the manufacturer's own site", note]);
  } else if (check === 'links') {
    var page = String(r.product_status || ''), image = String(r.image_status || '');
    if (/BROKEN/.test(page)) out.push(['High', 'Product page link is broken (' + page + ')', 'Product page must work', 'Find the current product page and update Page URL']);
    if (/BROKEN/.test(image)) out.push(['Medium', 'Image link is broken (' + image + ')', 'Every product needs a working image', 'Copy a new image address from the product page (or use the manufacturer logo)']);
    if (!out.length && s === 'review') out.push(['Low', 'Link gave an unusual response', 'Product page and image must work', 'Open the link and check it. ' + note]);
  } else if (check === 'name_qa') {
    var fix = String(r.suggested_corrections || '').trim() || note;
    if (s === 'fail') out.push(['High', 'Manufacturer, model or name looks wrong', "Manufacturer, model and name must match the manufacturer's listing", fix]);
    else if (s === 'warning') out.push(['Medium', 'Product name or model may be slightly off', "Product name must match the manufacturer's listing", fix]);
  } else if (check === 'status') {
    if (s === 'mismatch') {
      var nw = String(r['Suggested Status'] || '').trim() || String(r['Verified Status'] || '').trim();
      out.push(['High', 'Status is wrong (recorded: ' + (r['DB Status'] || '') + ')',
                'Supported while it still has firmware/software support; discontinued only when support has ended',
                ('Change status to ' + nw + '. ' + (r.Reason || '')).trim()]);
    } else if (s === 'unknown') {
      out.push(['Low', "Status couldn't be confirmed online", 'Status must be supported or discontinued', "Check the manufacturer's site and support page by hand"]);
    }
  } else if (check === 'firmware') {
    if (String(r.flag_has_firmware || '').toLowerCase() === 'flag') out.push(['Medium', 'has_firmware is wrong', 'has_firmware is TRUE when the manufacturer offers firmware', ('Correct has_firmware. ' + (r.firmware_source || '')).trim()]);
    var fa = String(r.flag_firmware_added || '').toLowerCase();
    if (fa.indexOf('contradiction') >= 0) out.push(['Medium', 'Firmware Added is TRUE but has_firmware is FALSE', 'Firmware can only be added to a product that has firmware', 'Fix whichever flag is wrong']);
    else if (fa === 'flag') out.push(['Medium', "Firmware exists but hasn't been added", 'Firmware Added is TRUE once the team adds it', 'Add the firmware to Beam']);
    if (String(r.flag_documents || '').toLowerCase() === 'flag') out.push(['Medium', "Documents exist but haven't been added", 'Official documents should be uploaded', ('Upload the documents. ' + (r.documents_source || '')).trim()]);
    if (!out.length && (s === 'warning' || s === 'unknown')) out.push(['Low', "Firmware/documents couldn't be confirmed", 'has_firmware must be correct', ("Check the manufacturer's downloads page by hand. " + note).trim()]);
  } else if (check === 'warranty') {
    var rule = "Product's own warranty, or the manufacturer's standard; never blank";
    if (s.indexOf('flag') === 0) out.push(['Medium', 'Warranty is blank', rule, note.replace('Warranty is blank. ', '')]);
    else if (s === 'fail') {
      var vd = String(r.verified_warranty_days || '').trim();
      out.push(['Medium', 'Warranty is wrong', rule, ((vd ? 'Change to ' + vd + ' days (' + (r.verified_warranty_description || '') + '). ' : '') + note).trim()]);
    } else if (s === 'error') out.push(['Medium', "Warranty value isn't a number", rule, note]);
    else if (s === 'warning' || s === 'unknown') out.push(['Low', "Warranty couldn't be confirmed", rule, note]);
  } else if (check === 'dimensions') {
    if (s.indexOf('fail') === 0 || s.indexOf('flag') === 0) out.push(['Medium', 'Dimensions: ' + r.qa_status, "Dimensions in the manufacturer's unit and format (W x H x D for electronics)", note]);
    else if (s === 'warning' || s === 'unknown') out.push(['Low', "Dimensions couldn't be confirmed", "Dimensions must match the manufacturer's specs", note]);
  }
  return out;
}

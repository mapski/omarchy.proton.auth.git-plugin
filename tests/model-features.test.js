// Pure UI state and reply-boundary regressions, without Qt or vault access.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const src = fs.readFileSync(path.join(__dirname, '..', 'Model.js'), 'utf8').replace('.pragma library', '');
const names = ['parseList', 'normalizePrefs', 'filterAndOrder', 'findIndexById', 'selectionAfterRefresh',
  'liveRemaining', 'refreshDelayMs', 'hiddenEntries', 'updatePrefs', 'issuerGlyph', 'errorHint'];
const M = new Function(src + '\nreturn {' + names.map(n => `${n}: typeof ${n} === 'function' ? ${n} : undefined`).join(',') + '};')();
const entry = (id, issuer = id, account = '') => ({ id, issuer, account, kind: 'totp', code: '123456', nextCode: '654321', digits: 6, period: 30, remaining: 10, order: 0 });
const reply = (entries, extra = {}) => JSON.stringify({ ok: true, entries, error: null, now: 1000, ...extra });

test('reply parser rejects malformed JSON without quoting its sensitive input', () => {
  const result = M.parseList('{"secret":"never-echo-this"');
  assert.equal(result.ok, false);
  assert.equal(JSON.stringify(result).includes('never-echo-this'), false);
  assert.equal(result.error.code, 'INVALID_REPLY');
});

test('reply parser enforces row schema and entry count before accepting any codes', () => {
  const badRows = [null, [], { ...entry('valid'), id: '-flag' }, { ...entry('valid'), id: '../x' },
    { ...entry('valid'), issuer: 'x'.repeat(257) }, { ...entry('valid'), code: 'secret' },
    { ...entry('valid'), nextCode: '12345' }, { ...entry('valid'), period: 0 },
    { ...entry('valid'), digits: 6.5 }, { ...entry('valid'), remaining: 31 },
    { ...entry('valid'), code: null }, { ...entry('valid'), account: {} }];
  for (const row of badRows) {
    const result = M.parseList(reply([entry('good'), row]));
    assert.equal(result.ok, false, JSON.stringify(row));
    assert.deepEqual(result.entries, []);
  }
  assert.equal(M.parseList(reply(Array.from({ length: 1001 }, (_, i) => entry('e' + i)))).ok, false);
  assert.equal(M.parseList(reply([entry('same'), entry('same')])).ok, false);
  assert.equal(M.parseList(' '.repeat(1024 * 1024 + 1)).error.code, 'OUTPUT_TOO_LARGE');
  assert.equal(M.parseList(reply([], { ok: 'true' })).ok, false);
});

test('reply parser strips unknown fields and supports hidden, Steam and unsupported rows', () => {
  const hidden = { ...entry('hidden'), code: null, nextCode: null, remaining: null, period: null, digits: null, kind: null };
  const steam = { ...entry('steam'), code: 'BCDF2', nextCode: 'GHJK3', digits: 5, kind: 'steam' };
  const unsupported = { ...hidden, id: 'unsupported', kind: 'unsupported', error: { code: 'UNSUPPORTED_ENTRY', message: 'Unsupported type' } };
  const result = M.parseList(reply([{ ...entry('shown'), secret: 'seed', uri: 'otpauth://seed' }, hidden, steam, unsupported], {
    secret: 'seed', prefs: { privacy: false, pinned: ['shown'], recent: ['steam'], secret: 'seed' },
    vaultMtime: 123, copy: { usedNext: true, code: 'secret' }, demo: true
  }));
  assert.equal(result.ok, true);
  assert.deepEqual(result.entries[0], entry('shown'));
  assert.equal(result.entries[1].code, null);
  assert.equal(result.entries[2].code, 'BCDF2');
  assert.equal(result.entries[3].kind, 'unsupported');
  assert.equal(JSON.stringify(result).includes('seed'), false);
  assert.deepEqual(result.copy, { usedNext: true });
  assert.deepEqual(result.prefs, { privacy: false, pinned: ['shown'], recent: ['steam'] });
  assert.equal(result.now, 1000);
  assert.equal(result.vaultMtime, 123);
});

test('preferences default to privacy and discard invalid, duplicate or excessive IDs', () => {
  assert.equal(typeof M.normalizePrefs, 'function');
  assert.deepEqual(M.normalizePrefs(null), { privacy: true, pinned: [], recent: [] });
  assert.deepEqual(M.normalizePrefs({ privacy: 'false', pinned: ['a', 'a', null, {}, '', '-option', '../a', 'has space', 'a:b_1.2'], recent: ['b', 'a', 'b'] }),
    { privacy: true, pinned: ['a', 'a:b_1.2'], recent: ['b', 'a'] });
  const large = Array.from({ length: 110 }, (_, i) => 'id-' + i);
  assert.equal(M.normalizePrefs({ pinned: large, recent: large }).pinned.length, 100);
  assert.deepEqual(M.normalizePrefs({ recent: large }).recent, ['id-0', 'id-1', 'id-2', 'id-3', 'id-4']);
});

test('preference operations toggle pins, remember most recent IDs and never mutate input', () => {
  assert.equal(typeof M.updatePrefs, 'function');
  const original = { privacy: true, pinned: ['a'], recent: ['a', 'b', 'c', 'd', 'e'] };
  assert.deepEqual(M.updatePrefs(original, 'pin', 'a'), { ...original, pinned: [] });
  assert.deepEqual(M.updatePrefs(original, 'pin', 'b').pinned, ['a', 'b']);
  assert.deepEqual(M.updatePrefs(original, 'recent', 'd').recent, ['d', 'a', 'b', 'c', 'e']);
  assert.deepEqual(M.updatePrefs(original, 'recent', 'f').recent, ['f', 'a', 'b', 'c', 'd']);
  assert.equal(M.updatePrefs(original, 'privacy', false).privacy, false);
  assert.equal(M.updatePrefs(original, 'privacy', 'false').privacy, true);
  assert.deepEqual(M.updatePrefs(original, 'pin', '-flag'), original);
  assert.deepEqual(original.pinned, ['a']);
});

test('empty-query order is pinned, recent, then issuer/account and stable ID', () => {
  assert.equal(typeof M.filterAndOrder, 'function');
  const rows = [entry('z', 'Google'), entry('b', 'GitHub'), entry('a', 'GitHub'), entry('recent', 'Proton'), entry('pinned', 'Zebra')];
  assert.deepEqual(M.filterAndOrder(rows, '', { pinned: ['pinned'], recent: ['recent'] }).map(x => x.id), ['pinned', 'recent', 'a', 'b', 'z']);
  assert.deepEqual(M.filterAndOrder(rows.slice().reverse(), '', { pinned: ['pinned'], recent: ['z'] }, ['recent']).map(x => x.id), ['pinned', 'recent', 'a', 'b', 'z']);
  assert.deepEqual(rows.map(x => x.id), ['z', 'b', 'a', 'recent', 'pinned']);
});

test('fuzzy search ranks issuer matches and finds gh in GitHub and tokens across labels', () => {
  assert.equal(typeof M.filterAndOrder, 'function');
  const rows = [entry('account', 'Other', 'github@example.net'), entry('github', 'GitHub', 'alice@example.net'), entry('gap', 'Good health'), entry('no', 'Proton')];
  assert.deepEqual(M.filterAndOrder(rows, 'gh', { pinned: ['gap'] }).map(x => x.id), ['github', 'gap', 'account']);
  assert.deepEqual(M.filterAndOrder(rows, 'git ALICE', {}).map(x => x.id), ['github']);
  assert.deepEqual(M.filterAndOrder(rows, 'xyz', {}), []);
});

test('selection follows stable IDs across refresh, removal and empty results', () => {
  assert.equal(typeof M.findIndexById, 'function');
  assert.equal(typeof M.selectionAfterRefresh, 'function');
  const rows = [entry('b'), entry('a')];
  assert.equal(M.findIndexById(rows, 'a'), 1);
  assert.equal(M.findIndexById(rows, 'absent'), -1);
  assert.equal(M.selectionAfterRefresh(rows, 'a'), 'a');
  assert.equal(M.selectionAfterRefresh(rows, 'gone'), 'b');
  assert.equal(M.selectionAfterRefresh([], 'a'), '');
  assert.equal(M.selectionAfterRefresh([null, entry('a')], 'gone'), 'a');
});

test('countdown uses actual elapsed time, clamps to zero and never extends on clock reversal', () => {
  assert.equal(typeof M.liveRemaining, 'function');
  assert.equal(M.liveRemaining(entry('a'), 1000, 1003.25), 6.75);
  assert.equal(M.liveRemaining(entry('a'), 1000, 2000), 0);
  assert.equal(M.liveRemaining(entry('a'), 1000, 990), 10);
  assert.equal(M.liveRemaining({ remaining: null, period: null, code: null }, 1000, 1001), 0);
  assert.equal(M.liveRemaining(entry('a'), NaN, 1001), 0);
});

test('refresh targets the earliest shown expiry plus 100ms and ignores hidden rows', () => {
  assert.equal(typeof M.refreshDelayMs, 'function');
  assert.equal(M.refreshDelayMs([{ ...entry('a'), remaining: 12 }, entry('b')], 1000, 1003250), 6850);
  assert.equal(M.refreshDelayMs([entry('b')], 1000, 1020000), 100);
  assert.equal(M.refreshDelayMs([{ ...entry('hidden'), code: null }], 1000, 1000000), -1);
  assert.equal(M.refreshDelayMs([], 1000, 1000000), -1);
});

test('privacy clearing copies rows while removing all code, type and timing fields', () => {
  assert.equal(typeof M.hiddenEntries, 'function');
  const rows = [entry('a'), entry('b')];
  const hidden = M.hiddenEntries(rows, 'b');
  assert.equal(hidden[0].code, null);
  for (const key of ['nextCode', 'period', 'digits', 'remaining', 'kind']) assert.equal(hidden[0][key], null);
  assert.equal(hidden[1].code, '123456');
  assert.equal(rows[0].code, '123456');
  assert.notEqual(hidden[1], rows[1]);
});

test('glyphs are local and unknown issuers have a bounded readable fallback', () => {
  assert.equal(typeof M.issuerGlyph, 'function');
  assert.notEqual(M.issuerGlyph('GitHub'), 'G');
  assert.equal(M.issuerGlyph('Github'), M.issuerGlyph('github'));
  assert.equal(M.issuerGlyph('Example'), 'E');
  assert.equal(M.issuerGlyph(''), '?');
  assert.equal(M.issuerGlyph(null), '?');
});

test('new helper status errors give actionable bounded messages', () => {
  assert.match(M.errorHint({ code: 'HELPER_OUTDATED' }), /update|updat/i);
  assert.match(M.errorHint({ code: 'APP_LOCKED' }), /unlock|open/i);
  assert.match(M.errorHint({ code: 'COPY_FAILED' }), /clipboard|copy/i);
  assert.ok(M.errorHint({ code: 'X', message: 'x'.repeat(10000) }).length <= 200);
});

test('reply byte cap also applies to multibyte UTF-8 and error replies discard codes', () => {
  assert.equal(M.parseList('é'.repeat(600000)).error.code, 'OUTPUT_TOO_LARGE');
  const result = M.parseList(JSON.stringify({ ok: false, entries: [entry('must-discard')], error: { code: 'COPY_FAILED', message: 'Retry' } }));
  assert.deepEqual(result.entries, []);
  assert.equal(result.error.code, 'COPY_FAILED');
});

test('direct malformed arguments have safe output types and bounded countdown delays', () => {
  for (const value of [null, false, 0, {}, 'not-an-array']) {
    assert.deepEqual(M.filterAndOrder(value, null, value), []);
    assert.deepEqual(M.hiddenEntries(value), []);
    assert.equal(M.findIndexById(value, 'id'), -1);
    assert.equal(M.selectionAfterRefresh(value), '');
    assert.equal(M.refreshDelayMs(value, 1000, 1000000), -1);
  }
  assert.equal(M.liveRemaining({ ...entry('a'), code: '' }, 1000, 1000), 0);
  assert.equal(M.refreshDelayMs([{ ...entry('a'), period: 1e100, remaining: 1e100 }], 1000, 1000000), -1);
});

test('legacy formatting supports numeric codes without invoking objects', () => {
  const legacy = new Function(src + '\nreturn {formatCode, remainingFraction};')();
  assert.equal(legacy.formatCode(123456), '123 456');
  const hostile = { valueOf() { throw new Error('must not invoke'); }, toString() { throw new Error('must not invoke'); } };
  assert.equal(legacy.formatCode(hostile), '');
  assert.equal(legacy.remainingFraction(hostile, 30), 0);
  assert.equal(legacy.remainingFraction(15, hostile), 0.5);
});

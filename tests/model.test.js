// Unit tests for Model.js (pure JS, loaded without Qt). Run: node --test tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const src = fs.readFileSync(path.join(__dirname, "..", "Model.js"), "utf8").replace(".pragma library", "");
const names = ["parseList", "formatCode", "filterEntries", "remainingFraction", "entryKey", "errorHint"];
const M = new Function(src + "\nreturn {" + names.join(",") + "};")();

test("parseList accepts helper JSON", () => {
  const r = M.parseList('{"ok":true,"entries":[{"issuer":"A","code":"123456"}],"error":null}');
  assert.equal(r.ok, true);
  assert.equal(r.entries.length, 1);
});

test("parseList never throws on garbage", () => {
  for (const raw of ["", "nope", "null", "42", undefined]) {
    const r = M.parseList(raw);
    assert.equal(r.ok, false);
    assert.deepEqual(r.entries, []);
  }
});

test("formatCode groups 6 and 8 digit codes", () => {
  assert.equal(M.formatCode("123456"), "123 456");
  assert.equal(M.formatCode("12345678"), "1234 5678");
  assert.equal(M.formatCode(" 12 34 56 "), "123 456");
  assert.equal(M.formatCode("12345"), "12345");
});

test("filterEntries matches issuer and account case-insensitively", () => {
  const e = [{ issuer: "GitHub", account: "me@x" }, { issuer: "Proton", account: "you@y" }];
  assert.equal(M.filterEntries(e, "git").length, 1);
  assert.equal(M.filterEntries(e, "YOU@").length, 1);
  assert.equal(M.filterEntries(e, "").length, 2);
  assert.equal(M.filterEntries(e, "zzz").length, 0);
});

test("remainingFraction is clamped", () => {
  assert.equal(M.remainingFraction(15, 30), 0.5);
  assert.equal(M.remainingFraction(99, 30), 1);
  assert.equal(M.remainingFraction(-1, 30), 0);
  assert.equal(M.remainingFraction(10, 0), 0.3333333333333333);
});

test("errorHint maps known codes and falls back to message", () => {
  assert.match(M.errorHint({ code: "KEYRING_LOCKED" }), /locked/);
  assert.equal(M.errorHint({ code: "X", message: "boom" }), "boom");
  assert.equal(M.errorHint(null), "");
});

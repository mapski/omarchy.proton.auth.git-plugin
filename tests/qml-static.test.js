// Static checks on the QML: untrusted strings are rendered as plain text and
// codes never go on a command line. Run: node --test tests/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const panel = fs.readFileSync(path.join(__dirname, "..", "Panel.qml"), "utf8");

test("Text elements showing vault data or helper messages use PlainText", () => {
  const lines = panel.split("\n");
  const dynamic = lines
    .map((l, i) => [l, i])
    .filter(([l]) => /^\s*text:\s*.*(modelData|statusLine)/.test(l));
  assert.ok(dynamic.length >= 6);
  for (const [, i] of dynamic) {
    // textFormat must be set within the same element (next few lines).
    const block = lines.slice(i, i + 6).join("\n");
    assert.match(block, /textFormat:\s*Text\.PlainText/, `line ${i + 1}: ${lines[i].trim()}`);
  }
});

test("wl-copy receives the code on stdin, not argv", () => {
  const m = panel.match(/copyProcess\.command\s*=\s*(\[[^\]]*\])/);
  assert.ok(m, "copyProcess.command assignment not found");
  assert.deepEqual(JSON.parse(m[1]), ["wl-copy", "--sensitive"]);
  assert.match(panel, /write\(root\._pendingCopy\)/);
});

test("no network-capable APIs in QML/JS", () => {
  for (const f of ["Panel.qml", "BarWidget.qml", "Service.qml", "ProtonAuthIcon.qml", "Model.js"]) {
    const src = fs.readFileSync(path.join(__dirname, "..", f), "utf8");
    assert.doesNotMatch(src, /XMLHttpRequest|Qt\.openUrlExternally|WebSocket|https?:\/\//, f);
  }
});

const service = fs.readFileSync(path.join(__dirname, "..", "Service.qml"), "utf8");

test("the shell never reads the clipboard itself (#9048)", () => {
  assert.doesNotMatch(panel, /wl-paste/, "Panel.qml must not run wl-paste directly");
  const m = panel.match(/clipboardCheck\.command\s*=\s*(\[[^\]]*\])/);
  assert.ok(m, "clipboardCheck.command assignment not found");
  assert.equal(m[1], '["/bin/sh", clipcheckPath]');
  assert.match(panel, /write\(root\._pendingCheck\)/, "code must go to the check on stdin");
  assert.match(panel, /clipboardCheckOut\.text === "match\\n"/);
});

test("only known, bounded processes have output collectors; no stderr collectors", () => {
  for (const [name, src] of [["Panel.qml", panel], ["Service.qml", service]]) {
    assert.doesNotMatch(src, /SplitParser/, `${name}: SplitParser buffers undelimited data without limit`);
    assert.doesNotMatch(src, /stderr\s*:/, `${name}: stderr must not be collected`);
  }
  assert.equal((panel.match(/StdioCollector/g) || []).length, 1); // clipcheck token
  assert.equal((service.match(/StdioCollector/g) || []).length, 1); // capped helper JSON
});

test("every collected child process has a watchdog", () => {
  assert.match(panel, /running:\s*clipboardCheck\.running/);
  assert.match(service, /running:\s*listProcess\.running/);
});

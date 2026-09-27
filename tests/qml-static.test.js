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

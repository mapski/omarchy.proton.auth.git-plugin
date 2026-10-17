// Optional rendered regression: PROTONAUTH_QML_TEST=1 node --test tests/layout.test.js
// Requires a running Wayland session and the installed Omarchy shell components.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

test("rendered popup contains cards and scrolls keyboard selection", {
  skip: process.env.PROTONAUTH_QML_TEST !== "1",
}, () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "protonauth-layout-"));
  try {
    const shell = path.join(process.env.OMARCHY_PATH || "/usr/share/omarchy", "shell");
    for (const name of ["Commons", "Ui"]) fs.symlinkSync(path.join(shell, name), path.join(dir, name));
    fs.symlinkSync(path.resolve(process.env.PROTONAUTH_PLUGIN_DIR || path.join(__dirname, "..")), path.join(dir, "plugin"));
    const source = fs.readFileSync(path.join(__dirname, "qml/layout.qml"), "utf8")
      .replace('import "../../" as Plugin', 'import "plugin" as Plugin');
    fs.writeFileSync(path.join(dir, "shell.qml"), source);
    const result = spawnSync("quickshell", ["-p", dir, "--no-duplicate"], {
      encoding: "utf8", timeout: 15000,
    });
    const output = result.stdout + result.stderr;
    assert.equal(result.status, 0, output);
    assert.doesNotMatch(output, /LAYOUT_FAIL|TypeError|ReferenceError|Binding loop|Failed to load configuration/);
    assert.match(output, /LAYOUT_PASS/);
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
});

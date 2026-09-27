// Ownership tests for install.sh / uninstall.sh / the helper's temp dirs.
// Everything runs in a throwaway sandbox (HOME, plugin dir, TMPDIR, state dir).
// Run: node --test tests/*.test.js   (needs bash, jq, python3)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const ROOT = path.join(__dirname, "..");
const ID = "io.github.mapski.proton-authenticator";

function sandbox() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "pa-test-"));
  const dirs = {
    base,
    home: path.join(base, "home"),
    plugins: path.join(base, "plugins"),
    tmp: path.join(base, "tmp"),
    state: path.join(base, "state"),
  };
  for (const d of [dirs.home, dirs.plugins, dirs.tmp, dirs.state]) fs.mkdirSync(d, { recursive: true });
  dirs.env = {
    PATH: "/usr/local/bin:/usr/bin:/bin",
    HOME: dirs.home,
    TMPDIR: dirs.tmp,
    XDG_STATE_HOME: dirs.state,
    OMARCHY_PLUGINS_DIR: dirs.plugins,
    OMARCHY_SHELL_JSON: path.join(dirs.home, "shell.json"),
  };
  return dirs;
}
const cleanup = (sb) => fs.rmSync(sb.base, { recursive: true, force: true });

function sh(sb, script, ...args) {
  const r = spawnSync("bash", [path.join(ROOT, script), ...args], { env: sb.env, encoding: "utf8" });
  assert.equal(r.status, 0, `${script} ${args.join(" ")} failed:\n${r.stdout}\n${r.stderr}`);
  return r;
}
const installId = (sb) => fs.readFileSync(path.join(sb.state, ID, "install-id"), "utf8").trim();
function sentinelDir(p) {
  fs.mkdirSync(p, { recursive: true });
  fs.writeFileSync(path.join(p, "keep-me"), "unrelated data");
}
const intact = (p) => fs.readFileSync(path.join(p, "keep-me"), "utf8") === "unrelated data";

test("install.sh never touches a pre-existing staging-looking dir", () => {
  const sb = sandbox();
  try {
    // Every name the old installer could have used (.<id>.install.<pid>) plus
    // mktemp-looking ones: all must survive untouched.
    const foreign = ["1", "12345", "99999", "AAAAAA", "zzzzzz"].map((s) => path.join(sb.plugins, `.${ID}.install.${s}`));
    foreign.forEach(sentinelDir);
    sh(sb, "install.sh", "--no-enable");
    sh(sb, "install.sh", "--no-enable"); // idempotent re-run
    for (const f of foreign) assert.ok(intact(f), `${f} was modified`);
    const target = path.join(sb.plugins, ID);
    assert.equal(JSON.parse(fs.readFileSync(path.join(target, "manifest.json"), "utf8")).id, ID);
    // No staging dir of our own left behind.
    const leftovers = fs.readdirSync(sb.plugins).filter((n) => n.startsWith(`.${ID}.install.`));
    assert.equal(leftovers.length, foreign.length);
    assert.match(installId(sb), /^[0-9a-f]{32}$/);
  } finally {
    cleanup(sb);
  }
});

test("uninstall.sh removes only temp dirs carrying this installation's marker", () => {
  const sb = sandbox();
  try {
    sh(sb, "install.sh", "--no-enable");
    const id = installId(sb);
    const t = (n) => path.join(sb.tmp, `protonauth-idb-${n}`);
    sentinelDir(t("foreign")); // no marker at all
    sentinelDir(t("otherinstall"));
    fs.writeFileSync(path.join(t("otherinstall"), `.${ID}`), "0".repeat(32) + "\n");
    sentinelDir(t("ours"));
    fs.writeFileSync(path.join(t("ours"), `.${ID}`), id + "\n");
    // Symlink to a dir with a valid marker: the link must not be followed/removed.
    sentinelDir(path.join(sb.base, "elsewhere"));
    fs.writeFileSync(path.join(sb.base, "elsewhere", `.${ID}`), id + "\n");
    fs.symlinkSync(path.join(sb.base, "elsewhere"), t("link"));

    const r = sh(sb, "uninstall.sh");
    assert.ok(intact(t("foreign")), "foreign temp dir without marker was removed");
    assert.ok(intact(t("otherinstall")), "temp dir of another installation was removed");
    assert.ok(fs.lstatSync(t("link")).isSymbolicLink(), "symlinked temp dir was removed");
    assert.ok(intact(path.join(sb.base, "elsewhere")));
    assert.equal(fs.existsSync(t("ours")), false, "our own stray temp dir was not removed");
    assert.match(r.stderr, /left alone/);
  } finally {
    cleanup(sb);
  }
});

test("uninstall.sh --purge leaves foreign plugin dirs and look-alike backups alone", () => {
  const sb = sandbox();
  try {
    sh(sb, "install.sh", "--no-enable");
    const bak = path.join(sb.plugins, `.${ID}.bak-foreign`);
    const stage = path.join(sb.plugins, `.${ID}.install.12345`);
    sentinelDir(bak);
    sentinelDir(stage);
    const legacy = path.join(sb.plugins, "mp.protonauth");
    sentinelDir(legacy);
    fs.writeFileSync(path.join(legacy, "manifest.json"), JSON.stringify({ id: "someone.else" }));
    const ours = path.join(sb.plugins, `.${ID}.bak-20260101-000000`);
    fs.cpSync(path.join(sb.plugins, ID), ours, { recursive: true });

    sh(sb, "uninstall.sh", "--purge");
    assert.equal(fs.existsSync(path.join(sb.plugins, ID)), false, "installed plugin not removed");
    assert.equal(fs.existsSync(ours), false, "our own backup not removed");
    assert.ok(intact(bak) && intact(stage) && intact(legacy), "foreign dir removed");
  } finally {
    cleanup(sb);
  }
});

test("helper temp dir: mkdtemp, 0700, ownership marker, removed on exit and on error", () => {
  const sb = sandbox();
  try {
    const py = `
import os, sqlite3, stat, sys, tempfile
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, "bin"))})
import protonauth_list as h
src = Path(os.environ["TMPDIR"]) / "src.sqlite3"
c = sqlite3.connect(src); c.execute("create table t(x)"); c.commit(); c.close()
with h.vault_snapshot(src) as tmp:
    d = tmp.parent
    assert d.name.startswith("protonauth-idb-"), d
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert (d / ("." + h.PLUGIN_ID)).read_text().strip() == h.install_id()
assert not d.exists(), "not cleaned up"
try:
    with h.vault_snapshot(src) as tmp:
        d = tmp.parent
        raise RuntimeError("boom")
except RuntimeError:
    pass
assert not d.exists(), "not cleaned up on error"
print("ok")
`;
    const r = spawnSync("python3", ["-c", py], { env: sb.env, encoding: "utf8" });
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout.trim(), "ok");
  } finally {
    cleanup(sb);
  }
});

test("helper temp dir is removed when the helper gets SIGTERM", async () => {
  const sb = sandbox();
  try {
    const py = `
import os, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, "bin"))})
import protonauth_list as h
import signal
for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(s, h._exit_on_signal)
src = Path(os.environ["TMPDIR"]) / "src.sqlite3"
c = sqlite3.connect(src); c.execute("create table t(x)"); c.commit(); c.close()
with h.vault_snapshot(src) as tmp:
    assert oct(tmp.stat().st_mode & 0o777) == "0o600", oct(tmp.stat().st_mode)
    print(tmp.parent, flush=True)
    time.sleep(30)
`;
    const { spawn } = require("node:child_process");
    const child = spawn("python3", ["-c", py], { env: sb.env });
    let out = "";
    let err = "";
    child.stderr.on("data", (d) => (err += d));
    const dir = await new Promise((resolve, reject) => {
      child.stdout.on("data", (d) => {
        out += d;
        if (out.includes("\n")) resolve(out.trim());
      });
      child.on("exit", () => reject(new Error("helper exited early: " + err)));
    });
    assert.ok(fs.existsSync(dir));
    const code = await new Promise((resolve) => {
      child.removeAllListeners("exit");
      child.on("exit", (c) => resolve(c));
      child.kill("SIGTERM");
    });
    assert.equal(code, 128 + 15);
    assert.equal(fs.existsSync(dir), false, "temp dir left behind after SIGTERM");
  } finally {
    cleanup(sb);
  }
});

test("helper fails closed: unexpected exception text is never printed", () => {
  const sb = sandbox();
  try {
    const py = `
import sys
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, "bin"))})
import protonauth_list as h
h._IMPORT_ERROR = None
def boom():
    raise RuntimeError("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP")
h.list_entries = boom
sys.argv = ["protonauth-list"]
sys.exit(h.main())
`;
    const r = spawnSync("python3", ["-c", py], { env: sb.env, encoding: "utf8" });
    assert.equal(r.status, 1);
    const j = JSON.parse(r.stdout);
    assert.equal(j.ok, false);
    assert.equal(j.error.message, "internal error (RuntimeError)");
    assert.doesNotMatch(r.stdout + r.stderr, /JBSWY3DP/);
  } finally {
    cleanup(sb);
  }
});

// Minimal omarchy / omarchy-shell stubs so install.sh runs its bar/legacy steps.
function withStubs(sb) {
  const bin = path.join(sb.base, "bin");
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, "omarchy-shell"),
    '#!/bin/sh\ncase "$*" in\n  "shell ping") echo ok ;;\n  "shell listShellConfig") echo \'{"bar":{"layout":{"right":["mp.protonauth"]}}}\' ;;\nesac\nexit 0\n');
  fs.writeFileSync(path.join(bin, "omarchy"),
    '#!/bin/sh\ncase "$*" in\n  "plugin list --json") echo "[]" ;;\nesac\nexit 0\n');
  fs.chmodSync(path.join(bin, "omarchy-shell"), 0o755);
  fs.chmodSync(path.join(bin, "omarchy"), 0o755);
  sb.env.PATH = `${bin}:${sb.env.PATH}`;
}
function tsAt(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

test("install.sh: shell.json backup never writes through a planted path; foreign legacy dir untouched", () => {
  const sb = sandbox();
  try {
    withStubs(sb);
    const shellJson = sb.env.OMARCHY_SHELL_JSON;
    fs.writeFileSync(shellJson, '{"bar":{}}');
    const victim = path.join(sb.base, "victim");
    fs.writeFileSync(victim, "do not overwrite");
    // Plant symlinks at every fixed backup name the old scripts would use soon.
    const now = Date.now();
    for (let s = -1; s < 10; s++) {
      fs.symlinkSync(victim, `${shellJson}.bak-proton-auth-${tsAt(new Date(now + s * 1000))}`);
    }
    const foreignLegacy = path.join(sb.plugins, "mp.protonauth");
    sentinelDir(foreignLegacy); // no manifest -> not ours
    const ownLegacy = path.join(sb.plugins, "mp.proton-authenticator-plugin");
    fs.mkdirSync(ownLegacy);
    fs.writeFileSync(path.join(ownLegacy, "manifest.json"), JSON.stringify({ id: "mp.proton-authenticator-plugin" }));

    const r = sh(sb, "install.sh");
    assert.equal(fs.readFileSync(victim, "utf8"), "do not overwrite");
    const backups = fs.readdirSync(path.dirname(shellJson))
      .filter((n) => n.startsWith("shell.json.bak-proton-auth-"))
      .map((n) => path.join(path.dirname(shellJson), n))
      .filter((p) => !fs.lstatSync(p).isSymbolicLink());
    assert.equal(backups.length, 1, r.stdout);
    assert.equal(fs.readFileSync(backups[0], "utf8"), '{"bar":{}}');
    assert.ok(intact(foreignLegacy), "foreign legacy dir moved");
    assert.equal(fs.existsSync(ownLegacy), false, "own legacy dir not retired");
    assert.ok(fs.readdirSync(sb.plugins).some((n) => n.startsWith(".mp.proton-authenticator-plugin.bak-")));
  } finally {
    cleanup(sb);
  }
});

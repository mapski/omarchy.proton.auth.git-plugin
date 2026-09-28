// Bounded reads of untrusted output (marketplace review #9048).
//  - bin/protonauth-clipcheck: the clipboard is compared in a short-lived
//    child that reads at most len(code)+1 bytes under a timeout and prints only
//    "match"/"nomatch". Fake wl-paste programs stand in for hostile owners.
//  - bin/protonauth_list.py: secret-tool reads, output size, labels, timeouts.
// Run: node --test tests/*.test.js   (needs sh, coreutils, python3)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const ROOT = path.join(__dirname, "..");
const CLIPCHECK = path.join(ROOT, "bin", "protonauth-clipcheck");
const BIN = path.join(ROOT, "bin");

// Runs the clipcheck script with `wlPaste` (a sh script body) as wl-paste.
function clipcheck(wlPaste, expected) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "pa-clip-"));
  try {
    const fake = path.join(base, "wl-paste");
    fs.writeFileSync(fake, `#!/bin/sh\n${wlPaste}\n`, { mode: 0o755 });
    // The script hard-codes /usr/bin/wl-paste; test a copy pointing at the fake.
    const src = fs.readFileSync(CLIPCHECK, "utf8");
    assert.match(src, /^WL_PASTE=\/usr\/bin\/wl-paste$/m);
    const script = path.join(base, "clipcheck");
    fs.writeFileSync(script, src.replace(/^WL_PASTE=\/usr\/bin\/wl-paste$/m, `WL_PASTE=${fake}`));
    const env = { PATH: "/usr/local/bin:/usr/bin:/bin", LOG: path.join(base, "log") };
    const t0 = Date.now();
    const r = spawnSync("sh", [script], { input: expected, env, encoding: "utf8", timeout: 15000 });
    const log = fs.existsSync(env.LOG) ? fs.readFileSync(env.LOG, "utf8") : "";
    return { ...r, ms: Date.now() - t0, log };
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
}

test("clipcheck: exact code on the clipboard -> match; argv is fixed", () => {
  const r = clipcheck('echo "$*" > "$LOG"; printf 123456', "123456");
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, "match\n");
  assert.equal(r.log.trim(), "--no-newline --type text");
});

test("clipcheck: anything but the exact bytes -> nomatch", () => {
  const cases = {
    other: "printf 654321",
    prefix: "printf 12345",
    longer: "printf 1234567",
    trailingNewline: "printf '123456\\n'",
    nulByte: "printf '123\\000456'",
    empty: ":",
    noTextType: "echo 'No suitable type of content copied' >&2; exit 1",
  };
  for (const [name, body] of Object.entries(cases)) {
    const r = clipcheck(body, "123456");
    assert.equal(r.stdout, "nomatch\n", name);
  }
});

test("clipcheck: only 1-10 digit codes are accepted, wl-paste not even run otherwise", () => {
  for (const bad of ["", "12a456", "12 34", "12345678901", "-n", "$(id)"]) {
    const r = clipcheck('echo called > "$LOG"; printf "%s" "$1"', bad);
    assert.equal(r.stdout, "nomatch\n", JSON.stringify(bad));
    assert.equal(r.log, "", `wl-paste ran for ${JSON.stringify(bad)}`);
  }
});

test("clipcheck: endless clipboard (SIGPIPE ignored) is bounded in time and output", () => {
  // A clipboard owner that never stops sending data.
  const r = clipcheck("trap '' PIPE; exec cat /dev/zero", "123456");
  assert.equal(r.stdout, "nomatch\n");
  assert.ok(r.ms < 5000, `took ${r.ms} ms`);
});

test("clipcheck: 100 MiB text clipboard starting with the code -> nomatch, fast", () => {
  const r = clipcheck("printf 123456; head -c 104857600 /dev/zero | tr '\\000' 7", "123456");
  assert.equal(r.stdout, "nomatch\n");
  assert.ok(r.ms < 5000, `took ${r.ms} ms`);
});

test("clipcheck: owner that hangs and ignores SIGTERM is killed (timeout -k)", () => {
  const r = clipcheck("trap '' TERM PIPE; printf 1234; exec sleep 60", "123456");
  assert.equal(r.stdout, "nomatch\n");
  assert.ok(r.ms >= 1500 && r.ms < 6000, `took ${r.ms} ms`);
  const left = spawnSync("pgrep", ["-f", "exec sleep 60|^sleep 60$"], { encoding: "utf8" });
  assert.equal(left.stdout.trim(), "", "a wl-paste stand-in survived");
});

test("clipcheck: stdout is only ever a fixed token", () => {
  // Even when the clipboard holds data that looks like a token.
  for (const body of ["printf 'match\\n'", "printf match", "cat /dev/urandom"]) {
    const r = clipcheck(body, "123456");
    assert.ok(["match\n", "nomatch\n"].includes(r.stdout), r.stdout);
    assert.ok(r.stdout.length <= 8);
  }
});

function py(code) {
  const r = spawnSync("python3", ["-c", `import sys\nsys.path.insert(0, ${JSON.stringify(BIN)})\nimport protonauth_list as h\n${code}`],
    { encoding: "utf8", timeout: 30000, env: { PATH: "/usr/local/bin:/usr/bin:/bin", HOME: os.tmpdir() } });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout.trim();
}

test("helper: run_bounded never reads more than limit+1 bytes and enforces a timeout", () => {
  const out = py(`
import subprocess, time
t = time.monotonic()
rc, data = h.run_bounded(["sh", "-c", "exec cat /dev/zero"], 64, 5)
assert rc is None and len(data) == 65, (rc, len(data))
rc, data = h.run_bounded(["printf", "abc"], 64, 5)
assert (rc, data) == (0, b"abc"), (rc, data)
try:
    h.run_bounded(["sleep", "30"], 64, 1)
    raise SystemExit("no timeout")
except subprocess.TimeoutExpired:
    pass
assert time.monotonic() - t < 10
print("ok")
`);
  assert.equal(out, "ok");
});

test("helper: oversized secret-tool output and a hung secret-tool fail closed", () => {
  const out = py(`
import os, tempfile
d = tempfile.mkdtemp()
def fake(body):
    p = os.path.join(d, "secret-tool")
    with open(p, "w") as f:
        f.write("#!/bin/sh\\n" + body + "\\n")
    os.chmod(p, 0o755)
    h.SECRET_TOOL = p
fake("exec cat /dev/zero")
try:
    h.read_keyring("abc"); raise SystemExit("accepted")
except h.Err as e:
    assert e.code == "DECRYPT_FAILED", e.code
h.SECRET_TOOL_TIMEOUT_SEC = 1
fake("exec sleep 30")
try:
    h.read_keyring("abc"); raise SystemExit("accepted")
except h.Err as e:
    assert e.code == "HELPER_TIMEOUT", e.code
fake("printf 0123456789abcdef0123456789abcdef")
assert len(h.read_keyring("abc")) == 32
print("ok")
`);
  assert.equal(out, "ok");
});

test("helper: stdout is capped, messages truncated, whole run has a deadline", () => {
  const out = py(`
import io, json, contextlib
h._IMPORT_ERROR = None
h.list_entries = lambda: {"ok": True, "entries": [{"issuer": "x" * (2 * h.MAX_OUTPUT_BYTES)}]}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sys.argv = ["protonauth-list"]; h.main()
s = buf.getvalue()
assert len(s.encode()) <= h.MAX_OUTPUT_BYTES, len(s)
assert json.loads(s)["error"]["code"] == "OUTPUT_TOO_LARGE"
assert len(h.fail("X", "m" * 10000)["error"]["message"]) == h.MAX_MESSAGE_CHARS
import time
h.HELPER_TIMEOUT_SEC = 1
def slow():
    time.sleep(5)
h.list_entries = slow
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = h.main()
assert rc == 1 and json.loads(buf.getvalue())["error"]["code"] == "HELPER_TIMEOUT", buf.getvalue()
print("ok")
`);
  assert.equal(out, "ok");
});

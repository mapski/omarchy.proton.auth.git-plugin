// Bounded helper subprocess I/O and output. Clipboard owner behavior is tested
// in test_clipboard_owner.py; the old clipboard-reading checker was removed.
const test = require("node:test");
const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const BIN = path.join(__dirname, "..", "bin");

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
h.ALLOW_PROMPT = True  # This isolated test fakes only the explicitly prompting client.
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

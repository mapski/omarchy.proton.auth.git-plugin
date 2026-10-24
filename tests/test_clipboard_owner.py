"""Exercise detached ownership using fake processes; no Wayland connection."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

BIN = Path(__file__).resolve().parents[1] / 'bin'
OWNER = BIN / 'protonauth_clipboard.py'

class ClipboardOwner(unittest.TestCase):
    def launch(self, temp, behavior, code='287082', duration=1):
        fake = Path(temp) / 'fake-wl-copy'
        fake.write_text('#!/usr/bin/python3\nimport sys, time, json, signal\nfrom pathlib import Path\n' +
                        'data = sys.stdin.buffer.read()\n' +
                        'Path('+repr(str(Path(temp)/'received'))+').write_text(json.dumps({"argv":sys.argv[1:], "stdin":data.decode()}))\n' + behavior)
        fake.chmod(0o755)
        loader = 'import sys; sys.path.insert(0, '+repr(str(BIN))+'); import protonauth_clipboard as o; o.WL_COPY='+repr(str(fake))+'; raise SystemExit(o.main())'
        proc = subprocess.Popen(['/usr/bin/python3', '-E', '-s', '-c', loader, str(duration)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        proc.stdin.write(code.encode())
        proc.stdin.close()
        proc.stdin = None
        return proc

    def test_owner_ack_survives_launcher_then_expires_only_own_process(self):
        self.assertTrue(OWNER.exists(), 'detached clipboard owner is missing')
        with tempfile.TemporaryDirectory() as temp:
            ended = Path(temp)/'ended'
            proc = self.launch(temp, 'signal.signal(signal.SIGTERM, lambda *args: (Path('+repr(str(ended))+').touch(), sys.exit(0)))\ntime.sleep(30)\n')
            try:
                self.assertEqual(proc.stdout.readline(), b'ready\n')
                self.assertIsNone(proc.poll(), 'owner must remain alive after acknowledgement')
                data = json.loads((Path(temp)/'received').read_text())
                self.assertEqual(data['stdin'], '287082')
                self.assertIn('--foreground', data['argv'])
                self.assertIn('--sensitive', data['argv'])
                self.assertNotIn('--clear', data['argv'])
                self.assertNotIn('287082', str(data['argv']))
                stdout, stderr = proc.communicate(timeout=4)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertEqual(stdout, b'')
                self.assertTrue(ended.exists(), 'expiration must terminate the owned process')
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_failure_has_fixed_ack_and_no_code(self):
        self.assertTrue(OWNER.exists(), 'detached clipboard owner is missing')
        with tempfile.TemporaryDirectory() as temp:
            proc = self.launch(temp, 'sys.exit(1)\n')
            stdout, stderr = proc.communicate(timeout=4)
            self.assertEqual(stdout, b'failed\n')
            self.assertEqual(stderr, b'')
            self.assertNotEqual(proc.returncode, 0)

    def test_replacement_exits_without_clearing_new_owner(self):
        self.assertTrue(OWNER.exists(), 'detached clipboard owner is missing')
        with tempfile.TemporaryDirectory() as temp:
            proc = self.launch(temp, 'time.sleep(0.4)\n', code='PV9M4', duration=10)
            t0 = time.monotonic()
            stdout, stderr = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(stdout, b'ready\n')
            self.assertLess(time.monotonic()-t0, 2)
            self.assertNotIn('--clear', (Path(temp)/'received').read_text())

    def test_invalid_code_never_starts_owner(self):
        self.assertTrue(OWNER.exists(), 'detached clipboard owner is missing')
        for code in ('bad-code', '12345\n', '12345678901', '$(id)', ''):
            with tempfile.TemporaryDirectory() as temp:
                proc = self.launch(temp, 'time.sleep(30)\n', code=code)
                stdout, stderr = proc.communicate(timeout=3)
                self.assertEqual(stdout, b'failed\n')
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse((Path(temp)/'received').exists())

if __name__ == '__main__':
    unittest.main()

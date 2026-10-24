#!/usr/bin/python3
"""Detached, bounded owner of one sensitive Wayland clipboard selection.

The code arrives only on stdin. A fixed acknowledgement confirms the foreground
owner survived its startup grace, not that a future paste or clipboard manager
will preserve/delete it. Expiry terminates this process's child only.
"""
from __future__ import annotations

import contextlib
import os
import re
import selectors
import signal
import subprocess
import sys
import time

from protonauth_list import harden, safe_env

WL_COPY = "/usr/bin/wl-copy"
STARTUP_GRACE = 0.25
CODE_RE = re.compile(rb"(?:[0-9]{6,8}|[23456789BCDFGHJKMNPQRTVWXY]{5})")


def _terminated(_signum, _frame):
    raise SystemExit(1)


def read_code() -> bytes:
    deadline = time.monotonic() + 2
    data = b""
    with selectors.DefaultSelector() as selector:
        selector.register(0, selectors.EVENT_READ)
        while len(data) <= 10:
            left = deadline-time.monotonic()
            if left <= 0 or not selector.select(left):
                raise ValueError("input timeout")
            chunk = os.read(0, 11-len(data))
            if not chunk:
                break
            data += chunk
    if not CODE_RE.fullmatch(data):
        raise ValueError("invalid code")
    return data


def stop_owner(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        proc.wait(timeout=0.5)


def main() -> int:
    proc = None
    acknowledged = False
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, _terminated)
    try:
        harden()
        if len(sys.argv) != 2:
            raise ValueError("missing duration")
        duration = int(sys.argv[1])
        if not 1 <= duration <= 600:
            raise ValueError("invalid duration")
        code = read_code()
        proc = subprocess.Popen([WL_COPY, "--foreground", "--sensitive", "--type", "text/plain;charset=utf-8"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=safe_env())
        assert proc.stdin is not None
        proc.stdin.write(code)
        proc.stdin.close()
        del code  # Python copies/immutable objects are not guaranteed erased.
        try:
            status = proc.wait(timeout=STARTUP_GRACE)
            if status != 0:
                raise ValueError("owner startup failed")
            # Exit zero can mean a clipboard manager has already taken over.
        except subprocess.TimeoutExpired:
            pass
        os.write(1, b"ready\n")
        acknowledged = True
        os.close(1)  # Let the launcher return while this owner stays alive.
        try:
            proc.wait(timeout=duration)
        except subprocess.TimeoutExpired:
            stop_owner(proc)
        return 0
    except (Exception, SystemExit):  # noqa: BLE001 - fixed failure token, never disclose input
        if not acknowledged:
            with contextlib.suppress(OSError):
                os.write(1, b"failed\n")
        return 1
    finally:
        stop_owner(proc)


if __name__ == "__main__":
    sys.exit(main())

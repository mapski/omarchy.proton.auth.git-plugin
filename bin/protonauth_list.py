#!/usr/bin/env python3
"""Read Proton Authenticator locally; emit bounded JSON with no seeds/root keys."""
from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import ctypes
import hashlib
import hmac
import json
import math
import os
import re
import resource
import secrets
import selectors
import shutil
import signal
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

__version__ = "1.1.0"

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError as _imp_err:  # reported as JSON by main()
    _IMPORT_ERROR: ImportError | None = _imp_err
else:
    _IMPORT_ERROR = None

DATA_DIR = Path.home() / ".local/share/me.proton.authenticator"
HKDF_INFO = b"authenticator;storage-key"
ITEM_AAD = b"authenticator;item"
SERVICE = "com.proton.authenticator"
# Fixed absolute paths prevent PATH substitution of external clients.
SECRET_TOOL = "/usr/bin/secret-tool"
GDBUS = "/usr/bin/gdbus"
ALLOW_PROMPT = False
ADAPTER_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")

PLUGIN_ID = "io.github.mapski.proton-authenticator"
# Ownership marker written into every temp dir this installation creates.
# uninstall.sh only removes a leftover protonauth-idb-* dir whose marker holds
# this installation's id (see install_id()).
TMP_PREFIX = "protonauth-idb-"
TMP_MARKER = "." + PLUGIN_ID
INSTALL_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Bounds on everything this helper reads from other programs or writes to the
# shell (omarchy-shell buffers our whole stdout, so it must stay small).
HELPER_TIMEOUT_SEC = 20        # whole run; SIGALRM -> HELPER_TIMEOUT JSON
SECRET_TOOL_TIMEOUT_SEC = 10   # a keyring unlock prompt left open, etc.
SECRET_MAX_BYTES = 64          # the vault key is 32 bytes; more is refused
MAX_ENTRIES = 1000
MAX_LABEL_CHARS = 256          # issuer / account, truncated for display
MAX_ID_CHARS = 128
MAX_MESSAGE_CHARS = 200
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_DB_BYTES = 64 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024
MAX_SCAN_FILES = 4096
MAX_PREFS_BYTES = 16384
ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")
HIDDEN_FIELDS = ("code", "nextCode", "period", "digits", "remaining", "kind")



def state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    base = Path(xdg) if xdg.startswith("/") else Path.home() / ".local/state"
    return base / PLUGIN_ID


def _read_id(f: Path) -> str:
    """The id stored in f, or "" if missing/invalid. Never follows a symlink."""
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return ""
    with os.fdopen(fd) as fh:
        value = fh.read(128).strip()
    return value if INSTALL_ID_RE.match(value) else ""


def install_id() -> str:
    """Per-installation random id (created by install.sh, or here on first use)."""
    d = state_dir()
    f = d / "install-id"
    value = _read_id(f)
    if value:
        return value
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = secrets.token_hex(16)
    try:
        # O_EXCL: never overwrite an id another process just wrote.
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        existing = _read_id(f)
        if existing:
            return existing
        raise Err("VAULT_UNREADABLE", f"invalid install id in {f}") from None
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    return value


class Err(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def safe_env() -> dict:
    """Keep session transport variables, discard code-loading environment."""
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("PYTHON", "LD_", "DYLD_"))
            and key not in ("BASH_ENV", "ENV", "GCONV_PATH", "GI_TYPELIB_PATH",
                            "GIO_EXTRA_MODULES", "GIO_MODULE_DIR", "GTK_PATH", "WAYLAND_DEBUG")}


def harden() -> dict:
    """Linux dump protection is mandatory; memory locking is best effort.

    Python immutable objects and allocator copies cannot be reliably erased.
    This is process hardening, not a same-user malware isolation boundary.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        libc = ctypes.CDLL(None, use_errno=True)
        if not sys.platform.startswith("linux") or libc.prctl(4, 0, 0, 0, 0) != 0:
            raise OSError("non-dumpable unavailable")
        locked = libc.mlockall(1) == 0  # MCL_CURRENT only; future allocations may be unlocked.
    except (OSError, ValueError, AttributeError) as exc:
        raise Err("PROCESS_HARDENING", "cannot disable process dumps") from exc
    return {"coreDumpsDisabled": True, "nonDumpable": True, "memoryLocked": locked,
            "memoryLockScope": "current-pages" if locked else "none", "zeroization": "not-guaranteed"}


def _db_size(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal")):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DB_BYTES:
            raise Err("OUTPUT_TOO_LARGE", "vault database exceeds safe input bounds")


def _connect(path: Path, readonly: bool = True):
    con = sqlite3.connect("file:" + quote(str(path), safe="/") + ("?mode=ro" if readonly else ""), uri=True, timeout=1)
    con.create_collation("IDBKEY", lambda a, b: (a > b) - (a < b))
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_ROW_BYTES * 2)
    con.set_progress_handler(lambda: 1, 1000000)
    return con


def find_idb() -> Path:
    root = DATA_DIR / "databases" / "indexeddb"
    if not root.is_dir():
        raise Err("VAULT_UNREADABLE", "Proton IndexedDB directory is missing")
    candidates = []
    scanned = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        scanned += len(dirs) + len(files)
        if scanned > MAX_SCAN_FILES:
            raise Err("OUTPUT_TOO_LARGE", "vault discovery exceeds safe input bounds")
        if "IndexedDB.sqlite3" not in files:
            continue
        path = Path(directory) / "IndexedDB.sqlite3"
        _db_size(path)
        try:
            with contextlib.closing(_connect(path)) as con:
                names = {r[0] for r in con.execute("SELECT name FROM ObjectStoreInfo LIMIT 32")}
            if {"items", "storageKey"} <= names:
                candidates.append(path)
        except (sqlite3.Error, OSError):
            continue
    if not candidates:
        raise Err("VAULT_UNREADABLE", "no IndexedDB with items/storageKey stores")
    return max(candidates, key=lambda p: p.stat().st_mtime)


@contextlib.contextmanager
def vault_snapshot(src: Path):
    """Private read-only copy of the vault DB in a fresh mkdtemp() dir.

    The dir carries an ownership marker with this installation's id and is
    removed (only that exact path) when the block exits, even on errors.
    """
    _db_size(src)
    owner = install_id()
    tmpdir = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))  # unique, 0700, ours
    try:
        fd = os.open(tmpdir / TMP_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(owner + "\n")
        tmp = tmpdir / "idb.sqlite3"
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))  # 0600 before any data
        src_con = _connect(src)
        dst_con = sqlite3.connect(tmp)
        try:
            deadline = time.monotonic() + HELPER_TIMEOUT_SEC
            def progress(_status, _remaining, total):
                if total * 4096 > MAX_DB_BYTES or time.monotonic() > deadline:
                    raise Err("OUTPUT_TOO_LARGE", "vault snapshot exceeds safe input bounds")
            src_con.backup(dst_con, pages=256, progress=progress)
            if tmp.stat().st_size > MAX_DB_BYTES:
                raise Err("OUTPUT_TOO_LARGE", "vault snapshot exceeds safe input bounds")
        finally:
            dst_con.close()
            src_con.close()
        yield tmp
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# WebKit SerializedScriptValue v15, restricted to the values used in Proton's
# records. Format grammar/tags: WebKit Source/WebCore/bindings/js/
# SerializedScriptValue.cpp (webkitgtk-2.50.1). Decode boundaries, never search
# strings/ciphertext for property-name byte patterns.
MAX_SSV_DEPTH = 8
MAX_SSV_PROPERTIES = 256
MAX_SSV_POOL = 512
_UNDEFINED = object()


class _SSVReader:
    def __init__(self, blob: bytes):
        if not isinstance(blob, bytes) or len(blob) > MAX_ROW_BYTES:
            raise Err("UNSUPPORTED_SCHEMA", "invalid serialized record size")
        self.blob = blob
        self.pos = 0
        self.pool = []
        self.properties = 0

    def take(self, size: int) -> bytes:
        end = self.pos + size
        if size < 0 or end > len(self.blob):
            raise Err("UNSUPPORTED_SCHEMA", "truncated serialized record")
        data = self.blob[self.pos:end]
        self.pos = end
        return data

    def integer(self, size=4, signed=False) -> int:
        return int.from_bytes(self.take(size), "little", signed=signed)

    def text(self, head: int) -> str:
        if head == 0xFFFFFFFE:
            width = 1 if len(self.pool) <= 0xFF else 2 if len(self.pool) <= 0xFFFF else 4
            index = self.integer(width)
            if index >= len(self.pool):
                raise Err("UNSUPPORTED_SCHEMA", "invalid serialized string reference")
            return self.pool[index]
        if head == 0xFFFFFFFF or len(self.pool) >= MAX_SSV_POOL:
            raise Err("UNSUPPORTED_SCHEMA", "invalid serialized string pool")
        eight_bit = bool(head & 0x80000000)
        length = (head & 0x7FFFFFFF) * (1 if eight_bit else 2)
        if length > MAX_ROW_BYTES:
            raise Err("UNSUPPORTED_SCHEMA", "serialized string exceeds input bounds")
        try:
            value = self.take(length).decode("latin1" if eight_bit else "utf-16-le")
        except UnicodeError as exc:
            raise Err("UNSUPPORTED_SCHEMA", "invalid serialized string encoding") from exc
        # StringData, including zero length, enters WebKit's constant pool.
        # EmptyStringTag bypasses StringData and does not enter this pool.
        self.pool.append(value)
        return value

    def value(self, depth=1):
        if depth > MAX_SSV_DEPTH:
            raise Err("UNSUPPORTED_SCHEMA", "serialized object exceeds depth bound")
        tag = self.integer(1)
        if tag == 0x02:
            result = {}
            while True:
                head = self.integer()
                if head == 0xFFFFFFFF:
                    return result
                self.properties += 1
                if self.properties > MAX_SSV_PROPERTIES:
                    raise Err("UNSUPPORTED_SCHEMA", "serialized object exceeds property bound")
                name = self.text(head)
                if name in result:
                    raise Err("UNSUPPORTED_SCHEMA", "duplicate serialized property")
                result[name] = self.value(depth+1)
        if tag == 0x03:
            return _UNDEFINED
        if tag == 0x04:
            return None
        if tag == 0x05:
            return self.integer(4, signed=True)
        if tag in (0x06, 0x07):
            return tag - 0x06
        if tag in (0x08, 0x09):
            return tag == 0x09
        if tag == 0x0A:
            value = struct.unpack("<d", self.take(8))[0]
            if not math.isfinite(value):
                raise Err("UNSUPPORTED_SCHEMA", "nonfinite serialized number")
            return value
        if tag == 0x10:
            return self.text(self.integer())
        if tag == 0x11:
            return ""
        if tag == 0x15:
            length = self.integer(8)
            if length > MAX_ROW_BYTES:
                raise Err("UNSUPPORTED_SCHEMA", "serialized buffer exceeds input bounds")
            return self.take(length)
        if tag == 0x16:
            # Proton uses Uint8Array views. Reject other element formats and
            # references/transfers/resizable backing stores in this subset.
            subtype, offset, length = self.integer(1), self.integer(8), self.integer(8)
            if subtype != 2 or offset > MAX_ROW_BYTES or length > MAX_ROW_BYTES or self.integer(1) != 0x15:
                raise Err("UNSUPPORTED_SCHEMA", "unsupported serialized buffer view")
            backing_length = self.integer(8)
            if backing_length > MAX_ROW_BYTES or offset+length > backing_length:
                raise Err("UNSUPPORTED_SCHEMA", "invalid serialized buffer view bounds")
            return self.take(backing_length)[offset:offset+length]
        raise Err("UNSUPPORTED_SCHEMA", "unsupported serialized value tag")


def decode_ssv(blob: bytes) -> dict:
    reader = _SSVReader(blob)
    if reader.integer() != 15:
        raise Err("UNSUPPORTED_SCHEMA", "unsupported serialized record version")
    value = reader.value()
    if not isinstance(value, dict) or reader.pos != len(blob):
        raise Err("UNSUPPORTED_SCHEMA", "invalid root object or trailing serialized data")
    return value


def _property(key: bytes, record: bytes | dict):
    obj = decode_ssv(record) if isinstance(record, bytes) else record
    return obj.get(key.decode("ascii"), _UNDEFINED)


def extract_arraybuffer_after(marker: bytes, blob: bytes | dict) -> bytes:
    value = _property(marker, blob)
    if not isinstance(value, bytes):
        raise Err("UNSUPPORTED_SCHEMA", "missing or invalid buffer property")
    return value


def extract_hb_string_after(key: bytes, blob: bytes | dict) -> str | None:
    value = _property(key, blob)
    if value is _UNDEFINED or value is None:
        return None
    if not isinstance(value, str):
        raise Err("UNSUPPORTED_SCHEMA", "invalid string property")
    return value


def extract_i32_after(key: bytes, blob: bytes | dict) -> int | None:
    value = _property(key, blob)
    if value is _UNDEFINED or value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value or not -(2**31) <= value < 2**31:
        raise Err("UNSUPPORTED_SCHEMA", "invalid integer property")
    return int(value)


def extract_number_after(key: bytes, blob: bytes | dict) -> int | float | None:
    value = _property(key, blob)
    if value is _UNDEFINED or value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 2**53-1:
        raise Err("UNSUPPORTED_SCHEMA", "invalid numeric property")
    return value


def load_rows(db: Path, store: str) -> list[bytes]:
    with contextlib.closing(_connect(db)) as con:
        stores = dict(con.execute("SELECT name, id FROM ObjectStoreInfo LIMIT 32"))
        if store not in stores:
            raise Err("UNSUPPORTED_SCHEMA", "required object store missing")
        oid = stores[store]
        maximum = MAX_ENTRIES if store == "items" else 1
        # Aggregate first, before sqlite allocates/decode any record BLOB.
        count, length = con.execute("SELECT COUNT(*), MAX(length(value)) FROM Records WHERE objectStoreID=?", (oid,)).fetchone()
        if count > maximum or (length or 0) > MAX_ROW_BYTES:
            raise Err("OUTPUT_TOO_LARGE", "vault records exceed safe input bounds")
        out = []
        for (value,) in con.execute("SELECT value FROM Records WHERE objectStoreID=? LIMIT ?", (oid, maximum+1)):
            if not isinstance(value, bytes):
                raise Err("UNSUPPORTED_SCHEMA", "vault record is not binary")
            out.append(value)
        return out


def run_bounded(argv: list[str], limit: int, timeout: float) -> tuple[int | None, bytes]:
    """Run argv (no shell) and read at most limit+1 bytes of its stdout.

    Returns (returncode, data). If the program writes more than `limit` bytes
    it is killed and returncode is None; if it doesn't finish within `timeout`
    seconds it is killed and TimeoutExpired is raised. stderr is discarded.
    """
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=safe_env())
    try:
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        buf = b""
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_READ)
            while len(buf) <= limit:
                left = deadline - time.monotonic()
                if left <= 0 or not sel.select(left):
                    raise subprocess.TimeoutExpired(argv, timeout)
                chunk = os.read(fd, limit + 1 - len(buf))
                if not chunk:
                    break
                buf += chunk
        if len(buf) > limit:
            return None, buf[: limit + 1]
        return proc.wait(timeout=max(0.1, deadline - time.monotonic())), buf
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def gdbus_call(path: str, method: str, *args: str, limit=4096) -> bytes:
    try:
        rc, out = run_bounded([GDBUS, "call", "--session", "--dest", "org.freedesktop.secrets",
                               "--object-path", path, "--method", method, "--", *args], limit, 3)
    except FileNotFoundError as exc:
        raise Err("DEPS_MISSING", "gdbus is missing (install glib2)") from exc
    except subprocess.TimeoutExpired as exc:
        raise Err("HELPER_TIMEOUT", "Secret Service did not answer") from exc
    if rc != 0:
        raise Err("KEYRING_LOCKED", "Secret Service is unavailable or locked")
    return out


def keyring_item(adapter: str) -> str:
    out = gdbus_call("/org/freedesktop/secrets", "org.freedesktop.Secret.Service.SearchItems",
                    "{'service': '" + SERVICE + "', 'username': '" + adapter + "'}")
    # Parse only two arrays of validated object paths, never evaluate GVariant.
    match = re.fullmatch(rb"\(\s*(?:@ao )?\[([^\]]*)\],\s*(?:@ao )?\[([^\]]*)\]\s*\)\s*", out)
    if not match:
        raise Err("UNSUPPORTED_SCHEMA", "unexpected Secret Service search reply")
    path_pattern = rb"(?:objectpath )?'(/[A-Za-z0-9_/]+)'"
    def paths(group):
        residue = re.sub(path_pattern, b"", group).replace(b",", b"").strip()
        if residue:
            raise Err("UNSUPPORTED_SCHEMA", "invalid Secret Service object paths")
        return [value.decode("ascii") for value in re.findall(path_pattern, group)]
    unlocked, locked = paths(match[1]), paths(match[2])
    if locked:
        raise Err("KEYRING_LOCKED", "unlock the session keyring to read codes")
    if len(unlocked) != 1:
        raise Err("KEY_MISSING", "a unique Proton keyring item was not found")
    return unlocked[0]


class _GList(ctypes.Structure):
    pass


_GList._fields_ = [("data", ctypes.c_void_p), ("next", ctypes.POINTER(_GList)), ("prev", ctypes.POINTER(_GList))]


def _libsecret_read(adapter: str) -> bytes:
    """One-process libsecret session. Search/load only; never request Unlock.

    Runs in a hardened child so parent can enforce a hard timeout even while
    the synchronous C functions are waiting for the service. Validate length
    before copying raw bytes, including NUL/newline, into Python.
    """
    secret = ctypes.CDLL("libsecret-1.so.0")
    glib = ctypes.CDLL("libglib-2.0.so.0")
    gobject = ctypes.CDLL("libgobject-2.0.so.0")
    ptr, errptr = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    def bind(library, name, result, *arguments):
        function = getattr(library, name)
        function.restype, function.argtypes = result, list(arguments)
        return function
    service_get = bind(secret, "secret_service_get_sync", ptr, ctypes.c_int, ptr, errptr)
    search = bind(secret, "secret_service_search_sync", ctypes.POINTER(_GList), ptr, ptr, ptr, ctypes.c_int, ptr, errptr)
    get_locked = bind(secret, "secret_item_get_locked", ctypes.c_int, ptr)
    get_secret = bind(secret, "secret_item_get_secret", ptr, ptr)
    get_bytes = bind(secret, "secret_value_get", ptr, ptr, ctypes.POINTER(ctypes.c_size_t))
    value_unref = bind(secret, "secret_value_unref", None, ptr)
    obj_unref = bind(gobject, "g_object_unref", None, ptr)
    hash_new = bind(glib, "g_hash_table_new", ptr, ptr, ptr)
    hash_insert = bind(glib, "g_hash_table_insert", ctypes.c_int, ptr, ptr, ptr)
    hash_unref = bind(glib, "g_hash_table_unref", None, ptr)
    list_free = bind(glib, "g_list_free", None, ctypes.POINTER(_GList))
    error_free = bind(glib, "g_error_free", None, ptr)
    error, service, attributes, matches, value = ptr(), None, None, None, None
    try:
        service = service_get(0, None, ctypes.byref(error))
        if error.value or not service:
            raise Err("KEYRING_LOCKED", "Secret Service is unavailable")
        attributes = hash_new(ctypes.cast(glib.g_str_hash, ptr), ctypes.cast(glib.g_str_equal, ptr))
        if not attributes:
            raise Err("KEYRING_LOCKED", "cannot create key lookup")
        # g_hash_table_new does not own these buffers; retain them until unref.
        buffers = [ctypes.create_string_buffer(raw) for raw in
                   (b"service", SERVICE.encode(), b"username", adapter.encode())]
        hash_insert(attributes, buffers[0], buffers[1])
        hash_insert(attributes, buffers[2], buffers[3])
        # SECRET_SEARCH_ALL=2, LOAD_SECRETS=8; no SECRET_SEARCH_UNLOCK=4.
        matches = search(service, None, attributes, 2 | 8, None, ctypes.byref(error))
        if error.value or not matches or matches.contents.next:
            raise Err("KEYRING_LOCKED", "a unique unlocked Proton key was not found")
        item = matches.contents.data
        if get_locked(item):
            raise Err("KEYRING_LOCKED", "unlock the session keyring")
        value = get_secret(item)
        if not value:
            raise Err("KEYRING_LOCKED", "key is locked or unavailable")
        length = ctypes.c_size_t()
        data = get_bytes(value, ctypes.byref(length))
        if not data or length.value != 32:
            raise Err("DECRYPT_FAILED", "unexpected key length")
        return ctypes.string_at(data, length.value)
    finally:
        if value:
            value_unref(value)
        if matches:
            node = matches
            while node:
                obj_unref(node.contents.data)
                node = node.contents.next
            list_free(matches)
        if attributes:
            hash_unref(attributes)
        if service:
            obj_unref(service)
        if error.value:
            error_free(error)


def read_keyring_no_prompt(adapter: str) -> bytes:
    keyring_item(adapter)  # Bounded no-prompt locked/ambiguous-item preflight.
    worker = ("import sys; sys.path.insert(0,sys.argv[1]); import protonauth_list as h; "
              "h.harden(); sys.stdout.buffer.write(h._libsecret_read(sys.argv[2]))")
    try:
        rc, raw = run_bounded(["/usr/bin/python3", "-E", "-s", "-c", worker,
                               str(Path(__file__).resolve().parent), adapter], SECRET_MAX_BYTES, 5)
    except subprocess.TimeoutExpired as exc:
        raise Err("HELPER_TIMEOUT", "keyring read did not answer") from exc
    if rc != 0:
        raise Err("KEYRING_LOCKED", "keyring read failed; unlock the session keyring and retry")
    if len(raw) != 32:
        raise Err("DECRYPT_FAILED", "unexpected key length")
    return raw


def read_keyring(adapter_key_id: str) -> bytes:
    # Comes from the vault; refuse anything that could be read as an option.
    if not ADAPTER_ID_RE.fullmatch(adapter_key_id):
        raise Err("UNSUPPORTED_SCHEMA", "unexpected adapterKeyId format")
    if not ALLOW_PROMPT:
        return read_keyring_no_prompt(adapter_key_id)
    try:
        rc, secret = run_bounded(  # fixed argv, no shell
            [SECRET_TOOL, "lookup", "service", SERVICE, "username", adapter_key_id],
            SECRET_MAX_BYTES, SECRET_TOOL_TIMEOUT_SEC,
        )
    except FileNotFoundError as e:
        raise Err("DEPS_MISSING", "secret-tool not found (install package: libsecret)") from e
    except subprocess.TimeoutExpired as e:
        raise Err("HELPER_TIMEOUT", "secret-tool did not answer (keyring prompt open?)") from e
    if rc is None:
        raise Err("DECRYPT_FAILED", "unexpected key length (too long)")
    if rc != 0:
        raise Err("KEY_MISSING", f"no Secret Service item for {adapter_key_id}")
    if not secret:
        raise Err("KEYRING_LOCKED", "empty secret — unlock the session keyring")
    if len(secret) != 32:
        raise Err("DECRYPT_FAILED", f"unexpected key length {len(secret)}")
    return secret


def storage_ref(sk: bytes) -> tuple[bytes, str]:
    sk = decode_ssv(sk)
    source = extract_i32_after(b"source", sk)
    if source == 1:
        raise Err("APP_LOCKED", "unlock Proton Authenticator to use the keyring-backed vault")
    if source == 2:
        raise Err("UNSAFE_KEY_STORAGE", "fallback storage keys are not supported")
    if source != 0:
        raise Err("UNSUPPORTED_SCHEMA", "unrecognized storage key source")
    salt = extract_arraybuffer_after(b"salt", sk)
    adapter = extract_hb_string_after(b"adapterKeyId", sk) or extract_hb_string_after(b"id", sk)
    if len(salt) != 32 or not isinstance(adapter, str) or not ADAPTER_ID_RE.fullmatch(adapter):
        raise Err("UNSUPPORTED_SCHEMA", "invalid storage key metadata")
    return salt, adapter


def otp_fields(obj: dict, now: int) -> dict:
    """Interpret supported full Proton items. Never guess an unknown format."""
    result = dict.fromkeys(HIDDEN_FIELDS)
    result["kind"] = "unsupported"
    try:
        kind = obj.get("entryType")
        if kind not in ("Totp", "Steam"):
            return result
        seed = obj.get("secret")
        uri = obj.get("uri", "")
        if not isinstance(seed, str) or not isinstance(uri, str):
            return result
        parsed = urlparse(uri)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=32) if uri else {}
        if any(len(values) != 1 for values in query.values()):
            return result
        params = {key.lower(): values[0] for key, values in query.items()}
        if kind == "Totp" and uri and (parsed.scheme != "otpauth" or parsed.netloc.lower() != "totp"):
            return result
        if kind == "Steam" and uri and parsed.scheme not in ("steam", "otpauth"):
            return result
        uri_seed = params.get("secret")
        if uri_seed and seed and uri_seed.rstrip("=").upper() != seed.rstrip("=").upper():
            return result
        seed = seed or uri_seed
        if not isinstance(seed, str) or len(seed) > 1024 or not re.fullmatch(r"[A-Za-z2-7]+=*", seed):
            return result
        decoded = base64.b32decode(seed.upper() + "=" * ((-len(seed)) % 8))
        if not decoded:
            return result
        period = obj.get("period", 30)
        if isinstance(period, bool) or not isinstance(period, int) or not 1 <= period <= 86400:
            return result
        if "period" in params and int(params["period"]) != period:
            return result
        if kind == "Steam":
            if period != 30:
                return result
            digits, digest = 5, hashlib.sha1
        else:
            algorithm = params.get("algorithm", "SHA1").upper()
            digest = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}.get(algorithm)
            if digest is None:
                return result
            if "digits" in params and "digitcount" in params and params["digits"] != params["digitcount"]:
                return result
            digits = int(params.get("digits", params.get("digitcount", obj.get("digits", 6))))
            if digits not in (6, 7, 8):
                return result
        def at(timestamp):
            mac = hmac.new(decoded, struct.pack(">Q", timestamp // period), digest).digest()
            offset = mac[-1] & 15
            value = struct.unpack_from(">I", mac, offset)[0] & 0x7fffffff
            if kind == "Steam":
                alphabet = "23456789BCDFGHJKMNPQRTVWXY"
                code = ""
                for _ in range(5):
                    code += alphabet[value % len(alphabet)]
                    value //= len(alphabet)
                return code
            return str(value % 10**digits).zfill(digits)
        return {"kind": kind.lower(), "code": at(now), "nextCode": at(now+period), "period": period,
                "digits": digits, "remaining": period - now % period}
    except (ValueError, TypeError, binascii.Error, OverflowError, struct.error):
        return result


def success(entries=None, now=None, **extra) -> dict:
    now = int(time.time()) if now is None else now
    return {"ok": True, "entries": [] if entries is None else entries, "now": now,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "error": None, **extra}


def list_entries(private: bool = False, reveal: str | None = None, only: str | None = None) -> dict:
    source_db = find_idb()
    mtime = int(source_db.stat().st_mtime)
    with vault_snapshot(source_db) as idb:
        sk_rows = load_rows(idb, "storageKey")
        if not sk_rows:
            raise Err("VAULT_UNREADABLE", "storageKey store empty")
        salt, adapter = storage_ref(sk_rows[0])
        secret = read_keyring(adapter)
        aes_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=HKDF_INFO).derive(secret)
        aesgcm = AESGCM(aes_key)
        entries, seen, pending = [], set(), []
        for vb in load_rows(idb, "items"):
            vb = decode_ssv(vb)
            entry_id = extract_hb_string_after(b"id", vb)
            if not isinstance(entry_id, str) or not ID_RE.fullmatch(entry_id) or entry_id in seen:
                raise Err("UNSUPPORTED_SCHEMA", "invalid or duplicate item id")
            seen.add(entry_id)
            ct = extract_arraybuffer_after(b"__encryptedData", vb)
            obj = None
            for ivlen in (12, 16):
                try:
                    pt = aesgcm.decrypt(ct[:ivlen], ct[ivlen:], ITEM_AAD)
                except (InvalidTag, ValueError):
                    continue
                try:
                    obj = json.loads(pt)
                except (ValueError, UnicodeError) as exc:
                    raise Err("UNSUPPORTED_SCHEMA", "invalid encrypted item JSON") from exc
                break
            if obj is None:
                raise Err("DECRYPT_FAILED", "item authentication failed")
            if not isinstance(obj, dict) or ("id" in obj and obj["id"] != entry_id):
                raise Err("UNSUPPORTED_SCHEMA", "invalid encrypted item object")
            issuer = extract_hb_string_after(b"issuer", vb) or obj.get("issuer") or ""
            account = extract_hb_string_after(b"name", vb) or obj.get("name") or ""
            if not isinstance(issuer, str) or not isinstance(account, str):
                raise Err("UNSUPPORTED_SCHEMA", "invalid item labels")
            entry = {"id": entry_id, "issuer": issuer[:MAX_LABEL_CHARS], "account": account[:MAX_LABEL_CHARS],
                     "order": extract_number_after(b"order", vb) or 0, **dict.fromkeys(HIDDEN_FIELDS)}
            if only is None or only == entry_id:
                entries.append(entry)
                if not private or reveal == entry_id:
                    # Retain only fields used to generate the deliberately
                    # visible OTP; notes/other decrypted metadata stay local
                    # to this iteration. No seed enters the response object.
                    config = {key: obj[key] for key in ("entryType", "secret", "uri", "period", "digits") if key in obj}
                    pending.append((entry, config))
    # Authentication, label/ID/order validation and private snapshot cleanup
    # may take seconds. Generate codes only after all of that work completes.
    if only is not None and not entries:
        raise Err("ENTRY_MISSING", "selected account is no longer in the vault")
    entries.sort(key=lambda e: (-e["order"], e["issuer"].lower(), e["account"].lower()))
    now = int(time.time())
    for entry, config in pending:
        entry.update(otp_fields(config, now))
    return success(entries, now, vaultMtime=mtime)


@contextlib.contextmanager
def prefs_directory(create=False):
    directory = state_dir()
    try:
        if create:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not create:
            yield None
            return
        raise Err("PREFS_INVALID", "cannot create preferences directory") from None
    except OSError as exc:
        raise Err("PREFS_INVALID", "unsafe preferences directory") from exc
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise Err("PREFS_INVALID", "preferences directory belongs to another user")
        if create:
            os.fchmod(fd, 0o700)
        yield fd
    finally:
        os.close(fd)


def normalize_prefs(value) -> dict:
    if not isinstance(value, dict):
        raise Err("PREFS_INVALID", "preferences must be a JSON object")
    def ids(key, maximum):
        values = value.get(key, [])
        if not isinstance(values, list) or len(values) > MAX_ENTRIES:
            raise Err("PREFS_INVALID", "invalid account ID list")
        out = []
        for entry_id in values:
            if not isinstance(entry_id, str) or not ID_RE.fullmatch(entry_id):
                raise Err("PREFS_INVALID", "invalid account ID")
            if entry_id not in out and len(out) < maximum:
                out.append(entry_id)
        return out
    privacy = value.get("privacy", True)
    if not isinstance(privacy, bool):
        raise Err("PREFS_INVALID", "privacy must be a boolean")
    return {"privacy": privacy, "pinned": ids("pinned", 100), "recent": ids("recent", 5)}


def _prefs_read(fd) -> dict:
    if fd is None:
        return normalize_prefs({})
    try:
        pfd = os.open("prefs.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return normalize_prefs({})
    except OSError as exc:
        raise Err("PREFS_INVALID", "unsafe preferences file") from exc
    with os.fdopen(pfd, "rb") as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > MAX_PREFS_BYTES:
            raise Err("PREFS_INVALID", "unsafe or oversized preferences file")
        raw = file.read(MAX_PREFS_BYTES + 1)
    if len(raw) > MAX_PREFS_BYTES:
        raise Err("PREFS_INVALID", "preferences exceed safe input bounds")
    try:
        return normalize_prefs(json.loads(raw))
    except (ValueError, UnicodeError) as exc:
        raise Err("PREFS_INVALID", "invalid preferences JSON") from exc


def prefs_get() -> dict:
    with prefs_directory() as fd:
        return _prefs_read(fd)


def prefs_set(value: dict) -> dict:
    value = normalize_prefs(value)
    raw = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    if len(raw) > MAX_PREFS_BYTES:
        raise Err("PREFS_INVALID", "preferences exceed safe input bounds")
    with prefs_directory(create=True) as fd:
        _prefs_read(fd)  # Refuse existing links/devices before atomic replacement.
        name = ".prefs-" + secrets.token_hex(8)
        try:
            pfd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(pfd, "wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, "prefs.json", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=fd)
    return value


def clipboard_copy(code: str, clear_after: int) -> None:
    script = Path(__file__).with_name("protonauth_clipboard.py")
    proc = subprocess.Popen(["/usr/bin/python3", "-E", "-s", str(script), str(clear_after)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=True, env=safe_env())
    acknowledged = False
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(code.encode("ascii"))
        proc.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            if not selector.select(3):
                raise Err("CLIPBOARD_FAILED", "clipboard owner did not acknowledge startup")
            ack = os.read(proc.stdout.fileno(), 64)
        if ack != b"ready\n":
            raise Err("CLIPBOARD_FAILED", "clipboard owner could not start")
        acknowledged = True
    except (OSError, ValueError) as exc:
        raise Err("CLIPBOARD_FAILED", "clipboard owner could not start") from exc
    finally:
        if not acknowledged:
            # The child has a private session; never target another owner.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=1)
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        if proc.stdout:
            proc.stdout.close()
    # Do not wait: the detached owner deliberately lives past this helper.


def copy_entry(entry_id: str, clear_after: int = 20, demo_mode=False) -> dict:
    if type(clear_after) is not int or not 1 <= clear_after <= 600:
        raise Err("INVALID_ARGUMENT", "clipboard expiry must be between 1 and 600 seconds")
    payload = demo() if demo_mode else list_entries(only=entry_id)
    entry = next((entry for entry in payload["entries"] if entry["id"] == entry_id), None)
    if entry is None:
        raise Err("ENTRY_MISSING", "selected account is no longer in the vault")
    if entry["kind"] not in ("totp", "steam") or not entry["code"]:
        raise Err("UNSUPPORTED_ENTRY", "this account format cannot be copied")
    used_next = entry["remaining"] <= 5
    clipboard_copy(entry["nextCode"] if used_next else entry["code"], clear_after)
    return success(copy={"id": entry_id, "usedNext": used_next, "clearAfter": clear_after})


def fail(code: str, message: str) -> dict:
    return {
        "ok": False,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "now": int(time.time()),
        "entries": [],
        "error": {"code": code, "message": str(message)[:MAX_MESSAGE_CHARS]},
    }


def check() -> dict:
    """Diagnose dependencies and key access without decrypting items/codes."""
    idb = find_idb()
    with vault_snapshot(idb) as tmp:
        sk_rows = load_rows(tmp, "storageKey")
        if not sk_rows:
            raise Err("VAULT_UNREADABLE", "storageKey store empty")
        _salt, adapter = storage_ref(sk_rows[0])
        read_keyring(adapter)
        count = len(load_rows(tmp, "items"))
    import cryptography
    return success(vaultMtime=int(idb.stat().st_mtime), check={"items": count, "keyring": "unlocked",
                   "version": __version__, "python": sys.version.split()[0], "cryptography": cryptography.__version__})


# Fake accounts for screenshots/demos. The secrets are well-known public test
# values (RFC 4226/6238 style), not anyone's real seeds.
DEMO_ACCOUNTS = [
    ("GitHub", "octocat@example.com", "JBSWY3DPEHPK3PXP", 30, 6),
    ("Proton", "demo@proton.me", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 30, 6),
    ("Google", "jane.doe@example.com", "KRSXG5CTMVRXEZLUKN2XAZLSKNSWG4TFOQ", 30, 6),
    ("AWS", "root@example.org", "MFRGGZDFMZTWQ2LKNNWG23TPOBYXE43U", 30, 6),
    ("Discord", "jane#0001", "ONSWG4TFORZWK3DUMVZXG5DFON2A", 30, 6),
    ("Tailscale", "jane@example.net", "OBQXG43XN5ZGIZLYMFWXA3DF", 30, 8),
]


def demo(private=False, reveal=None, only=None) -> dict:
    now = int(time.time())
    entries = []
    for i, (issuer, account, seed, period, digits) in enumerate(DEMO_ACCOUNTS):
        entry_id = f"demo-{i}"
        fields = (dict.fromkeys(HIDDEN_FIELDS) if private and reveal != entry_id else
                  otp_fields({"entryType": "Totp", "secret": seed, "period": period, "digits": digits}, now))
        if only is None or only == entry_id:
            entries.append({"id": entry_id, "issuer": issuer, "account": account, "order": 0, **fields})
    if only is not None and not entries:
        raise Err("ENTRY_MISSING", "selected demo account is missing")
    return success(entries, now, demo=True)


def _exit_on_signal(signum, _frame):
    # Turn SIGTERM/SIGHUP/SIGINT into SystemExit so every `finally` (temp dir
    # removal) still runs when omarchy-shell stops the helper.
    raise SystemExit(128 + signum)


def _on_alarm(_signum, _frame):
    # Whole-run deadline: unwinds like any other error (finally blocks run,
    # a running secret-tool is killed) and is reported as JSON.
    raise Err("HELPER_TIMEOUT", f"helper took longer than {HELPER_TIMEOUT_SEC} s")


def emit(payload: dict) -> None:
    """Print payload as one JSON line, never more than MAX_OUTPUT_BYTES."""
    out = json.dumps(payload, separators=(",", ":"))
    if len(out.encode("utf-8")) > MAX_OUTPUT_BYTES:
        out = json.dumps(fail("OUTPUT_TOO_LARGE", f"output over {MAX_OUTPUT_BYTES} bytes"),
                         separators=(",", ":"))
    print(out)


def main() -> int:
    global ALLOW_PROMPT
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, _exit_on_signal)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(HELPER_TIMEOUT_SEC)
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--prefs-get", action="store_true")
    modes.add_argument("--prefs-set", action="store_true")
    modes.add_argument("--copy", metavar="ID")
    ap.add_argument("--demo", action="store_true", help="use public fake accounts only")
    ap.add_argument("--private", action="store_true", help="hide codes except explicitly revealed account")
    ap.add_argument("--reveal", metavar="ID")
    ap.add_argument("--only", metavar="ID")
    ap.add_argument("--clear-after", type=int, default=20)
    ap.add_argument("--allow-prompt", action="store_true", help="allow an explicit keyring unlock prompt")
    ap.add_argument("--version", action="version", version=f"protonauth-list {__version__}")
    args = ap.parse_args()
    ALLOW_PROMPT = args.allow_prompt
    try:
        protections = harden()
        if any(value is not None and not ID_RE.fullmatch(value) for value in (args.reveal, args.only, args.copy)):
            raise Err("INVALID_ARGUMENT", "invalid account ID")
        if args.reveal and not args.private:
            raise Err("INVALID_ARGUMENT", "--reveal requires --private")
        if args.prefs_get:
            payload = success(prefs=prefs_get())
        elif args.prefs_set:
            raw = sys.stdin.buffer.read(MAX_PREFS_BYTES+1)
            if len(raw) > MAX_PREFS_BYTES:
                raise Err("PREFS_INVALID", "preferences exceed safe input bounds")
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise Err("PREFS_INVALID", "invalid preferences JSON") from exc
            payload = success(prefs=prefs_set(value))
        else:
            if _IMPORT_ERROR is not None:
                raise Err("DEPS_MISSING", "cryptography is missing (install python-cryptography)")
            if args.copy:
                payload = copy_entry(args.copy, args.clear_after, args.demo)
            elif args.demo:
                payload = (success(demo=True, check={"keyring": "demo", "items": len(DEMO_ACCOUNTS), "protections": protections})
                           if args.check else demo(args.private, args.reveal, args.only))
            elif args.check:
                payload = check()
                payload["check"]["protections"] = protections
            elif args.private or args.only:
                payload = list_entries(args.private, args.reveal, args.only)
            else:
                payload = list_entries()  # Keep the full-list import interface.
        emit(payload)
        return 0
    except Err as exc:
        emit(fail(exc.code, exc.message))
        return 1
    except Exception as exc:  # noqa: BLE001 - fail closed, disclose exception type only
        emit(fail("DECRYPT_FAILED", f"internal error ({type(exc).__name__})"))
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())

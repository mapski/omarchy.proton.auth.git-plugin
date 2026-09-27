#!/usr/bin/env python3
"""Emit Proton Authenticator TOTP codes from the local vault (labels+codes only)."""
from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

__version__ = "1.0.0"

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    import pyotp
except ImportError as _imp_err:  # reported as JSON by main()
    _IMPORT_ERROR: ImportError | None = _imp_err
else:
    _IMPORT_ERROR = None

DATA_DIR = Path.home() / ".local/share/me.proton.authenticator"
HKDF_INFO = b"authenticator;storage-key"
ITEM_AAD = b"authenticator;item"
SERVICE = "com.proton.authenticator"


class Err(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def find_idb() -> Path:
    root = DATA_DIR / "databases" / "indexeddb"
    if not root.exists():
        raise Err("VAULT_UNREADABLE", f"missing IndexedDB under {root}")
    candidates: list[Path] = []
    for p in root.rglob("IndexedDB.sqlite3"):
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            con.create_collation("IDBKEY", lambda a, b: (a > b) - (a < b))
            names = {r[0] for r in con.execute("SELECT name FROM ObjectStoreInfo")}
            con.close()
            if {"items", "storageKey"} <= names:
                candidates.append(p)
        except Exception:
            continue
    if not candidates:
        raise Err("VAULT_UNREADABLE", "no IndexedDB with items/storageKey stores")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def backup_db(src: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="protonauth-idb-"))
    tmp = tmpdir / "idb.sqlite3"
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_con = sqlite3.connect(tmp)
    src_con.backup(dst_con)
    dst_con.close()
    src_con.close()
    return tmp


def extract_arraybuffer_after(marker: bytes, blob: bytes) -> bytes:
    i = blob.find(marker)
    if i < 0:
        raise Err("UNSUPPORTED_SCHEMA", f"missing marker {marker!r}")
    j = i + len(marker)
    if j >= len(blob) or blob[j] != 0x16:
        raise Err("UNSUPPORTED_SCHEMA", "expected ArrayBuffer tag after marker")
    j += 1
    if blob[j] != 0x02:
        raise Err("UNSUPPORTED_SCHEMA", "unexpected ArrayBuffer header")
    j += 1
    a, b = struct.unpack_from("<QQ", blob, j)
    j += 16
    if a != 0 or blob[j] != 0x15:
        raise Err("UNSUPPORTED_SCHEMA", "unexpected ArrayBuffer view header")
    j += 1
    (c,) = struct.unpack_from("<Q", blob, j)
    j += 8
    if c != b:
        raise Err("UNSUPPORTED_SCHEMA", "ArrayBuffer length mismatch")
    return blob[j : j + b]


def extract_hb_string_after(key: bytes, blob: bytes) -> str | None:
    pat = struct.pack("<I", 0x80000000 | len(key)) + key
    i = blob.find(pat)
    if i < 0:
        return None
    j = i + len(pat)
    if blob[j] != 0x10:
        return None
    j += 1
    (raw,) = struct.unpack_from("<I", blob, j)
    j += 4
    if raw in (0xFFFFFFFE, 0xFFFFFFFF):
        return ""
    if not (raw & 0x80000000):
        return None
    n = raw & 0xFFFFFF
    return blob[j : j + n].decode("utf-8")


def extract_i32_after(key: bytes, blob: bytes) -> int | None:
    pat = struct.pack("<I", 0x80000000 | len(key)) + key
    i = blob.find(pat)
    if i < 0:
        return None
    j = i + len(pat)
    if blob[j] != 0x05:
        return None
    j += 1
    return struct.unpack_from("<i", blob, j)[0]


def load_rows(db: Path, store: str) -> list[bytes]:
    con = sqlite3.connect(db)
    con.create_collation("IDBKEY", lambda a, b: (a > b) - (a < b))
    oid = dict(con.execute("SELECT name, id FROM ObjectStoreInfo"))[store]
    out: list[bytes] = []
    for (val,) in con.execute("SELECT value FROM Records WHERE objectStoreID=?", (oid,)):
        out.append(val if isinstance(val, bytes) else val.encode("latin1"))
    con.close()
    return out


def read_keyring(adapter_key_id: str) -> bytes:
    try:
        secret = subprocess.check_output(
            ["secret-tool", "lookup", "service", SERVICE, "username", adapter_key_id],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        raise Err("KEY_MISSING", f"no Secret Service item for {adapter_key_id}") from e
    if not secret:
        raise Err("KEYRING_LOCKED", "empty secret — unlock the session keyring")
    if len(secret) != 32:
        raise Err("DECRYPT_FAILED", f"unexpected key length {len(secret)}")
    return secret


def list_entries() -> dict:
    idb = backup_db(find_idb())
    try:
        sk_rows = load_rows(idb, "storageKey")
        if not sk_rows:
            raise Err("VAULT_UNREADABLE", "storageKey store empty")
        sk = sk_rows[0]
        salt = extract_arraybuffer_after(b"salt", sk)
        adapter = extract_hb_string_after(b"adapterKeyId", sk)
        if not adapter:
            raise Err("UNSUPPORTED_SCHEMA", "adapterKeyId missing")
        secret = read_keyring(adapter)
        aes_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=HKDF_INFO,
        ).derive(secret)
        aesgcm = AESGCM(aes_key)

        now = int(time.time())
        entries: list[dict] = []
        for vb in load_rows(idb, "items"):
            issuer = extract_hb_string_after(b"issuer", vb) or ""
            name = extract_hb_string_after(b"name", vb)
            if name is None:
                name = ""
            order = extract_i32_after(b"order", vb)
            entry_id = extract_hb_string_after(b"id", vb) or ""
            ct = extract_arraybuffer_after(b"__encryptedData", vb)
            obj = None
            for ivlen in (12, 16):
                try:
                    pt = aesgcm.decrypt(ct[:ivlen], ct[ivlen:], ITEM_AAD)
                    obj = json.loads(pt.decode())
                    break
                except Exception:
                    continue
            if obj is None:
                raise Err("DECRYPT_FAILED", f"AES-GCM failed for {issuer or entry_id}")

            seed = obj.get("secret")
            if not seed and obj.get("uri"):
                seed = pyotp.parse_uri(obj["uri"]).secret
            if not seed:
                raise Err("DECRYPT_FAILED", f"no secret for {issuer or entry_id}")
            period = int(obj.get("period") or 30)
            digits = int(obj.get("digits") or 6)
            totp = pyotp.TOTP(seed, digits=digits, interval=period)
            entries.append(
                {
                    "id": entry_id,
                    "issuer": issuer or obj.get("issuer") or "",
                    "account": name if name != "" else (obj.get("name") or ""),
                    "period": period,
                    "digits": digits,
                    "code": totp.at(now),
                    "nextCode": totp.at(now + period),
                    "remaining": period - (now % period),
                    "order": order if order is not None else 0,
                }
            )
        entries.sort(key=lambda e: (-e["order"], e["issuer"].lower(), e["account"].lower()))
        return {
            "ok": True,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "entries": entries,
            "error": None,
        }
    finally:
        try:
            idb.unlink(missing_ok=True)
            idb.parent.rmdir()
        except Exception:
            pass


def fail(code: str, message: str) -> dict:
    return {
        "ok": False,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entries": [],
        "error": {"code": code, "message": message},
    }


def check() -> dict:
    """Diagnose the setup without decrypting anything or printing codes."""
    import shutil

    if shutil.which("secret-tool") is None:
        raise Err("DEPS_MISSING", "secret-tool not found (install package: libsecret)")
    idb = find_idb()
    tmp = backup_db(idb)
    try:
        sk_rows = load_rows(tmp, "storageKey")
        if not sk_rows:
            raise Err("VAULT_UNREADABLE", "storageKey store empty")
        adapter = extract_hb_string_after(b"adapterKeyId", sk_rows[0])
        if not adapter:
            raise Err("UNSUPPORTED_SCHEMA", "adapterKeyId missing")
        read_keyring(adapter)
        items = len(load_rows(tmp, "items"))
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except Exception:
            pass
    return {
        "ok": True,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entries": [],
        "error": None,
        "check": {"vault": str(idb), "items": items, "keyring": "unlocked"},
    }


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


def demo() -> dict:
    now = int(time.time())
    entries = []
    for i, (issuer, account, seed, period, digits) in enumerate(DEMO_ACCOUNTS):
        totp = pyotp.TOTP(seed, digits=digits, interval=period)
        entries.append({
            "id": f"demo-{i}", "issuer": issuer, "account": account,
            "period": period, "digits": digits,
            "code": totp.at(now), "nextCode": totp.at(now + period),
            "remaining": period - (now % period), "order": 0,
        })
    return {"ok": True, "demo": True,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "entries": entries, "error": None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify dependencies, vault and keyring access; prints no codes")
    ap.add_argument("--demo", action="store_true",
                    help="print fake demo accounts (for screenshots); never touches the vault")
    ap.add_argument("--version", action="version", version=f"protonauth-list {__version__}")
    args = ap.parse_args()
    if _IMPORT_ERROR is not None:
        print(json.dumps(fail("DEPS_MISSING",
                              f"{_IMPORT_ERROR} (install: python-cryptography python-pyotp)"),
                         separators=(",", ":")))
        return 1
    try:
        if args.demo:
            print(json.dumps(demo(), separators=(",", ":")))
            return 0
        if args.check:
            print(json.dumps(check(), separators=(",", ":")))
            return 0
        payload = list_entries()
        for e in payload["entries"]:
            if "secret" in e:
                raise Err("DECRYPT_FAILED", "refusing to emit secret field")
        print(json.dumps(payload, separators=(",", ":")))
        return 0
    except Err as e:
        print(json.dumps(fail(e.code, e.message), separators=(",", ":")))
        return 1
    except Exception as e:
        print(json.dumps(fail("DECRYPT_FAILED", str(e)), separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(main())

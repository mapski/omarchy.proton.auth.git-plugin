"""Synthetic vaults and public RFC seeds only; never access user services."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import protonauth_list as h

ROOT = b'\x11' * 32
SALT = b'\x22' * 32
IV = b'\x33' * 12
SEED = base64.b32encode(b'12345678901234567890').decode()

def text(value, utf16=False):
    raw = value.encode('utf-16-le') if utf16 else value.encode('latin1')
    head = len(raw)//2 if utf16 else 0x80000000 | len(raw)
    return struct.pack('<I', head) + raw

def property_bytes(name, value):
    return text(name) + value

def string(name, value, utf16=False):
    return property_bytes(name, b'\x10' + text(value, utf16))

def number(name, value):
    encoded = (b'\x06' if value == 0 else b'\x07') if type(value) is int and value in (0, 1) else b'\x05' + struct.pack('<i', value)
    return property_bytes(name, encoded)

def double(name, value):
    return property_bytes(name, b'\x0a' + struct.pack('<d', value))

def buffer(name, value):
    return property_bytes(name, b'\x16\x02' + struct.pack('<QQ', 0, len(value)) + b'\x15' + struct.pack('<Q', len(value)) + value)

def record(*properties):
    return struct.pack('<I', 15) + b'\x02' + b''.join(properties) + b'\xff'*4

def item(kind='Totp', algorithm='SHA1', digits=6, raw=b'12345678901234567890', **extra):
    seed = base64.b32encode(raw).decode()
    obj = dict(id='fixture-1', issuer='Fixture', name='test@example.invalid', entryType=kind,
               secret=seed, period=30, uri=f'otpauth://totp/Fixture:test?secret={seed}&algorithm={algorithm}&digits={digits}')
    obj.update(extra)
    return obj

@contextlib.contextmanager
def vault(objects, source=0, old_id=False, corrupt=False, order=None):
    with tempfile.TemporaryDirectory(prefix='proton-synthetic-') as temp:
        db = Path(temp) / 'fixture.sqlite3'
        con = sqlite3.connect(db)
        con.executescript('CREATE TABLE ObjectStoreInfo(id INTEGER, name TEXT); CREATE TABLE Records(objectStoreID INTEGER, value BLOB); INSERT INTO ObjectStoreInfo VALUES(1,"storageKey"),(2,"items");')
        sk = record(buffer('salt', SALT), string('id' if old_id else 'adapterKeyId', 'fixture-key'), number('source', source))
        con.execute('INSERT INTO Records VALUES(1,?)', (sk,))
        key = h.HKDF(algorithm=h.hashes.SHA256(), length=32, salt=SALT, info=h.HKDF_INFO).derive(ROOT)
        for index, obj in enumerate(objects):
            ct = IV + h.AESGCM(key).encrypt(IV, json.dumps(obj).encode(), h.ITEM_AAD)
            if corrupt and index == len(objects)-1:
                ct = ct[:-1] + bytes([ct[-1] ^ 1])
            row = record(string('id', obj['id']), buffer('__encryptedData', ct), b'' if order is None else double('order', order))
            con.execute('INSERT INTO Records VALUES(2,?)', (row,))
        con.commit()
        con.close()
        with patch.object(h, 'find_idb', return_value=db), patch.object(h, 'install_id', return_value='0'*32), patch.object(h, 'read_keyring', return_value=ROOT), patch.object(h.time, 'time', return_value=59):
            yield db

class Formats(unittest.TestCase):
    def test_rfc_algorithms_and_uri_digits(self):
        cases = [('SHA1', b'12345678901234567890', '94287082'), ('SHA256', b'12345678901234567890123456789012', '46119246'), ('SHA512', b'1234567890123456789012345678901234567890123456789012345678901234', '90693936')]
        for alg, seed, expected in cases:
            with self.subTest(algorithm=alg), vault([item(algorithm=alg, digits=8, raw=seed)]):
                entry = h.list_entries()['entries'][0]
                self.assertEqual(entry['code'], expected)
                self.assertEqual(entry['kind'], 'totp')
                self.assertEqual(entry['digits'], 8)
                self.assertEqual(entry['issuer'], 'Fixture')

    def test_uri_digitcount(self):
        obj = item()
        obj['uri'] = obj['uri'].replace('digits=6', 'digitcount=8')
        with vault([obj]):
            self.assertEqual(h.list_entries()['entries'][0]['code'], '94287082')

    def test_steam(self):
        with vault([item(kind='Steam', uri='steam://' + SEED)]):
            entry = h.list_entries()['entries'][0]
            self.assertEqual(entry['kind'], 'steam')
            self.assertEqual(entry['code'], 'PV9M4')
            self.assertEqual(entry['digits'], 5)

    def test_unsupported_formats_never_guess(self):
        cases = [item(kind='Hotp'), item(algorithm='MD5'), item(secret='!!!'), item(period=0), item(digits=4), item(uri='otpauth://hotp/Test?secret='+SEED), item(uri='not-a-uri')]
        for obj in cases:
            with self.subTest(obj=obj), vault([obj]):
                entry = h.list_entries()['entries'][0]
                self.assertEqual(entry['kind'], 'unsupported')
                self.assertIsNone(entry['code'])
                self.assertIsNone(entry['nextCode'])

    def test_bad_ciphertext_fails_whole_request(self):
        with vault([item(), item(id='fixture-2')], corrupt=True):
            with self.assertRaises(h.Err) as exc:
                h.list_entries()
            self.assertEqual(exc.exception.code, 'DECRYPT_FAILED')

    def test_private_nulls_and_only_reveals_selected(self):
        with vault([item(), item(id='fixture-2')]):
            payload = h.list_entries(private=True, reveal='fixture-2')
            self.assertEqual(payload['now'], 59)
            self.assertIsInstance(payload['vaultMtime'], int)
            entries = {e['id']: e for e in payload['entries']}
            for key in ('code', 'nextCode', 'period', 'digits', 'remaining', 'kind'):
                self.assertIsNone(entries['fixture-1'][key], key)
            self.assertEqual(entries['fixture-2']['code'], '287082')

    def test_only_selects_and_missing_fails(self):
        with vault([item(), item(id='fixture-2')]):
            self.assertEqual([e['id'] for e in h.list_entries(only='fixture-2')['entries']], ['fixture-2'])
            with self.assertRaises(h.Err):
                h.list_entries(only='missing')

    def test_duplicate_or_empty_ids_fail_closed(self):
        for objects in ([item(), item()], [item(id='')]):
            with vault(objects), self.assertRaises(h.Err):
                h.list_entries()

    def test_password_or_fallback_key_sources_fail(self):
        for source in (1, 2, -1, 100):
            with self.subTest(source=source), vault([item()], source=source), self.assertRaises(h.Err):
                h.list_entries()

    def test_old_adapter_id(self):
        with vault([item()], old_id=True):
            self.assertEqual(h.list_entries()['entries'][0]['code'], '287082')

    def test_truncated_serialized_fields_are_typed_failures(self):
        for blob in (b'salt', b'salt\x16', b'salt\x16\x02' + bytes(7)):
            with self.subTest(blob=blob), self.assertRaises(h.Err):
                h.extract_arraybuffer_after(b'salt', blob)

    def test_row_count_bound_before_materialization(self):
        with vault([item(), item(id='fixture-2')]) as db, patch.object(h, 'MAX_ENTRIES', 1), self.assertRaises(h.Err) as exc:
            h.load_rows(db, 'items')
        self.assertEqual(exc.exception.code, 'OUTPUT_TOO_LARGE')

class Preferences(unittest.TestCase):
    def test_defaults_and_id_only_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_STATE_HOME': temp}):
            self.assertTrue(h.prefs_get()['privacy'])
            saved = h.prefs_set({'privacy': False, 'pinned': ['a', 'a', 'b'], 'recent': ['a','b','c','d','e','f'], 'secret': SEED})
            self.assertEqual(saved, {'privacy': False, 'pinned': ['a','b'], 'recent': ['a','b','c','d','e']})
            self.assertEqual(h.prefs_get(), saved)
            p = h.state_dir() / 'prefs.json'
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
            self.assertNotIn(SEED, p.read_text())

    def test_symlink_state_directory_and_file_are_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_STATE_HOME': temp}):
            outside = Path(temp) / 'outside'
            outside.mkdir()
            h.state_dir().symlink_to(outside, target_is_directory=True)
            with self.assertRaises(h.Err):
                h.prefs_set({'privacy': False})
            h.state_dir().unlink()
            h.state_dir().mkdir()
            (h.state_dir() / 'prefs.json').symlink_to(outside / 'target')
            with self.assertRaises(h.Err):
                h.prefs_get()
            with self.assertRaises(h.Err):
                h.prefs_set({'privacy': False})
            self.assertFalse((outside / 'target').exists())

    def test_oversized_preferences_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_STATE_HOME': temp}):
            h.state_dir().mkdir()
            (h.state_dir() / 'prefs.json').write_bytes(b' '*20000)
            with self.assertRaises(h.Err):
                h.prefs_get()

class Copy(unittest.TestCase):
    def test_copy_recomputes_uses_next_at_five_seconds_and_returns_no_code(self):
        for now, expected, used_next in ((54, '287082', False), (55, '359152', True), (59, '359152', True)):
            with vault([item()]), patch.object(h.time, 'time', return_value=now), patch.object(h, 'clipboard_copy') as sink:
                result = h.copy_entry('fixture-1', clear_after=12)
                sink.assert_called_once_with(expected, 12)
                self.assertEqual(result['copy']['usedNext'], used_next)
                self.assertEqual(result['copy']['id'], 'fixture-1')
                self.assertEqual(result['entries'], [])
                self.assertNotIn(expected, json.dumps(result))

    def test_copy_uses_clock_after_all_rows_are_authenticated(self):
        clock = {'now': 54, 'items': 0}
        original_decode = h.decode_ssv
        def delayed_decode(blob):
            result = original_decode(blob)
            if '__encryptedData' in result:
                clock['items'] += 1
                if clock['items'] == 2:
                    clock['now'] = 61
            return result
        with vault([item(), item(id='fixture-2')]), patch.object(h.time, 'time', side_effect=lambda: clock['now']), patch.object(h, 'decode_ssv', side_effect=delayed_decode), patch.object(h, 'clipboard_copy') as sink:
            result = h.copy_entry('fixture-1', clear_after=12)
        # Public RFC code for step2, generated after the delayed second row.
        sink.assert_called_once_with('359152', 12)
        self.assertFalse(result['copy']['usedNext'])
        self.assertEqual(result['entries'], [])
        self.assertNotIn('359152', json.dumps(result))

    def test_unsupported_copy_does_not_write(self):
        with vault([item(kind='Hotp')]), patch.object(h, 'clipboard_copy') as sink, self.assertRaises(h.Err):
            h.copy_entry('fixture-1', clear_after=12)
        sink.assert_not_called()


class Keyring(unittest.TestCase):
    def test_no_prompt_reads_bounded_worker_without_unlocking(self):
        calls = []
        def fake(argv, limit, timeout):
            calls.append(argv)
            if argv[0] == '/usr/bin/gdbus':
                return 0, b"([objectpath '/org/freedesktop/secrets/item/1'], @ao [])\n"
            self.assertEqual(argv[0], '/usr/bin/python3')
            self.assertIn('_libsecret_read', argv[4])
            self.assertEqual(limit, 64)
            return 0, ROOT
        with patch.object(h, 'run_bounded', side_effect=fake), patch.object(h, 'ALLOW_PROMPT', False):
            self.assertEqual(h.read_keyring('fixture-key'), ROOT)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(arg in ('Unlock', '--unlock', 'lookup') for command in calls for arg in command))

    def test_locked_or_ambiguous_keyring_never_fetches_secret(self):
        for data in (b"(@ao [], [objectpath '/locked'])", b"(@ao [], @ao [])", b"([objectpath '/a', '/b'], @ao [])", b'bad result'):
            calls = []
            def fake(argv, limit, timeout):
                calls.append(argv)
                return 0, data
            with patch.object(h, 'run_bounded', side_effect=fake), patch.object(h, 'ALLOW_PROMPT', False), self.assertRaises(h.Err):
                h.read_keyring('fixture-key')
            self.assertEqual(len(calls), 1)

class Cli(unittest.TestCase):
    def test_demo_check_has_no_codes_or_vault_access(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'HOME': temp, 'XDG_STATE_HOME': temp}
            proc = subprocess.run([str(BIN/'protonauth-list'), '--demo', '--check'], env=env, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['entries'], [])
        self.assertEqual(result['check']['keyring'], 'demo')

class LibsecretBoundary(unittest.TestCase):
    """Fake only external C service calls; real GLib hash-table boundary."""
    @contextlib.contextmanager
    def backend(self, raw=b'\x00\n'+b'x'*30, locked=False, duplicate=False, reported_length=32):
        import ctypes
        from types import SimpleNamespace
        from unittest.mock import Mock
        real_glib = ctypes.CDLL('libglib-2.0.so.0')
        value = ctypes.create_string_buffer(raw)
        first = h._GList()
        second = h._GList()
        first.data, second.data = 2, 4
        if duplicate:
            first.next = ctypes.pointer(second)
        def get_bytes(_value, size):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = reported_length
            return ctypes.addressof(value)
        secret = SimpleNamespace(
            secret_service_get_sync=Mock(return_value=1),
            secret_service_search_sync=Mock(return_value=ctypes.pointer(first)),
            secret_item_get_locked=Mock(return_value=int(locked)),
            secret_item_get_secret=Mock(return_value=3),
            secret_value_get=Mock(side_effect=get_bytes),
            secret_value_unref=Mock())
        glib = SimpleNamespace(**{name: getattr(real_glib, name) for name in
                               ('g_hash_table_new','g_hash_table_insert','g_hash_table_unref','g_str_hash','g_str_equal')},
                               g_list_free=Mock(), g_error_free=Mock())
        gobject = SimpleNamespace(g_object_unref=Mock())
        libraries = {'libsecret-1.so.0':secret, 'libglib-2.0.so.0':glib, 'libgobject-2.0.so.0':gobject}
        with patch.object(h.ctypes, 'CDLL', side_effect=lambda path: libraries[path]):
            yield secret

    def test_binary_key_retained_and_no_unlock_flag(self):
        raw = b'\x00\n'+b'x'*30
        with self.backend(raw=raw) as secret:
            self.assertEqual(h._libsecret_read('fixture-key'), raw)
        flags = secret.secret_service_search_sync.call_args.args[3]
        self.assertEqual(flags, 10)  # ALL | LOAD_SECRETS; never UNLOCK (4).
        secret.secret_value_unref.assert_called_once_with(3)

    def test_locked_or_duplicate_item_never_materializes_key(self):
        for case in ({'locked':True}, {'duplicate':True}):
            with self.backend(**case) as secret, self.assertRaises(h.Err):
                h._libsecret_read('fixture-key')
            secret.secret_value_get.assert_not_called()

    def test_bad_length_never_copies_unbounded_pointer(self):
        for length in (0, 31, 33, 2**32):
            with self.backend(reported_length=length), patch.object(h.ctypes, 'string_at') as copy, self.assertRaises(h.Err):
                h._libsecret_read('fixture-key')
            copy.assert_not_called()

class SerializedRecords(unittest.TestCase):
    def test_compact_zero_and_one_and_fractional_order(self):
        self.assertEqual(h.extract_i32_after(b'source', record(number('source', 0))), 0)
        self.assertEqual(h.extract_i32_after(b'source', record(number('source', 1))), 1)
        with vault([item()], order=0.125):
            self.assertEqual(h.list_entries()['entries'][0]['order'], 0.125)

    def test_numeric_enum_requires_exact_integer_and_order_is_finite(self):
        for source in (0.0, 1.0, 2.0):
            self.assertEqual(h.extract_i32_after(b'source', record(double('source', source))), int(source))
        for encoded in (double('source', 0.5), double('source', float('nan')), double('source', float('inf')), property_bytes('source', b'\x08')):
            with self.subTest(encoded=encoded), self.assertRaises(h.Err):
                h.extract_i32_after(b'source', record(encoded))
        for order in (float('nan'), float('inf'), -(2**54)):
            with vault([item()], order=order), self.assertRaises(h.Err):
                h.list_entries()

    def test_labels_and_ciphertext_cannot_spoof_root_fields(self):
        injected = property_bytes('source', b'\x05'+struct.pack('<i', 0)).decode('latin1')
        sk = record(string('note', injected), buffer('salt', SALT), string('adapterKeyId', 'fixture-key'), number('source', 1))
        with self.assertRaises(h.Err) as exc:
            h.storage_ref(sk)
        self.assertEqual(exc.exception.code, 'APP_LOCKED')
        blob = record(string('issuer', string('id', 'evil').decode('latin1')), string('id', 'real'))
        self.assertEqual(h.extract_hb_string_after(b'id', blob), 'real')
        blob = record(buffer('__encryptedData', string('id', 'evil')), buffer('salt', SALT))
        self.assertIsNone(h.extract_hb_string_after(b'id', blob))
        self.assertEqual(h.extract_arraybuffer_after(b'salt', blob), SALT)

    def test_nested_fields_do_not_replace_root_properties(self):
        nested = property_bytes('metadata', b'\x02' + number('source', 0) + b'\xff'*4)
        blob = record(nested, number('source', 1))
        self.assertEqual(h.extract_i32_after(b'source', blob), 1)

    def test_latin1_utf16_and_pooled_strings(self):
        self.assertEqual(h.extract_hb_string_after(b'issuer', record(string('issuer', 'Café'))), 'Café')
        self.assertEqual(h.extract_hb_string_after(b'issuer', record(string('issuer', '日本 🔐', utf16=True))), '日本 🔐')
        # Pool: issuer key=0, Fixture value=1, name key=2; name value references 1.
        blob = record(string('issuer', 'Fixture'), property_bytes('name', b'\x10'+struct.pack('<I', 0xfffffffe)+b'\x01'))
        self.assertEqual(h.extract_hb_string_after(b'name', blob), 'Fixture')
        # StringTag(empty data) does enter the string pool, unlike EmptyStringTag.
        blob = record(string('id', ''), property_bytes('issuer', b'\x10'+struct.pack('<I', 0xfffffffe)+b'\x01'))
        self.assertEqual(h.extract_hb_string_after(b'issuer', blob), '')

    def test_two_byte_pool_index_and_duplicate_pooled_key(self):
        fields = [string('key'+str(index), 'value'+str(index)) for index in range(128)]
        # 256 strings are already pooled; alias key adds index256. Value255
        # must now use a two-byte pool index, not consume the following byte.
        blob = record(*fields, property_bytes('alias', b'\x10'+struct.pack('<I', 0xfffffffe)+struct.pack('<H', 255)))
        self.assertEqual(h.extract_hb_string_after(b'alias', blob), 'value127')
        duplicate = record(string('id', 'real'), struct.pack('<I', 0xfffffffe)+b'\x00'+b'\x11')
        with self.assertRaises(h.Err):
            h.extract_hb_string_after(b'id', duplicate)
        with patch.object(h, 'MAX_SSV_POOL', 2), self.assertRaises(h.Err):
            h.extract_hb_string_after(b'id', record(string('id', 'real'), string('issuer', 'Fixture')))

    def test_every_truncated_prefix_of_full_record_fails(self):
        blob = record(number('source', 0), double('order', 0.125), string('id', 'real'),
                      string('issuer', '日本 🔐', utf16=True), buffer('salt', SALT))
        for length in range(len(blob)):
            with self.subTest(length=length), self.assertRaises(h.Err):
                h.extract_hb_string_after(b'id', blob[:length])
        self.assertEqual(h.extract_hb_string_after(b'id', blob), 'real')

    def test_uint8_view_offset_and_plain_arraybuffer(self):
        view = property_bytes('salt', b'\x16\x02' + struct.pack('<QQ', 1, 3) + b'\x15' + struct.pack('<Q', 5) + b'abcde')
        self.assertEqual(h.extract_arraybuffer_after(b'salt', record(view)), b'bcd')
        blob = record(property_bytes('salt', b'\x15'+struct.pack('<Q', 3)+b'abc'))
        self.assertEqual(h.extract_arraybuffer_after(b'salt', blob), b'abc')

    def test_malformed_ambiguous_or_unframed_records_fail_closed(self):
        good = record(string('id', 'real'))
        cases = [good[:-1], good+b'\x00', good+b'x', struct.pack('<I', 16)+good[4:],
                 string('id', 'real'), record(string('id', 'real'), string('id', 'evil')),
                 record(property_bytes('id', b'\x10'+struct.pack('<I', 0xfffffffe)+b'\x01')),
                 record(property_bytes('id', b'\x10'+struct.pack('<I', 1)+b'\x00\xd8')),
                 record(property_bytes('salt', b'\x16\x02'+struct.pack('<QQ', 1, 5)+b'\x15'+struct.pack('<Q', 3)+b'abc')),
                 record(property_bytes('salt', b'\x16\x01'+struct.pack('<QQ', 0, 3)+b'\x15'+struct.pack('<Q', 3)+b'abc'))]
        for blob in cases:
            with self.subTest(length=len(blob)):
                with self.assertRaises(h.Err) as exc:
                    h.extract_hb_string_after(b'id', blob)
                self.assertEqual(exc.exception.code, 'UNSUPPORTED_SCHEMA')

    def test_depth_property_pool_and_string_input_bounds(self):
        nested = b'\x02'+string('id', 'real')+b'\xff'*4
        for _ in range(16):
            nested = b'\x02'+property_bytes('nest', nested)+b'\xff'*4
        cases = [struct.pack('<I', 15)+nested,
                 record(*(number('field'+str(index), index) for index in range(300))),
                 record(property_bytes('id', b'\x10'+struct.pack('<I', 0x80010001)+b'x'))]
        for blob in cases:
            with self.assertRaises(h.Err):
                h.extract_hb_string_after(b'id', blob)

if __name__ == '__main__':
    unittest.main()

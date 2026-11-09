"""Fault-inject the real home transaction; never touch a block device."""
import ctypes as C
import hashlib
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_home_seed import build

EXISTS = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_char_p)
SIMPLE = C.CFUNCTYPE(C.c_int, C.c_void_p)
MARK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int)
RENAME = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_char_p, C.c_char_p)


class Ops(C.Structure):
    _fields_ = [('exists', EXISTS), ('eligible', SIMPLE), ('matches', EXISTS),
                ('mark', MARK), ('create', SIMPLE), ('remove', EXISTS), ('rename', RENAME)]


class HomeTransactions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        (root / 'hash.c').write_text('''#include "sha256.h"
#include "gaijin_home.h"
void hash_hex(const void *p, size_t n, char *out) {gh_sha_hex(p,n,out);}
int valid_token(const char *p) {return gh_reset_valid(p);}
''')
        subprocess.run(['cc', '-shared', '-fPIC', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all', '-I', str(ROOT/'common'),
                        str(ROOT/'common/home_store.c'), str(root/'hash.c'), '-o', str(root/'home.so')], check=True)
        cls.lib = C.CDLL(str(root/'home.so'))
        cls.lib.gh_store_run.argtypes = [C.POINTER(Ops), C.c_void_p, C.c_char_p]
        cls.lib.hash_hex.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
        cls.lib.valid_token.argtypes = [C.c_char_p]
        cls.token = ('ghr1:' + 'a'*32 + ':02000000:' + 'b'*64).encode()

    def run_transaction(self, existing=None, token=None, fail='', eligible=1,
                        artifacts=(), change_after_create=False, mutate_on_failure=False):
        files = dict(artifacts)
        if existing is not None:
            files['userdata.ext2'] = existing
        events = []
        def step(name):
            events.append(name)
            return name == fail
        def exists(_, name):
            return int(name.decode() in files)
        def space(_):
            step('eligible')
            return eligible
        def matches(_, request):
            step('identity')
            return int(request == self.token and files.get('userdata.ext2') == b'old-dirty')
        def mark(_, reset):
            failed = step('mark')
            files['provision-v1'] = b'reset' if reset else b'create'
            return -int(failed)
        def create(_):
            failed = step('candidate')
            files['userdata.new'] = b'partial' if failed else b'factory-verified'
            if change_after_create:
                files['userdata.ext2'] = b'different-image'
            return -int(failed)
        def remove(_, name):
            name = name.decode()
            failed = step('erase' if name == 'userdata.ext2' else 'finish')
            if not failed or mutate_on_failure:
                files.pop(name, None)
            return -int(failed)
        def rename(_, source, target):
            source, target = source.decode(), target.decode()
            failed = step('publish')
            if target in files:
                return -1
            if not failed or mutate_on_failure:
                files[target] = files.pop(source)
            return -int(failed)
        callbacks = (EXISTS(exists), SIMPLE(space), EXISTS(matches), MARK(mark),
                     SIMPLE(create), EXISTS(remove), RENAME(rename))
        ops = Ops(*callbacks)
        result = self.lib.gh_store_run(C.byref(ops), None, token)
        return result, files, events

    def test_sha256_matches_independent_vectors(self):
        for data in (b'', b'abc', b'a'*1000000, bytes(range(256))*21):
            output = C.create_string_buffer(65)
            self.lib.hash_hex(data, len(data), output)
            self.assertEqual(output.value.decode(), hashlib.sha256(data).hexdigest())

    def test_strict_request_syntax(self):
        self.assertEqual(self.lib.valid_token(self.token), 1)
        for token in (b'', self.token[:-1], self.token+b'x', self.token.replace(b':', b'/', 1),
                      self.token.upper(), self.token[:37]+b'0'+self.token[38:]):
            self.assertEqual(self.lib.valid_token(token), 0)

    def test_absent_image_is_created_and_existing_images_are_untouched(self):
        result, files, events = self.run_transaction()
        self.assertEqual(result, 1)
        self.assertEqual(files, {'userdata.ext2': b'factory-verified'})
        self.assertNotIn('erase', events)
        for data in (b'old-dirty', b'healthy-home', b'unsupported-data'):
            result, files, events = self.run_transaction(existing=data)
            self.assertEqual(result, 0)
            self.assertEqual(files, {'userdata.ext2': data})
            self.assertEqual(events, [])

    def test_partial_state_never_becomes_an_empty_install(self):
        for name in ('provision-v1', 'userdata.new'):
            result, files, events = self.run_transaction(artifacts=((name, b'partial'),))
            self.assertEqual(result, 4)
            self.assertEqual(events, [])
            self.assertEqual(files, {name: b'partial'})

    def test_reset_verifies_candidate_and_rechecks_identity_before_erase(self):
        result, files, events = self.run_transaction(b'old-dirty', self.token)
        self.assertEqual(result, 2)
        self.assertEqual(files, {'userdata.ext2': b'factory-verified'})
        self.assertEqual(events, ['identity','eligible','mark','candidate','identity','erase','publish','finish'])
        result, files, events = self.run_transaction(b'old-dirty', self.token, change_after_create=True)
        self.assertEqual(result, 6)
        self.assertNotIn('erase', events)
        self.assertEqual(files['userdata.ext2'], b'different-image')
        self.assertIn('provision-v1', files)

    def test_space_and_stale_request_fail_before_any_mutation(self):
        for available, expected in ((0, 5), (-1, 7)):
            result, files, events = self.run_transaction(b'old-dirty', self.token, eligible=available)
            self.assertEqual(result, expected)
            self.assertEqual(files, {'userdata.ext2': b'old-dirty'})
            self.assertNotIn('mark', events)
        for current in (None, b'healthy-home', b'unsupported-data', b'factory-verified'):
            result, files, events = self.run_transaction(current, self.token)
            self.assertEqual(result, 6)
            self.assertNotIn('erase', events)

    def test_failure_at_each_durable_boundary_does_not_replay_erase(self):
        for failure in ('mark','candidate','erase','publish','finish'):
            for partial in (False, True):
                with self.subTest(failure=failure, partial=partial):
                    result, files, events = self.run_transaction(b'old-dirty', self.token,
                        fail=failure, mutate_on_failure=partial)
                    self.assertEqual(result, 4)
                    if failure in ('mark','candidate'):
                        self.assertEqual(files['userdata.ext2'], b'old-dirty')
                        self.assertNotIn('erase', events)
                    replay, after, replay_events = self.run_transaction(token=self.token,
                        artifacts=tuple(files.items()))
                    self.assertIn(replay, (4, 6))
                    self.assertEqual(after, files)
                    self.assertNotIn('erase', replay_events)


class FactorySeed(unittest.TestCase):
    def test_checked_factory_is_reproducible_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            a = build(Path(temporary)/'a')
            b = build(Path(temporary)/'b')
            self.assertEqual(a['expanded_sha256'], b['expanded_sha256'])
            self.assertEqual(a['compressed_sha256'], b['compressed_sha256'])
            self.assertEqual(a['capacity_bytes'], 32*1024*1024)
            self.assertLess(a['compressed_bytes'], 65536)
            self.assertEqual(a['host_fsck'], 'PASS')


class AppFSSelection(unittest.TestCase):
    def test_loader_reads_supported_argument_layout_and_rejects_bad_selections(self):
        source=(ROOT/'main/home.c').read_text()
        source=source[source.index('int gaijin_home_boot_selection'):source.index('static int path_for')]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'selection.c').write_text('#include <stddef.h>\n#include <string.h>\n#include "gaijin_home.h"\n'+source)
            subprocess.run(['cc','-shared','-fPIC','-Wall','-Wextra','-Werror','-I',str(ROOT/'common'),
                            str(root/'selection.c'),'-o',str(root/'selection.so')],check=True)
            lib=C.CDLL(str(root/'selection.so'))
            lib.gaijin_home_boot_selection.argtypes=[C.c_void_p,C.c_size_t,C.c_void_p]
            token=('ghr1:'+'a'*32+':02000000:'+'b'*64).encode()
            record=bytearray(256)
            struct.pack_into('<QI',record,0,0x2af7de4a994f7236,7)
            # AppFS bootloader has consumed valid; the argument is still available.
            record[12]=0
            record[13:13+len(token)]=token
            out=C.create_string_buffer(128)
            self.assertEqual(lib.gaijin_home_boot_selection(bytes(record),256,out),7)
            self.assertEqual(out.value,token)
            for offset in (0,8):
                bad=bytearray(record)
                if offset==0: bad[0]^=1
                else: struct.pack_into('<I',bad,8,255)
                self.assertEqual(lib.gaijin_home_boot_selection(bytes(bad),256,out),-1)
                self.assertEqual(out.value,b'')
            record[13:141]=b'x'*128
            self.assertEqual(lib.gaijin_home_boot_selection(bytes(record),256,out),-1)
            self.assertEqual(lib.gaijin_home_boot_selection(bytes(record),100,out),-1)

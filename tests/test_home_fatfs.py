"""Run home creation/reset with the real ESP-IDF dynamic-buffer FatFs library."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HomeFatFs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        idf = Path(os.environ.get('IDF_PATH', Path.home() / '.espressif/v6.0.2/esp-idf'))
        cls.fatfs = idf / 'components/fatfs/src'
        cls.mkfs = shutil.which('mkfs.fat', path=os.environ.get('PATH', '') + os.pathsep + '/usr/sbin')
        if not (cls.fatfs / 'ff.c').exists() or not cls.mkfs:
            raise unittest.SkipTest('ESP-IDF FatFs source and host mkfs.fat required')
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        for name in ('FreeRTOS.h', 'semphr.h'):
            file = cls.work / 'freertos' / name
            file.parent.mkdir(exist_ok=True)
            file.write_text('#define portTICK_PERIOD_MS 1\n')
        config = {
            'USE_FASTSEEK': 0, 'USE_LABEL': 0, 'CODEPAGE': 437, 'LFN_HEAP': 1,
            'MAX_LFN': 255, 'VOLUME_COUNT': 1, 'PER_FILE_CACHE': 1, 'FS_LOCK': 0,
            'TIMEOUT_MS': 10000, 'USE_DYN_BUFFERS': 1, 'DONT_TRUST_FREE_CLUSTER_CNT': 0,
            'DONT_TRUST_LAST_ALLOC': 0,
        }
        (cls.work / 'sdkconfig.h').write_text(''.join(f'#define CONFIG_FATFS_{key} {value}\n' for key, value in config.items()) +
                                             '#define CONFIG_WL_SECTOR_SIZE 4096\n')
        seed = ROOT / 'build/home/home_seed.h'
        if not seed.exists():
            subprocess.run(['python3', str(ROOT / 'tools/build_home_seed.py')], cwd=ROOT, check=True, capture_output=True)
        cls.expected_hash = json.loads((seed.parent / 'home-seed.json').read_text())['expanded_sha256']
        actual = (ROOT / 'main/home.c').read_text()
        # Retain all loader logic; replace only includes for the host adapters.
        actual = '\n'.join(line for line in actual.splitlines() if not line.startswith('#include'))
        harness = (ROOT / 'tests/fixtures/host-home-fatfs.c').read_text()
        cls.source = harness.replace('/* HOME_IMPLEMENTATION */', actual)
        cls.binary = cls.compile(cls.source, 'home-fatfs')

    @classmethod
    def compile(cls, source, name):
        file = cls.work / (name + '.c')
        file.write_text(source)
        binary = cls.work / name
        subprocess.run(['cc', '-std=c11', '-g', '-O1', '-ftrivial-auto-var-init=pattern',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
                        '-I', str(cls.work), '-I', str(cls.fatfs), '-I', str(ROOT / 'common'),
                        '-I', str(ROOT / 'build/home'), str(file), str(cls.fatfs / 'ff.c'),
                        str(cls.fatfs / 'ffunicode.c'), str(ROOT / 'common/home_store.c'),
                        '-lz', '-o', str(binary)], check=True, capture_output=True, text=True)
        return binary

    def disk(self, directory):
        image = Path(directory) / 'test-only-partition.img'
        with image.open('wb') as file:
            file.truncate(2 * 1024 * 1024 * 1024)
        subprocess.run([self.mkfs, '-F', '32', '-s', '32', str(image)], check=True, capture_output=True)
        return image

    def run_home(self, binary, image, mode='create'):
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:halt_on_error=1')
        return subprocess.run([str(binary), str(image), mode], env=env, capture_output=True, text=True, timeout=30)

    def test_factory_creation_reuse_and_dirty_reset_with_real_fatfs(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.disk(directory)
            for mode, status in (('create', 'created'), ('reuse', 'existing'), ('reset', 'recreated')):
                result = self.run_home(self.binary, image, mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('STATUS:' + status, result.stdout)
                self.assertIn('HASH:' + self.expected_hash, result.stdout)

    def test_negative_control_reproduces_uninitialized_fil_crash(self):
        original = self.source.replace('FIL file={0}', 'FIL file')
        self.assertNotEqual(original, self.source)
        binary = self.compile(original, 'uninitialized-fil')
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_home(binary, self.disk(directory))
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue('AddressSanitizer' in result.stderr or 'runtime error:' in result.stderr, result.stderr)
            self.assertIn('f_open', result.stderr)
            self.assertIn('home_mark', result.stderr)

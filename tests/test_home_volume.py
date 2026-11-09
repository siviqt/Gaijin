"""Exercise the actual loader FAT guard against independent sector fixtures."""
import ctypes as C
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from check_home_volume import inspect_partition


class HomeVolume(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        text = (ROOT / 'main/home.c').read_text()
        guard = text[text.index('static int volume_check'):text.index('static int home_eligible')]
        harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "gaijin_home.h"
typedef unsigned long DWORD;
typedef unsigned long LBA_t;
typedef int FRESULT;
typedef struct {unsigned fs_type,csize,n_fats,pdrv;LBA_t volbase,fatbase,fsize;} FATFS;
typedef struct {struct {unsigned sector_size;} csd;unsigned long raw_cid[4];} sdmmc_card_t;
#define FS_FAT16 2
#define FS_FAT32 3
#define FR_OK 0
#define ESP_OK 0
const char *TAG="home";
static unsigned char sector[512], image[3][512];
static FATFS fs;
static sdmmc_card_t card;
static int fail_getfree, fail_read;
static char diagnostic[256];
struct home_context {FATFS *fs;char cid[33];};
static FRESULT f_getfree(const char *path,DWORD *count,FATFS **out)
{(void)path;*count=65536;*out=&fs;return fail_getfree;}
static sdmmc_card_t *gaijin_sd_card(void) {return &card;}
static int sdmmc_read_sectors(sdmmc_card_t *c,void *out,LBA_t lba,unsigned n)
{(void)c;if(n!=1 || lba>2 || fail_read==(int)lba) return -1;memcpy(out,image[lba],512);return 0;}
void log_error(const char *fmt,...)
{va_list ap;va_start(ap,fmt);vsnprintf(diagnostic,sizeof(diagnostic),fmt,ap);va_end(ap);}
#define ESP_LOGW(tag,...) ((void)(tag),log_error(__VA_ARGS__))
'''
        harness += guard + r'''
int run_guard(const unsigned char *boot,const unsigned char *first,const unsigned char *second,
              int fat32,int getfree_error,int read_error)
{
    memcpy(image[0],boot,512);memcpy(image[1],first,512);memcpy(image[2],second,512);
    fs=(FATFS){.fs_type=fat32?FS_FAT32:FS_FAT16,.csize=32,.n_fats=2,.volbase=0,.fatbase=1,.fsize=1};
    card.csd.sector_size=512;fail_getfree=getfree_error;fail_read=read_error;diagnostic[0]=0;
    struct home_context ctx={0};return volume_check(&ctx);
}
const char *last_diagnostic(void) {return diagnostic;}
'''
        (root / 'guard.c').write_text(harness)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-I', str(ROOT / 'common'), str(root / 'guard.c'),
                        '-o', str(root / 'guard.so')], check=True)
        cls.lib = C.CDLL(str(root / 'guard.so'))
        cls.lib.run_guard.argtypes = [C.c_void_p] * 3 + [C.c_int] * 3
        cls.lib.last_diagnostic.restype = C.c_char_p

    def fixture(self, fat32=True, state=0, extended=0, entries=None):
        boot = bytearray(512)
        boot[510:512] = b'\x55\xaa'
        struct.pack_into('<H', boot, 11, 512)
        boot[13] = 32
        struct.pack_into('<H', boot, 14, 1)
        boot[16] = 2
        struct.pack_into('<I' if fat32 else '<H', boot, 36 if fat32 else 22, 1)
        if fat32:
            struct.pack_into('<H', boot, 40, extended)
        boot[65 if fat32 else 37] = state
        tables = []
        for entry in entries or [0xffffffff if fat32 else 0xffff] * 2:
            table = bytearray(512)
            struct.pack_into('<I' if fat32 else '<H', table, 4 if fat32 else 2, entry)
            tables.append(bytes(table))
        return bytes(boot), *tables

    def run_guard(self, fixture, fat32=True, getfree_error=0, read_error=-1):
        result = self.lib.run_guard(*fixture, fat32, getfree_error, read_error)
        return result, self.lib.last_diagnostic().decode()

    def test_clean_fat16_and_fat32_are_eligible(self):
        for fat32 in (False, True):
            self.assertEqual(self.run_guard(self.fixture(fat32), fat32)[0], 0)

    def test_reserved_fat32_high_nibble_does_not_reject_matching_copies(self):
        self.assertEqual(self.run_guard(self.fixture(entries=[0xffffffff, 0x0fffffff]))[0], 0)

    def test_dirty_boot_flag_and_each_fat_error_flag_still_block_creation(self):
        for fat32 in (False, True):
            with self.subTest(fat32=fat32):
                result, message = self.run_guard(self.fixture(fat32, state=1), fat32)
                self.assertNotEqual(result, 0)
                self.assertIn('dirty', message)
                bits = (27, 26) if fat32 else (15, 14)
                clean = 0xffffffff if fat32 else 0xffff
                for bit in bits:
                    for copy in (0, 1):
                        entries = [clean, clean]
                        entries[copy] &= ~(1 << bit)
                        result, message = self.run_guard(self.fixture(fat32, entries=entries), fat32)
                        self.assertNotEqual(result, 0)
                        self.assertIn('FAT[1]', message)

    def test_io_failures_mirroring_and_meaningful_copy_mismatch_have_diagnostics(self):
        for args in ({'getfree_error': 1}, {'read_error': 0}, {'read_error': 1}, {'read_error': 2}):
            result, message = self.run_guard(self.fixture(), **args)
            self.assertNotEqual(result, 0)
            self.assertTrue(message)
        for fixture in (self.fixture(extended=0x80), self.fixture(entries=[0xffffffff, 0x0ffffffe])):
            result, message = self.run_guard(fixture)
            self.assertNotEqual(result, 0)
            self.assertTrue(message)

    def test_host_diagnostic_reads_exact_metadata_without_mutating_the_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'partition.img'
            for state in (0, 1):
                content = b''.join(self.fixture(state=state))
                image.write_bytes(content)
                report = inspect_partition(image)
                self.assertEqual(report['boot_state'], f'0x{state:02x}')
                self.assertEqual(report['fat_entry1'], ['0xffffffff'] * 2)
                self.assertEqual(len(report['observations']), state)
                self.assertTrue(report['read_only'])
                self.assertEqual(image.read_bytes(), content)

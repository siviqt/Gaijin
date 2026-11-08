"""Compile the loader's sector reference with read-only FatFS fixtures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SDReferenceTests(unittest.TestCase):
    def test_reads_mounted_drive_without_writes_and_handles_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / "ff.h").write_text('''#include <stdint.h>
typedef unsigned char BYTE;
typedef int FRESULT;
#define FR_OK 0
#define FA_READ 1
struct volume { BYTE pdrv; unsigned volbase; };
typedef struct { struct { struct volume *fs; } obj; } FIL;
FRESULT f_open(FIL *, const char *, unsigned);
FRESULT f_close(FIL *);
''')
            (work / "diskio.h").write_text('''typedef int DRESULT;
#define RES_OK 0
DRESULT disk_read(BYTE, BYTE *, unsigned, unsigned);
''')
            (work / "esp_attr.h").write_text('#define DRAM_ATTR\n')
            (work / "esp_err.h").write_text('typedef int esp_err_t;\n')
            (work / "esp_log.h").write_text('''#define ESP_LOG_INFO 1
void capture(const char *, const char *, ...);
void capture_hex(const char *, const void *, unsigned, int);
#define ESP_LOGI capture
#define ESP_LOGE capture
#define ESP_LOG_BUFFER_HEX_LEVEL capture_hex
''')
            harness = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static int fail_open, fail_read, reads, closes, dumps;
static char message[256];
static struct volume volume = { .pdrv = 3, .volbase = 2048 };
FRESULT f_open(FIL *file, const char *name, unsigned flags)
{
    assert(!strcmp(name, "0:/apps/com.github.siviqt.gaijin/boot/manifest.bin"));
    assert(flags == FA_READ);
    if (fail_open) return 3;
    file->obj.fs = &volume;
    return FR_OK;
}
FRESULT f_close(FIL *file) { assert(file->obj.fs == &volume); closes++; return FR_OK; }
DRESULT disk_read(BYTE drive, BYTE *buffer, unsigned sector, unsigned count)
{
    assert(drive == 3 && sector == 0 && count == 1);
    assert((uintptr_t)buffer % 64 == 0);
    reads++;
    if (fail_read) return 1;
    memset(buffer, 0, 512); buffer[510] = 0x55; buffer[511] = 0xaa;
    return RES_OK;
}
void capture(const char *tag, const char *format, ...)
{
    (void)tag;
    va_list ap; va_start(ap, format);
    vsnprintf(message, sizeof(message), format, ap);
    va_end(ap);
}
void capture_hex(const char *tag, const void *bytes, unsigned length, int level)
{ (void)tag; (void)bytes; assert(length == 32 && level == ESP_LOG_INFO); dumps++; }
int main(void)
{
    gaijin_sd_reference();
    assert(reads == 1 && closes == 1 && dumps == 1);
    assert(strstr(message, "nonzero=2 signature=55aa FAT-start=2048"));
    fail_open = 1;
    gaijin_sd_reference();
    assert(reads == 1 && closes == 1 && dumps == 1);
    assert(strstr(message, "manifest open failed"));
    fail_open = 0; fail_read = 1;
    gaijin_sd_reference();
    assert(reads == 2 && closes == 2 && dumps == 1);
    assert(strstr(message, "disk_read LBA0 failed"));
    return 0;
}
'''
            c = work / "reference.c"
            c.write_text((ROOT / "main/sd_diagnostic.c").read_text() + harness)
            exe = work / "reference"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(work),
                            "-I", str(ROOT / "main"),
                            str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

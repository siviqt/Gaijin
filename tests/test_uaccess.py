"""Run the platform bounds with Linux's actual generic string-copy code."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def platform_bounds():
    patch = (ROOT / 'linux/patches/0002-esp32p4-user-access.patch').read_text()
    bounds = ''.join(line[1:] for line in patch.splitlines(True)
                     if line.startswith('+') and not line.startswith('+++'))
    return bounds.replace('#ifdef CONFIG_SOC_ESP32P4\n', '').replace('#endif\n', '')


class UserAccessTests(unittest.TestCase):
    def test_rv32_built_kernel_string_functions(self):
        cross = os.environ.get('CROSS_COMPILE', 'riscv32-esp-elf-')
        compiler, emulator = shutil.which(cross + 'gcc'), shutil.which('qemu-riscv32')
        objects = [ROOT / 'build/kernel/lib' / name for name in
                   ('strncpy_from_user.o', 'strnlen_user.o')]
        if not compiler or not emulator or not all(obj.exists() for obj in objects):
            self.skipTest('build the kernel and set CROSS_COMPILE to run its RV32 objects')
        environment = r'''
#define __user
#define NULL ((void *)0)
#define UINTPTR_MAX 0xffffffffUL
#define ULONG_MAX 0xffffffffUL
#define EFAULT 14
typedef unsigned int uintptr_t;
typedef unsigned int size_t;
_Static_assert(sizeof(long)==4, "RV32 required");
#define assert(x) do { if (!(x)) return 1; } while(0)
#define puts(x) ((void)0)
static _Alignas(16) char ram[64];
unsigned long phys_ram_base=(unsigned long)ram;
void *high_memory=ram+sizeof(ram);
#define PAGE_OFFSET phys_ram_base
void *memset(void *p,int c,size_t n) { char *s=p; while(n--) *s++=c; return p; }
void *memcpy(void *p,const void *q,size_t n) {
    char *d=p; const char *s=q; while(n--) *d++=*s++; return p;
}
int memcmp(const void *p,const void *q,size_t n) {
    const unsigned char *a=p,*b=q;
    while(n--) { int d=*a++-*b++; if(d) return d; } return 0;
}
int strcmp(const char *a,const char *b) {
    while(*a && *a==*b) { a++; b++; } return (unsigned char)*a-(unsigned char)*b;
}
long strncpy_from_user(char *,const char *,long);
long strnlen_user(const char *,long);
'''
        with tempfile.TemporaryDirectory() as directory:
            source, start, binary = [Path(directory)/name for name in ('check.c','start.S','check.elf')]
            source.write_text(environment + platform_bounds() +
                              (ROOT / 'tests/fixtures/uaccess-check.c').read_text())
            start.write_text('.text\n.global _start\n_start:\n.option norelax\n'
                             'la gp,__global_pointer$\ncall main\nli a7,93\necall\n')
            subprocess.run([compiler, '-march=rv32imac_zicsr_zifencei', '-mabi=ilp32',
                            '-O2', '-ffreestanding', '-fno-builtin', '-fno-pie',
                            '-nostdlib', '-static', '-Wl,--gc-sections', '-Wl,--no-relax',
                            str(start), str(source), *map(str, objects), '-o', str(binary)], check=True)
            subprocess.run([emulator, str(binary)], check=True, timeout=10)

    def test_runtime_probe_rejects_bad_syscall_pointers(self):
        with tempfile.TemporaryDirectory() as directory:
            main, binary = Path(directory)/'probe.c', Path(directory)/'probe'
            main.write_text('int gaijin_uaccess_probe(void);\nint main(void) { return gaijin_uaccess_probe(); }\n')
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                            str(main), str(ROOT / 'userspace/package/gaijin-abi-check/uaccess-probe.c'),
                            '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_generic_strings_reject_invalid_addresses_and_preserve_boundaries(self):
        source = Path(os.environ.get('GAIJIN_KERNEL_SOURCE', ROOT / 'build/linux-6.18.35'))
        if not (source / 'lib/strncpy_from_user.c').exists():
            self.skipTest('prepare the pinned kernel or set GAIJIN_KERNEL_SOURCE')
        bounds = platform_bounds()
        # Keep the upstream algorithms intact; only their kernel environment is
        # modeled. Raw loads deliberately have no extra validity check, so a
        # missing begin/check reproduces the original invalid dereference.
        shim = r'''
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#define __user
#define __force
#define unlikely(x) (x)
#define likely(x) (x)
#define EXPORT_SYMBOL(x)
#define might_fault() ((void)0)
#define should_fail_usercopy() 0
#define kasan_check_write(p,n) ((void)0)
#define check_object_size(p,n,b) ((void)0)
#define can_do_masked_user_access() 0
#define masked_user_access_begin(p) (p)
#define user_read_access_end() ((void)0)
#define user_read_access_begin(p,n) __access_ok(p,n)
#define untagged_addr(p) (p)
#define unsafe_get_user(x,p,label) do { if (0) goto label; (x)=*(p); } while (0)
#define PAGE_OFFSET ((unsigned long)ram)
static _Alignas(16) char ram[64];
void *high_memory=ram+sizeof(ram);
/* BOUNDS */
/* Word helpers for the native host's little-endian word width. */
struct word_at_a_time { int unused; };
#define WORD_AT_A_TIME_CONSTANTS {0}
static unsigned long has_zero(unsigned long word, unsigned long *data,
                             const struct word_at_a_time *unused) {
    (void)unused;
    for (unsigned i=0;i<sizeof(word);i++)
        if (((word>>(i*8))&255)==0) {
            *data=i ? (1UL<<(i*8))-1 : 0;
            return 1;
        }
    return 0;
}
#define prep_zero_mask(c,d,k) (d)
#define create_zero_mask(d) (d)
#define zero_bytemask(d) (d)
static unsigned long find_zero(unsigned long mask) {
    unsigned long n=0;
    while (mask&255) { n++; mask>>=8; }
    return n;
}
#define aligned_byte_mask(n) ((1UL<<((n)*8))-1)
'''.replace('/* BOUNDS */', bounds)
        algorithms = '\n'.join('\n'.join(line for line in
            (source / 'lib' / name).read_text().splitlines()
            if not line.startswith('#include'))
            for name in ('strncpy_from_user.c', 'strnlen_user.c'))
        main = (ROOT / 'tests/fixtures/uaccess-check.c').read_text()
        with tempfile.TemporaryDirectory() as directory:
            code, binary = Path(directory)/'uaccess.c', Path(directory)/'uaccess'
            code.write_text(shim + algorithms + main)
            subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined',
                            '-fno-sanitize-recover=all', str(code), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=10,
                           env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
            # Demonstrate that the generic string entry really needs the range
            # check: bypassing access_begin must fail on the first NULL string.
            code.write_text((shim + algorithms + main).replace(
                '#define user_read_access_begin(p,n) __access_ok(p,n)',
                '#define user_read_access_begin(p,n) 1'))
            subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=undefined',
                            '-fno-sanitize-recover=all', str(code), '-o', str(binary)], check=True)
            negative = subprocess.run([str(binary)], capture_output=True, timeout=10)
            self.assertNotEqual(negative.returncode, 0, 'unchecked NULL string unexpectedly succeeded')

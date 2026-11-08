"""Bound the actual P4 exec-cache IRQ exclusion and preserve cache ordering."""
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CacheLatencyTests(unittest.TestCase):
    def test_loaded_text_cache_sync_leaves_frame_irq_gaps(self):
        patch = (ROOT / 'linux/patches/0001-gaijin-esp32p4.patch').read_text()
        section = patch.split('+++ b/arch/riscv/include/asm/cacheflush.h\n', 1)[1].split('\n--- ', 1)[0]
        # Reconstruct the new-side text of the function from the shipped patch.
        text = ''.join(line[1:] for line in section.splitlines(True)
                       if line.startswith(('+', ' ')))
        start = text.index('static inline void local_flush_icache_range')
        opened = text.index('{', start)
        depth = 1
        end = opened + 1
        while depth:
            depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        function = text[start:end].replace('asm volatile ("fence.i" ::: "memory");', '')
        function = function.replace('0x4fc003f4UL', '(uintptr_t)fake_wb')
        function = function.replace('0x4fc003e4UL', '(uintptr_t)fake_inv')
        start = text.index('static inline void flush_icache_range')
        end = text.index('\n}', start) + len('\n}')
        public = text[start:end]
        harness = r'''
#include <stdint.h>
#include <assert.h>
#define CONFIG_SOC_ESP32P4 1
#define min(a,b) ((a)<(b)?(a):(b))
static unsigned enabled=1, step, gaps, chunks;
static uint32_t next, limit, address, length;
#define local_irq_save(f) do { (f)=enabled; enabled=0; } while (0)
#define local_irq_restore(f) do { assert(step==0); enabled=(f); if(enabled) gaps++; } while (0)
static void fake_wb(uint32_t map,uint32_t addr,uint32_t len) {
    assert(!enabled && len>0 && len<=4096);
    if(step==0) {
        assert(map==0x10 && addr==next && addr+len<=limit);
        address=addr; length=len; next+=len; chunks++;
    } else assert(step==1 && map==0x20 && addr==address && len==length);
    step++;
}
static void fake_inv(uint32_t map,uint32_t addr,uint32_t len) {
    assert(!enabled && addr==address && len==length);
    assert((step==2 && map==3) || (step==3 && map==0x20));
    step=(step+1)%4;
}
/* FUNCTION */
/* PUBLIC */
int main(void) {
    next=0x49001000; limit=next+600123;
    flush_icache_range(next,limit);
    assert(next==limit && gaps==chunks && chunks>100 && enabled);
    /* A caller that already masked IRQs must never be forcibly unmasked. */
    enabled=0; gaps=chunks=0; next=0x49002000; limit=next+9001;
    flush_icache_range(next,limit);
    assert(next==limit && chunks==3 && gaps==0 && !enabled);
    return 0;
}
'''.replace('/* FUNCTION */', function).replace('/* PUBLIC */', public)
        with tempfile.TemporaryDirectory() as directory:
            source, binary = Path(directory)/'cache.c', Path(directory)/'cache'
            source.write_text(harness)
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

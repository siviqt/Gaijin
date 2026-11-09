"""Execute the loader's actual ISR, renderer and teardown with injected faults."""
import ctypes as C
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Progress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        source = (ROOT / 'main/display.c').read_text()
        source = source[source.index('/* Loader-only scanout.'):]
        harness = (ROOT / 'tests/fixtures/host-progress.c').read_text()
        (work / 'progress.c').write_text(harness.replace('/* PROGRESS_IMPLEMENTATION */', source))
        subprocess.run(['cc', '-shared', '-fPIC', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all', str(work / 'progress.c'),
                        '-o', str(work / 'progress.so')], check=True)
        cls.lib = C.CDLL(str(work / 'progress.so'))
        cls.lib.mock_stat.argtypes = [C.c_char_p]
        cls.lib.mock_stat.restype = C.c_uint64
        cls.lib.mock_fault.argtypes = [C.c_char_p, C.c_uint]
        cls.lib.mock_frame.argtypes = [C.c_uint, C.c_int64, C.c_uint]
        cls.lib.mock_draw.argtypes = [C.c_uint, C.c_int64]
        cls.lib.mock_log.restype = C.c_char_p

    def setUp(self):
        self.lib.mock_reset()

    def s(self, key):
        return self.lib.mock_stat(key.encode())

    def fault(self, key, value=1):
        self.lib.mock_fault(key.encode(), value)

    def assert_stopped(self):
        for field in ('running', 'started', 'irq', 'generation', 'propagation', 'dpi', 'dma', 'pending'):
            self.assertEqual(self.s(field), 0, field)
        self.assertEqual(self.s('guards'), 1)

    def test_start_primes_dma_before_dpi_and_owns_an_internal_ram_interrupt(self):
        self.lib.mock_start()
        self.assertEqual(self.s('running'), 1)
        self.assertEqual(self.s('irq'), 1)
        self.assertEqual(self.s('flags'), 0x400 | 0x800 | 2)
        self.assertEqual(self.s('bad-source'), 0)
        self.assertEqual(self.s('bad-start-order'), 0)
        self.assertEqual(self.s('descriptor-ok'), 1)
        self.assertEqual(self.s('dma-starts'), 1)
        self.assertEqual(self.lib.mock_stop(), 0)
        self.assert_stopped()

    def test_frame_completions_rearm_immediately_with_fresh_full_descriptors(self):
        self.lib.mock_start()
        for frame in range(1, 121):
            self.lib.mock_corrupt_descriptor()
            self.lib.mock_frame(2, 1000000 + frame * 16677, 0)
            self.assertEqual(self.s('frames'), frame)
            self.assertEqual(self.s('dma-starts'), frame + 1)
            self.assertEqual(self.s('descriptor-ok'), 1)
        self.assertEqual(self.s('max-frame-us'), 16677)
        self.assertEqual(self.s('logs'), 0)  # no ISR logging
        self.lib.mock_stop()
        self.assertIn(b'frames=120', self.lib.mock_log())
        self.assertIn(b'irq-released=1', self.lib.mock_log())

    def test_dma_errors_win_over_completion_and_stop_without_retry(self):
        for bit in range(5, 14):
            with self.subTest(bit=bit):
                self.lib.mock_reset()
                self.lib.mock_start()
                self.lib.mock_frame(2 | (1 << bit), 1016677, 0)
                self.assertEqual(self.s('dma-errors'), 1)
                self.assertEqual(self.s('frames'), 0)
                self.assertEqual(self.s('dma-starts'), 1)
                self.assertNotEqual(self.lib.mock_stop(), 0)
                self.assert_stopped()
                self.lib.mock_start()
                self.assertEqual(self.s('allocs'), 1)  # latched fault prevents restarting

    def test_underrun_observations_use_raw_state_without_stopping_refresh(self):
        self.lib.mock_start()
        self.lib.mock_frame(2, 1016677, 1)
        self.lib.mock_frame(2, 1033354, 0)
        self.assertEqual(self.s('underruns'), 1)
        self.assertEqual(self.s('frames'), 2)
        self.assertEqual(self.lib.mock_stop(), 0)
        self.assertIn(b'underrun-observations=1', self.lib.mock_log())

    def test_rendering_coalesces_percentages_limits_rate_and_forces_final_value(self):
        self.lib.mock_start()
        initial = self.s('sync-bytes')
        for percent in range(10):
            self.lib.mock_draw(percent, 1000000 + percent * 10000)
        self.assertEqual(self.s('draws'), 1)
        self.assertEqual(self.s('sync-bytes'), initial)
        self.lib.mock_draw(10, 1100000)
        self.assertEqual(self.s('percent'), 10)
        self.assertEqual(self.s('draws'), 2)
        self.assertEqual(self.lib.mock_pixel(159, 244), 0x07e0)
        self.assertEqual(self.lib.mock_pixel(160, 244), 0x0841)
        self.assertEqual(self.lib.mock_pixel(159, 240), 0xffff)
        self.lib.mock_draw(5, 1300000)
        self.assertEqual(self.s('percent'), 10)
        self.lib.mock_draw(200, 1100001)  # clamped 100% bypasses the time limit
        self.assertEqual(self.s('percent'), 100)
        self.assertEqual(self.lib.mock_pixel(699, 287), 0x07e0)
        self.assertEqual(self.s('sync-bytes') - initial, 600 * 128)
        self.assertEqual(self.s('largest-cache'), 4096)
        self.assertEqual(self.s('bad-cache'), 0)
        self.lib.mock_stop()
        self.assert_stopped()

    def test_completion_can_interrupt_dirty_region_sync(self):
        self.lib.mock_start()
        self.fault('inject-cache')
        self.lib.mock_draw(25, 1100000)
        self.assertEqual(self.s('frames'), 1)
        self.assertEqual(self.s('dma-starts'), 2)
        self.assertEqual(self.s('percent'), 25)
        self.assertEqual(self.s('bad-cache'), 0)
        self.assertEqual(self.lib.mock_stop(), 0)

    def test_queued_completion_during_stop_cannot_restart_dma(self):
        self.lib.mock_start()
        self.fault('inject-stop')
        self.assertEqual(self.lib.mock_stop(), 0)
        self.assertEqual(self.s('dma-starts'), 1)
        self.assert_stopped()
        self.assertEqual(self.lib.mock_stop(), 0)
        self.assertEqual(self.s('frees'), 1)
        self.assertEqual(self.s('logs'), 1)

    def test_cache_failures_during_initialization_and_priming_unwind_resources(self):
        # 188 bounded framebuffer calls, then descriptor capture/invalidate/publish.
        for call in (1, 188, 189, 190, 191):
            with self.subTest(call=call):
                self.lib.mock_reset()
                self.fault('cache-call', call)
                self.lib.mock_start()
                self.assertEqual(self.s('cache-errors'), 1)
                self.assertNotEqual(self.lib.mock_stop(), 0)
                self.assert_stopped()

    def test_cache_failures_during_isr_or_rendering_stop_scanout(self):
        for where in ('isr', 'render'):
            with self.subTest(where=where):
                self.lib.mock_reset()
                self.lib.mock_start()
                self.fault('cache-call', self.s('cache-calls') + 1)
                if where == 'isr':
                    self.lib.mock_frame(2, 1016677, 0)
                else:
                    self.lib.mock_draw(1, 1100000)
                self.assertEqual(self.s('cache-errors'), 1)
                self.assertEqual(self.s('dma-starts'), 1)
                self.assertNotEqual(self.lib.mock_stop(), 0)
                self.assert_stopped()

    def test_interrupt_allocation_and_enable_failures_leave_no_handler(self):
        for failure in ('alloc', 'enable'):
            with self.subTest(failure=failure):
                self.lib.mock_reset()
                self.fault(failure)
                self.lib.mock_start()
                self.assertNotEqual(self.lib.mock_stop(), 0)
                self.assert_stopped()

    def test_disable_and_free_failures_latch_failure_and_allow_cleanup_retry(self):
        for failure in ('disable', 'free'):
            with self.subTest(failure=failure):
                self.lib.mock_reset()
                self.lib.mock_start()
                self.fault(failure)
                self.assertNotEqual(self.lib.mock_stop(), 0)
                self.fault(failure, 0)
                self.assertNotEqual(self.lib.mock_stop(), 0)
                self.assert_stopped()

    def test_stuck_dma_has_bounded_drain_and_prevents_handoff(self):
        self.lib.mock_start()
        self.fault('stuck')
        self.assertNotEqual(self.lib.mock_stop(), 0)
        self.assertLessEqual(self.s('clock') - 1000000, 110000)
        self.assertEqual(self.s('irq'), 0)
        self.assertEqual(self.s('propagation'), 0)
        self.assertEqual(self.s('dpi'), 0)
        self.assertEqual(self.s('fault'), 4)

    def test_display_only_diagnostic_refreshes_static_and_animated_screen(self):
        self.lib.mock_test()
        self.assertEqual(self.s('clock') - 1000000, 10000000)
        self.assertGreaterEqual(self.s('frames'), 590)
        self.assertLessEqual(self.s('draws'), 52)
        self.assertEqual(self.s('percent'), 100)
        self.assertLess(self.s('sync-bytes'), 900000)
        self.assertEqual(self.s('bad-cache'), 0)
        self.assert_stopped()

    def test_unavailable_display_is_a_noop(self):
        self.fault('ready', 0)
        self.lib.mock_start()
        self.lib.mock_test()
        self.assertEqual(self.lib.mock_stop(), 0)
        self.assertEqual(self.s('allocs'), 0)
        self.assertEqual(self.s('cache-calls'), 0)

    def test_built_interrupt_code_and_state_reside_in_internal_ram(self):
        elf = ROOT / 'build/loader/linux-loader.elf'
        if not elf.exists():
            self.skipTest('built loader ELF required')
        prefix = os.environ.get('CROSS_COMPILE', 'riscv32-esp-elf-')
        if not shutil.which(prefix + 'nm'):
            self.skipTest('ESP32-P4 cross tools required')
        output = subprocess.check_output([prefix + 'nm', '-n', str(elf)], text=True)
        symbols = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) == 3:
                symbols[parts[2]] = int(parts[0], 16)
        roots = ('progress_interrupt', 'progress_rearm', 'progress_quiesce', 'progress_observe_underrun')
        for name in (*roots, 'progress', 'esp_cache_msync', 'esp_timer_get_time'):
            self.assertIn(name, symbols)
            self.assertTrue(0x4ff00000 <= symbols[name] < 0x50000000, name)
        self.assertEqual(symbols['progress'] % 64, 0)
        for name in roots:
            dump = subprocess.check_output([prefix + 'objdump', '-d', '--disassemble=' + name, str(elf)], text=True)
            for target in re.findall(r'\b(?:jal|j)\s+([0-9a-f]+)\s+<', dump):
                address = int(target, 16)
                self.assertTrue(0x4fc00000 <= address < 0x4fd00000 or
                                0x4ff00000 <= address < 0x50000000, name + ' calls ' + target)

    def test_display_test_marker_is_strict_and_opened_read_only(self):
        text = (ROOT / 'main/main.c').read_text()
        parser = text[text.index('static int display_test_requested'):text.index('static void clear_appfs_selection')]
        harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *host_path;
static const char *TAG="display";
static FILE *test_open(const char *path,const char *mode)
{
    if(strcmp(path,"/sd/gaijin/display-test") || strcmp(mode,"rb")) abort();
    return fopen(host_path,mode);
}
#define fopen test_open
#define ESP_LOGW(tag,...) ((void)(tag))
'''
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'marker.c').write_text(harness + parser +
                '\nint requested(const char *path) {host_path=path;return display_test_requested();}\n')
            subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                            str(work / 'marker.c'), '-o', str(work / 'marker.so')], check=True)
            lib = C.CDLL(str(work / 'marker.so'))
            lib.requested.argtypes = [C.c_char_p]
            marker = work / 'display-test'
            self.assertEqual(lib.requested(os.fsencode(marker)), 0)
            for data, expected in ((b'gaijin-display-test-v1\n', 1), (b'', 0),
                                   (b'gaijin-display-test-v1', 0), (b'gaijin-display-test-v1\nx', 0),
                                   (b'gaijin-display-test-v1\n\0', 0), (b'ERASE\n', 0)):
                marker.write_bytes(data)
                self.assertEqual(lib.requested(os.fsencode(marker)), expected)
                self.assertEqual(marker.read_bytes(), data)

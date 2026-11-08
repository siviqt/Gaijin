"""Compile the shipped MCU poll path on the host and inject I2C snapshots/faults.

This checks protocol translation/recovery, not electrical timing or the P4.
"""
import ctypes as C
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from kernel_source import added_file

ROOT = Path(__file__).resolve().parents[1]


class KeyboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        header = added_file("drivers/input/keyboard/tanmatsu-keys.h")
        source = added_file("drivers/input/keyboard/tanmatsu-mcu.c")
        source = source[source.index('#define TANMATSU_FW'):source.index('/* Adapter locking')]
        (work / "tanmatsu-keys.h").write_text(header)
        harness = r'''
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <stdbool.h>
#include "tanmatsu-keys.h"
typedef uint8_t u8;
typedef uint16_t u16;
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define BIT(x) (1U << (x))
#define I2C_M_RD 1
#define dev_warn(...) ((void)0)
#define dev_info(...) ((void)0)
struct device {int unused;};
struct gpio_desc {int value;};
struct i2c_adapter {int unused;};
struct i2c_client {int addr; struct device dev; struct i2c_adapter *adapter;};
struct i2c_msg {int addr, flags; u16 len; u8 *buf;};
struct notifier_block {int unused;};
struct input_dev {void *data; int keys[KEY_MAX+1], switches[SW_MAX+1]; unsigned changes, syncs;};
static u8 sample[11], sampled_inputs;
static int fail_call, transfer_calls, transfer_result;
static void *input_get_drvdata(struct input_dev *dev) { return dev->data; }
static void input_report_key(struct input_dev *dev, unsigned key, int down) {
    if (dev->keys[key] != down) dev->changes++;
    dev->keys[key] = down;
}
static void input_report_switch(struct input_dev *dev, unsigned key, int down) {dev->switches[key] = down;}
static void input_sync(struct input_dev *dev) {dev->syncs++;}
static int gpiod_get_value_cansleep(struct gpio_desc *desc) {return desc->value;}
static int i2c_transfer(struct i2c_adapter *adapter, struct i2c_msg *msgs, int count) {
    transfer_calls++;
    if (transfer_calls == fail_call) return transfer_result;
    if (count != 2 || msgs[0].len != 1 || msgs[1].flags != I2C_M_RD) return -EINVAL;
    if (msgs[0].buf[0] == 0 && msgs[1].len == 11) memcpy(msgs[1].buf, sample, 11);
    else if (msgs[0].buf[0] == 15 && msgs[1].len == 1) msgs[1].buf[0] = sampled_inputs;
    else return -EINVAL;
    return 2;
}
'''
        harness += source
        harness += r'''
static struct input_dev input;
static struct tanmatsu_mcu mcu;
static struct i2c_client client;
static struct gpio_desc volume;
void reset(void) {
    memset(&input, 0, sizeof(input)); memset(&mcu, 0, sizeof(mcu));
    memset(sample, 0, sizeof(sample)); sample[0] = 9; sampled_inputs = 0;
    volume.value = 0; mcu.client = &client; mcu.volume_down = &volume; input.data = &mcu;
    fail_call = 0; transfer_calls = 0;
}
void bit(unsigned bit, int down) {
    if (down) sample[2 + bit/8] |= 1U << (bit%8);
    else sample[2 + bit/8] &= ~(1U << (bit%8));
}
void poll_sample(void) {transfer_calls = 0; tanmatsu_poll(&input);}
void fault(int call, int result) {fail_call = call; transfer_result = result;}
void firmware(unsigned version) {sample[0] = version; sample[1] = version >> 8;}
void inputs(unsigned value) {sampled_inputs = value;}
void vol(int value) {volume.value = value;}
int key(unsigned code) {return input.keys[code];}
int headphone(void) {return input.switches[SW_HEADPHONE_INSERT];}
unsigned changes(void) {return input.changes;}
unsigned errors(void) {return mcu.read_errors;}
unsigned syncs(void) {return input.syncs;}
'''
        (work / "poll.c").write_text(harness)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', '-O1',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-o', str(work / 'poll.so'), str(work / 'poll.c')], check=True)
        cls.lib = C.CDLL(str(work / 'poll.so'))
        defines = subprocess.check_output(
            ['cc', '-dM', '-E', '-include', 'linux/input-event-codes.h', '-'],
            input='', text=True)
        cls.codes = {name: int(value, 0) for name, value in re.findall(r'^#define\s+(KEY_\w+)\s+(0x[0-9a-fA-F]+|\d+)\s', defines, re.M)}

    def setUp(self):
        self.lib.reset()

    def key(self, name):
        return self.lib.key(self.codes['KEY_' + name])

    def test_every_physical_key_matches_bsp_bit_order(self):
        expected = '''ESC F1 F2 F3 GRAVE 1 2 3
            TAB Q W E FN A S D
            LEFTSHIFT Z X C LEFTCTRL LEFTMETA LEFTALT BACKSLASH
            4 5 6 7 R T Y U
            F G H J V B N M
            F4 F5 F6 BACKSPACE 9 0 MINUS EQUAL
            O P LEFTBRACE RIGHTBRACE L SEMICOLON APOSTROPHE ENTER
            DOT SLASH UP RIGHTSHIFT RIGHTALT LEFT DOWN RIGHT
            8 I K COMMA SPACE SPACE SPACE VOLUMEUP'''.split()
        self.assertEqual(len(expected), 72)
        for bit, name in enumerate(expected):
            with self.subTest(bit=bit, key=name):
                self.lib.reset()
                self.lib.bit(bit, 1)
                self.lib.poll_sample()
                self.assertEqual(self.key(name), 1)
                self.assertEqual(self.lib.changes(), 1)
                self.lib.bit(bit, 0)
                self.lib.poll_sample()
                self.assertEqual(self.key(name), 0)
                self.assertEqual(self.lib.changes(), 2)

    def test_modifiers_chords_duplicate_snapshots_and_space_aggregation(self):
        for bit in (16, 20, 13, 68, 69, 70):
            self.lib.bit(bit, 1)
        self.lib.poll_sample()
        for key in ('LEFTSHIFT', 'LEFTCTRL', 'A', 'SPACE'):
            self.assertEqual(self.key(key), 1)
        self.assertEqual(self.lib.changes(), 4)
        self.lib.poll_sample()
        self.assertEqual(self.lib.changes(), 4)
        for bit in (68, 69):
            self.lib.bit(bit, 0)
            self.lib.poll_sample()
            self.assertEqual(self.key('SPACE'), 1)
        self.lib.bit(70, 0)
        self.lib.poll_sample()
        self.assertEqual(self.key('SPACE'), 0)

    def test_i2c_errors_partial_reads_and_recovery_release_held_keys(self):
        for call, result in ((1, -110), (1, 1), (1, 0), (2, -5), (2, 1)):
            with self.subTest(call=call, result=result):
                self.lib.reset()
                self.lib.bit(13, 1)
                self.lib.bit(16, 1)
                self.lib.inputs(6)
                self.lib.vol(1)
                self.lib.poll_sample()
                self.assertEqual(self.key('A'), 1)
                self.assertEqual(self.key('POWER'), 1)
                self.assertEqual(self.lib.headphone(), 1)
                self.lib.fault(call, result)
                self.lib.poll_sample()
                for key in ('A', 'LEFTSHIFT', 'POWER', 'VOLUMEDOWN'):
                    self.assertEqual(self.key(key), 0)
                self.assertEqual(self.lib.headphone(), 0)
                self.assertEqual(self.lib.errors(), 1)
                self.lib.fault(0, 0)
                self.lib.poll_sample()
                self.assertEqual(self.key('A'), 1)
                self.lib.bit(13, 0)
                self.lib.poll_sample()
                self.assertEqual(self.key('A'), 0)

    def test_firmware_change_and_gpio_errors_never_emit_partial_sample(self):
        for version in (0, 8, 10, 265):
            self.lib.firmware(version)
            self.lib.bit(13, 1)
            self.lib.poll_sample()
            self.assertEqual(self.key('A'), 0)
        self.lib.firmware(9)
        self.lib.vol(-5)
        self.lib.poll_sample()
        self.assertEqual(self.key('A'), 0)
        self.lib.vol(0)
        self.lib.poll_sample()
        self.assertEqual(self.key('A'), 1)
        self.assertEqual(self.lib.errors(), 5)

    def test_diagnostic_arguments_and_missing_device(self):
        work = Path(self.temp.name)
        exe = work / 'input-test'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-o', str(exe),
                        str(ROOT / 'userspace/package/gaijin-input-test/input-test.c')], check=True)
        for seconds in ('0', '3601', '-1', 'xyz', ''):
            result = subprocess.run([str(exe), '/dev/null', seconds], capture_output=True)
            self.assertEqual(result.returncode, 2)
        result = subprocess.run([str(exe), '/dev/null', '1'], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'keyboard not found', result.stderr)


class GPIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        source = added_file('drivers/gpio/gpio-tanmatsu.c')
        source = source[source.index('struct tanmatsu_gpio'):source.index('static int tanmatsu_gpio_probe')]
        harness = r'''
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define __iomem
#define BIT(x) (1U << (x))
struct gpio_chip {void *data;};
static void *gpiochip_get_data(struct gpio_chip *chip) {return chip->data;}
static u32 readl(void *address) {return *(u32 *)address;}
static void writel(u32 value, void *address) {*(u32 *)address = value;}
static void bitmap_zero(unsigned long *mask, unsigned count) {memset(mask, 0, ((count+63)/64)*8);}
static void __set_bit(unsigned bit, unsigned long *mask) {mask[bit/64] |= 1UL << (bit%64);}
'''
        harness += source
        harness += r'''
static u32 gpio[0x800/4], mux[0x100/4], lp[0x54/4];
static struct tanmatsu_gpio g;
void reset(void) {
    memset(gpio, 0, sizeof(gpio)); memset(mux, 0xff, sizeof(mux)); memset(lp, 0xff, sizeof(lp));
    g.gpio = gpio; g.mux = mux; g.lp_mux = lp; g.chip.data = &g;
}
int request(unsigned pin) {return tanmatsu_gpio_request(&g.chip, pin);}
int output(unsigned pin, int value) {return tanmatsu_gpio_output(&g.chip, pin, value);}
int set(unsigned pin, int value) {return tanmatsu_gpio_set(&g.chip, pin, value);}
int input(unsigned pin) {return tanmatsu_gpio_input(&g.chip, pin);}
int direction(unsigned pin) {return tanmatsu_gpio_direction(&g.chip, pin);}
int get(unsigned pin) {return tanmatsu_gpio_get(&g.chip, pin);}
void release(unsigned pin) {tanmatsu_gpio_free(&g.chip, pin);}
u32 reg(unsigned offset) {return gpio[offset/4];}
void setreg(unsigned offset, u32 value) {gpio[offset/4] = value;}
u32 muxreg(unsigned pin) {return mux[pin+1];}
u32 lpreg(unsigned pin) {return lp[pin+2];}
int valid(unsigned pin) {
    unsigned long mask[1]; tanmatsu_gpio_valid(&g.chip, mask, 36);
    return !!(mask[0] & (1UL << pin));
}
'''
        (work / 'gpio.c').write_text(harness)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', '-O1',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-o', str(work / 'gpio.so'), str(work / 'gpio.c')], check=True)
        cls.lib = C.CDLL(str(work / 'gpio.so'))
        for name in ('reg', 'muxreg', 'lpreg'):
            getattr(cls.lib, name).restype = C.c_uint32

    def setUp(self):
        self.lib.reset()

    def test_only_required_pins_are_exposed(self):
        self.assertEqual([pin for pin in range(36) if self.lib.valid(pin)], [9, 10, 35])
        self.assertEqual(self.lib.output(35, 1), -22)
        self.assertEqual(self.lib.set(35, 1), -22)
        self.assertEqual(self.lib.reg(0x30), 0)

    def test_pin_request_releases_output_and_selects_hp_gpio(self):
        for pin in (9, 10, 35):
            self.lib.request(pin)
            self.assertEqual(self.lib.reg(0x28 if pin < 32 else 0x34), 1 << (pin % 32))
            self.assertEqual(self.lib.reg(0x558 + 4 * pin), 256 | (1 << 10))
            self.assertEqual(self.lib.reg(0x74 + 4 * pin), 0)
            mask = (7 << 12) | (1 << 9) | (1 << 8) | (1 << 7) | (1 << 1)
            self.assertEqual(self.lib.muxreg(pin) & mask, (1 << 12) | (1 << 9) | (1 << 8))
            if pin < 16:
                self.assertEqual(self.lib.lpreg(pin) & (1 << 4), 0)
        self.assertEqual(self.lib.muxreg(11), 0xffffffff)
        self.assertEqual(self.lib.lpreg(8), 0xffffffff)

    def test_i2c_direction_and_input_bank_offsets(self):
        self.lib.output(9, 0)
        self.assertEqual(self.lib.reg(0xc), 1 << 9)
        self.assertEqual(self.lib.reg(0x24), 1 << 9)
        self.lib.set(10, 1)
        self.assertEqual(self.lib.reg(0x8), 1 << 10)
        self.lib.input(9)
        self.assertEqual(self.lib.reg(0x28), 1 << 9)
        self.lib.setreg(0x20, 1 << 9)
        self.assertEqual(self.lib.direction(9), 0)
        self.assertEqual(self.lib.direction(10), 1)
        self.lib.setreg(0x3c, 1 << 10)
        self.lib.setreg(0x40, 1 << 3)
        self.assertEqual(self.lib.get(9), 0)
        self.assertEqual(self.lib.get(10), 1)
        self.assertEqual(self.lib.get(35), 1)
        self.lib.release(35)
        self.assertEqual(self.lib.reg(0x34), 1 << 3)

"""Execute shipped refresh/backlight code with DMA and I2C fault injection.

Host checks do not emulate the DSI PHY, panel orientation or bus bandwidth.
"""
import ctypes as C
from pathlib import Path
import subprocess
import tempfile
import unittest
from kernel_source import added_file


class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        driver = added_file('drivers/video/fbdev/tanmatsu-fb.c')
        driver = driver[driver.index('#define FB_BASE'):driver.index('static void tanmatsu_fb_destroy')]
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef int atomic_t;
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a) < (b) ? (a) : (b))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define DMA_TO_DEVICE 1
#define DMA_FROM_DEVICE 2
#define FB_VMODE_NONINTERLACED 0
#define IRQ_NONE 0
#define IRQ_HANDLED 1
typedef int irqreturn_t;
#define dev_err_ratelimited(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
#define to_delayed_work(w) container_of(w,struct delayed_work,work)
struct fb_bitfield { unsigned offset, length, msb_right; };
struct fb_var_screeninfo {
    unsigned xres,yres,xres_virtual,yres_virtual,bits_per_pixel,xoffset,yoffset,nonstd,grayscale,vmode;
    struct fb_bitfield red,green,blue,transp;
};
struct fb_info { void *par; struct fb_var_screeninfo var; };
static unsigned cpu_calls, device_calls, submitted, scheduled;
static uint32_t sync_address, sync_size, largest_sync, sync_gaps;
static void arch_sync_dma_for_cpu(uint32_t address, size_t size, int dir) {
    cpu_calls++; sync_address=address; sync_size=size;
}
static void arch_sync_dma_for_device(uint32_t address, size_t size, int dir) {
    if (device_calls && address != sync_address + sync_size)
        sync_gaps++;
    if (size > largest_sync)
        largest_sync = size;
    device_calls++; sync_address=address; sync_size=size;
}
static uint32_t readl(void *address) { return *(u32 *)address; }
static void writel(u32 value, void *address) { *(u32 *)address=value; }
static void wmb(void) { submitted++; }
static void atomic_inc(atomic_t *n) { (*n)++; }
static unsigned msecs_to_jiffies(unsigned n) {return n;}
static void schedule_delayed_work(struct delayed_work *w,unsigned n) { scheduled=n; }
'''
        harness += driver
        harness += r'''
static u32 dma[0x1000/4], bridge[0x100/4], desc[16];
static struct tanmatsu_fb fb;
static struct fb_info info;
void reset(void) {
    memset(&fb,0,sizeof(fb)); memset(dma,0,sizeof(dma)); memset(bridge,0,sizeof(bridge));
    memset(desc,0,sizeof(desc)); memset(&info,0,sizeof(info));
    fb.dma=dma; fb.bridge=bridge; fb.desc=desc; fb.info=&info; info.par=&fb;
    fb.descriptor[0]=FB_BASE; fb.descriptor[2]=0x50105000;
    fb.descriptor[4]=FB_BYTES/8-1; fb.descriptor[9]=BIT(30)|BIT(31);
    cpu_calls=device_calls=submitted=scheduled=0;
    largest_sync=sync_gaps=0;
}
void start(void) { tanmatsu_fb_start(&fb); }
int interrupt(unsigned status) { desc[9] &= ~BIT(31); dma[DMA_STATUS/4]=status; return tanmatsu_fb_irq(0,&fb); }
void flush(int underrun) { bridge[0x58/4]=underrun; tanmatsu_fb_flush(&fb.flush.work); }
unsigned reg(unsigned off) { return dma[off/4]; }
unsigned descriptor(unsigned n) { return desc[n]; }
unsigned stat(unsigned n) {
    unsigned values[]={cpu_calls,device_calls,submitted,scheduled,fb.frames,fb.errors,
                       sync_address,sync_size,fb.underruns,largest_sync,sync_gaps};
    return values[n];
}
int mode(unsigned field) {
    struct fb_var_screeninfo var={.xres=480,.yres=800,.xres_virtual=480,.yres_virtual=800,.bits_per_pixel=16};
    if (field==1) var.xres=800;
    if (field==2) var.xres_virtual=960;
    if (field==3) var.bits_per_pixel=32;
    if (field==4) var.xoffset=1;
    if (field==5) var.yoffset=1;
    if (field==6) var.vmode=1;
    if (field==7) var.nonstd=1;
    if (field==8) var.grayscale=1;
    return tanmatsu_fb_check_var(&var,&info);
}
int color(unsigned reg,unsigned r,unsigned g,unsigned b) { return tanmatsu_fb_setcolreg(reg,r,g,b,0,&info); }
unsigned palette(unsigned reg) { return fb.palette[reg]; }
'''
        (work / 'display.c').write_text(harness)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', '-O1',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-o', str(work / 'display.so'), str(work / 'display.c')], check=True)
        cls.lib = C.CDLL(str(work / 'display.so'))
        for name in ('stat', 'reg', 'descriptor', 'palette'):
            getattr(cls.lib, name).restype = C.c_uint32

    def setUp(self):
        self.lib.reset()

    def test_first_start_and_repeated_completed_frames_restore_dma_descriptor(self):
        self.lib.start()
        for frame in range(1, 101):
            self.assertEqual(self.lib.reg(0x18), 0x101)
            self.assertEqual(self.lib.reg(0x128), 0x48ebb801)
            self.assertEqual(self.lib.descriptor(0), 0x48e00000)
            self.assertEqual(self.lib.descriptor(9), 0xc0000000)
            self.assertEqual(self.lib.interrupt(2), 1)
            self.assertEqual(self.lib.stat(4), frame)
        self.assertEqual(self.lib.stat(0), 101)  # descriptor DMA write invalidated
        self.assertEqual(self.lib.stat(1), 101)  # restored descriptor published
        self.assertEqual(self.lib.stat(2), 101)  # ordered before channel enable
        self.assertEqual(self.lib.stat(6), 0x48ebb800)
        self.assertEqual(self.lib.stat(7), 64)

    def test_spurious_irq_and_dma_errors_never_restart_failed_transfer(self):
        self.assertEqual(self.lib.interrupt(0), 0)
        self.assertEqual(self.lib.stat(2), 0)
        for bit in range(5, 14):
            self.lib.reset()
            self.assertEqual(self.lib.interrupt((1 << bit) | 2), 1)
            self.assertEqual(self.lib.reg(0x190), 0)
            self.assertEqual(self.lib.reg(0x18), 0x100)
            self.assertEqual(self.lib.stat(2), 0)
            self.assertEqual(self.lib.stat(5), 1)
            self.assertEqual(self.lib.stat(4), 0)

    def test_periodic_flush_covers_pixels_and_tracks_underruns(self):
        self.lib.flush(0)
        self.assertEqual(self.lib.stat(1), 188)
        self.assertEqual(self.lib.stat(6) + self.lib.stat(7), 0x48e00000 + 768000)
        self.assertEqual(self.lib.stat(9), 4096)
        self.assertEqual(self.lib.stat(10), 0)
        self.assertEqual(self.lib.stat(3), 50)
        self.assertEqual(self.lib.stat(8), 0)
        self.lib.flush(1)
        self.assertEqual(self.lib.stat(8), 1)

    def test_only_fixed_rgb565_mode_and_bounded_palette(self):
        self.assertEqual(self.lib.mode(0), 0)
        for field in range(1, 9):
            self.assertEqual(self.lib.mode(field), -22)
        for slot, rgb, packed in ((0, (65535, 0, 0), 0xf800),
                                  (1, (0, 65535, 0), 0x07e0),
                                  (15, (0, 0, 65535), 0x001f)):
            self.assertEqual(self.lib.color(slot, *rgb), 0)
            self.assertEqual(self.lib.palette(slot), packed)
        self.assertEqual(self.lib.color(16, 0, 0, 0), -22)


class BacklightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        driver = added_file('drivers/input/keyboard/tanmatsu-mcu.c')
        read = driver[driver.index('static int tanmatsu_read'):driver.index('static void tanmatsu_release')]
        backlight = driver[driver.index('/* Adapter locking'):driver.index('static const struct backlight_ops')]
        harness = r'''
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define TANMATSU_FW 9
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define I2C_M_RD 1
struct i2c_client {int addr; void *adapter;};
struct i2c_msg { int addr, flags; unsigned len; u8 *buf; };
struct backlight_device { struct i2c_client *client; unsigned brightness, blank; };
static struct i2c_client client;
static struct backlight_device bl={.client=&client};
static unsigned version, raw, writes, written_reg, written_value;
static int read_result, write_result;
static void *bl_get_data(struct backlight_device *b) { return b->client; }
static unsigned backlight_get_brightness(struct backlight_device *b) { return b->blank ? 0 : b->brightness; }
static int i2c_transfer(void *adapter, struct i2c_msg *msgs, unsigned count) {
    if (read_result != 2) return read_result;
    if (msgs[0].buf[0]==0 && msgs[1].len==2) {
        msgs[1].buf[0]=version; msgs[1].buf[1]=version>>8;
    } else if (msgs[0].buf[0]==11 && msgs[1].len==1) msgs[1].buf[0]=raw;
    else return -EINVAL;
    return 2;
}
static int i2c_master_send(struct i2c_client *c,u8 *buf,unsigned len) {
    writes++; written_reg=buf[0]; written_value=buf[1]; return write_result;
}
'''
        harness += read + backlight
        harness += r'''
void reset(void) {version=9; raw=160; writes=0; read_result=write_result=2;}
void firmware(unsigned v) {version=v;}
void fault(int rd,int wr) {read_result=rd; write_result=wr;}
int update(unsigned brightness,unsigned blank) {bl.brightness=brightness; bl.blank=blank; return tanmatsu_backlight_update(&bl);}
int get(void) {return tanmatsu_backlight_get(&bl);}
unsigned stat(unsigned n) {unsigned a[]={writes,written_reg,written_value}; return a[n];}
'''
        (work / 'backlight.c').write_text(harness)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', '-O1',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-o', str(work / 'backlight.so'), str(work / 'backlight.c')], check=True)
        cls.lib = C.CDLL(str(work / 'backlight.so'))

    def setUp(self):
        self.lib.reset()

    def test_brightness_blanking_and_actual_readback(self):
        for brightness in (0, 1, 160, 255):
            self.assertEqual(self.lib.update(brightness, 0), 0)
            self.assertEqual(self.lib.stat(1), 11)
            self.assertEqual(self.lib.stat(2), brightness)
        self.assertEqual(self.lib.update(255, 1), 0)
        self.assertEqual(self.lib.stat(2), 0)
        self.assertEqual(self.lib.get(), 160)

    def test_firmware_rechecked_before_every_write(self):
        for firmware in (0, 8, 10, 265):
            self.lib.firmware(firmware)
            self.assertEqual(self.lib.update(100, 0), -93)
            self.assertEqual(self.lib.stat(0), 0)
        self.lib.firmware(9)
        self.assertEqual(self.lib.update(100, 0), 0)

    def test_short_or_failed_i2c_reads_and_writes_propagate_errors(self):
        for result in (0, 1, -110, -5):
            self.lib.reset()
            self.lib.fault(result, 2)
            self.assertEqual(self.lib.update(100, 0), result if result < 0 else -5)
            self.assertEqual(self.lib.stat(0), 0)
            self.assertEqual(self.lib.get(), result if result < 0 else -5)
            self.lib.reset()
            self.lib.fault(2, result)
            self.assertEqual(self.lib.update(100, 0), result if result < 0 else -5)

"""Run the actual read policy and DMA/cache functions with native simulators."""
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest
from kernel_source import added_file

ROOT = Path(__file__).resolve().parents[1]


def driver_source():
    return added_file("drivers/mmc/host/tanmatsu-sd.c")


class SDDriverTests(unittest.TestCase):
    def test_write_gate_is_empty_then_frozen_and_rejects_invalid_ranges(self):
        source = driver_source()
        helper = source[source.index("static bool tanmatsu_sd_writable"):source.index("static DEVICE_ATTR_WO")]
        program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define U32_MAX UINT32_MAX
#define SD_MAX_RANGES 64
#define CAP_SYS_RAWIO 17
struct mutex { int unused; };
struct mmc_card { bool blockaddr; };
struct tanmatsu_sd_range { u64 start, count; };
struct tanmatsu_sd;
struct mmc_host { struct mmc_card *card; struct tanmatsu_sd *data; };
struct tanmatsu_sd { struct mmc_host *mmc; struct mutex range_lock; unsigned nr_ranges; struct tanmatsu_sd_range ranges[64]; };
struct device { struct mmc_host *mmc; };
struct device_attribute { int unused; };
static int privileged=1;
static bool capable(int cap) { assert(cap==17); return privileged; }
static void *dev_get_drvdata(struct device *dev) { return dev->mmc; }
static void *mmc_priv(struct mmc_host *mmc) { return mmc->data; }
static void mutex_lock(struct mutex *m) { (void)m; }
static void mutex_unlock(struct mutex *m) { (void)m; }
static bool mmc_card_is_blockaddr(struct mmc_card *c) { return c->blockaddr; }
''' + helper.replace("    struct device_attribute *attr", "    struct device_attribute *attr")
        # The kernel callback ABI requires an unused attribute argument.
        program = program.replace("struct tanmatsu_sd_range ranges[SD_MAX_RANGES];", "struct tanmatsu_sd_range ranges[SD_MAX_RANGES];\n\t(void)attr;")
        program += r'''
int main(void) {
    struct mmc_card card={.blockaddr=true};
    struct mmc_host mmc={.card=&card};
    struct tanmatsu_sd h={.mmc=&mmc}; mmc.data=&h;
    struct device dev={.mmc=&mmc};
    assert(!tanmatsu_sd_writable(&h,100));
    const char *invalid[]={"", "0 1", "1 0", "4294967295 1", "4294967296 1", "10 10\n15 2", "10 2 garbage", "-1 2"};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++) {
        assert(write_ranges_store(&dev,NULL,invalid[i],strlen(invalid[i]))==-EINVAL);
        assert(h.nr_ranges==0);
    }
    const char *good="100 8\n200 1\n";
    privileged=0; assert(write_ranges_store(&dev,NULL,good,strlen(good))==-EPERM);
    privileged=1; assert(write_ranges_store(&dev,NULL,good,strlen(good))==(ssize_t)strlen(good));
    assert(h.nr_ranges==2);
    for(unsigned i=90;i<210;i++)
        assert(tanmatsu_sd_writable(&h,i)==((i>=100 && i<108) || i==200));
    /* Re-registration of the exact same image is safe after a clean stop. */
    assert(write_ranges_store(&dev,NULL,good,strlen(good))==(ssize_t)strlen(good));
    const char *changed[]={"1 10", "100 9\n200 1", "100 7\n200 1", "100 8", "100 8\n200 1\n300 1"};
    for(unsigned i=0;i<sizeof(changed)/sizeof(changed[0]);i++)
        assert(write_ranges_store(&dev,NULL,changed[i],strlen(changed[i]))==-EBUSY);
    assert(h.nr_ranges==2 && h.ranges[0].start==100);
    card.blockaddr=false;
    assert(tanmatsu_sd_writable(&h,100*512));
    assert(!tanmatsu_sd_writable(&h,100*512+1));
    mmc.card=NULL; assert(!tanmatsu_sd_writable(&h,100));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            c, exe = Path(temp)/"ranges.c", Path(temp)/"ranges"
            c.write_text(program)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

    def test_probe_shares_gpio_iomux_and_preserves_resource_errors(self):
        source = driver_source()
        probe = source[source.index("static int tanmatsu_sd_probe"):source.index("\tvalue = readl(power);")]
        probe += "\t(void)value; (void)pin; (void)mux; (void)gpio; mmc_free_host(mmc); return 0;\nfree_host:\n\tmmc_free_host(mmc); return err;\n}\n"
        prefix = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
typedef uint32_t u32;
#define __iomem
#define IORESOURCE_MEM 0x200
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
struct device { int unused; };
struct resource { uintptr_t start, end; };
struct platform_device { struct device dev; struct resource regs[4]; };
struct mmc_host;
struct tanmatsu_sd { struct mmc_host *mmc; void *regs; };
struct mmc_host { struct tanmatsu_sd data; };
static struct resource reserved[16];
static int nr_reserved, fail_index, map_count, frees, missing_mux;
static uintptr_t fail_map;
static int resource_size(struct resource *r) { return r->end - r->start + 1; }
static struct resource *platform_get_resource(struct platform_device *p, int type, unsigned index)
{ assert(type == IORESOURCE_MEM); return missing_mux && index == 3 ? NULL : &p->regs[index]; }
static struct mmc_host *mmc_alloc_host(unsigned bytes, struct device *d)
{ (void)bytes; (void)d; return calloc(1, sizeof(struct mmc_host)); }
static struct tanmatsu_sd *mmc_priv(struct mmc_host *h) { return &h->data; }
static void mmc_free_host(struct mmc_host *h) { frees++; free(h); }
static void *devm_ioremap(struct device *d, uintptr_t start, unsigned size)
{ (void)d; assert(size); map_count++; return start == fail_map ? NULL : (void *)start; }
static void *devm_platform_ioremap_resource(struct platform_device *p, unsigned index)
{
    if ((int)index == fail_index) return ERR_PTR(-EACCES);
    struct resource r = p->regs[index];
    for (int i = 0; i < nr_reserved; i++)
        if (r.start <= reserved[i].end && r.end >= reserved[i].start) return ERR_PTR(-EBUSY);
    reserved[nr_reserved++] = r;
    return (void *)r.start;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            dtb = Path(temp) / "board.dtb"
            subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb),
                            str(ROOT / "linux/tanmatsu.dts")], check=True)

            def resources(node):
                values = subprocess.check_output(["fdtget", "-t", "x", str(dtb), node, "reg"], text=True)
                numbers = [int(value, 16) for value in values.split()]
                return ",".join(f"{{0x{a:x},0x{a+b-1:x}}}" for a, b in zip(numbers[::2], numbers[1::2]))

            main = r'''
static struct resource gpio_resources[] = { GPIO_RESOURCES };
static void reset(void)
{
    nr_reserved = sizeof(gpio_resources) / sizeof(gpio_resources[0]);
    for (int i = 0; i < nr_reserved; i++) reserved[i] = gpio_resources[i];
    fail_index = -1; fail_map = 0; map_count = frees = missing_mux = 0;
}
int main(void)
{
    struct platform_device p = { .regs = { SD_RESOURCES } };
    reset();
    /* This is the exact overlap that rejected the first device build. */
    assert(PTR_ERR(devm_platform_ioremap_resource(&p, 3)) == -EBUSY);
    assert(tanmatsu_sd_probe(&p) == 0);
    assert(map_count == 2 && frees == 1); /* Shared IOMUX and GPIO maps. */
    for (int i = 0; i < 3; i++) {
        reset(); fail_index = i;
        assert(tanmatsu_sd_probe(&p) == -EACCES);
        assert(frees == 1);
    }
    reset(); fail_map = p.regs[3].start;
    assert(tanmatsu_sd_probe(&p) == -ENOMEM && frees == 1);
    reset(); fail_map = gpio_resources[0].start;
    assert(tanmatsu_sd_probe(&p) == -ENOMEM && frees == 1);
    reset(); missing_mux = 1;
    assert(tanmatsu_sd_probe(&p) == -EINVAL && frees == 1);
    return 0;
}
'''.replace("GPIO_RESOURCES", resources("/soc/gpio@500e0000")).replace("SD_RESOURCES", resources("/soc/mmc@50083000"))
            c = Path(temp) / "probe.c"
            exe = Path(temp) / "probe"
            c.write_text(prefix + probe + main)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

    def test_page_request_limits_match_pinned_block_layer(self):
        kernel = ROOT / "build/linux-6.18.35"
        if not kernel.exists():
            self.skipTest("pinned Linux source is not prepared")
        source = driver_source()
        limits = source[source.index("\tmmc->max_segs ="):source.index("\tplatform_set_drvdata(pdev, mmc);")]
        queue = (kernel / "drivers/mmc/core/queue.c").read_text()
        sector_limit = re.search(r"\tlim.max_hw_sectors = .*;", queue)[0]
        settings = (kernel / "block/blk-settings.c").read_text()
        sector_check = settings[settings.index("\tif (WARN_ON_ONCE(lim->max_hw_sectors < PAGE_SECTORS))"):settings.index("\tlogical_block_sectors =")]
        segment_check = settings[settings.index("\t\tif (WARN_ON_ONCE(lim->max_segment_size < BLK_MIN_SEGMENT_SIZE))"):settings.index("\n\t}\n", settings.index("\t\tif (WARN_ON_ONCE(lim->max_segment_size < BLK_MIN_SEGMENT_SIZE))"))]
        prefix = r'''
#include <assert.h>
#include <errno.h>
#define PAGE_SIZE 4096
#define PAGE_SECTORS (PAGE_SIZE / 512)
#define BLK_MIN_SEGMENT_SIZE PAGE_SIZE
#define SD_MAX_BLOCKS (PAGE_SIZE / 512)
#define WARN_ON_ONCE(x) (x)
#define min(a, b) ((a) < (b) ? (a) : (b))
struct mmc_host { unsigned max_segs, max_seg_size, max_req_size, max_blk_size, max_blk_count; };
struct queue_limits { unsigned max_hw_sectors, max_segment_size; };
'''
        program = prefix + "static void setup(struct mmc_host *mmc) {\n" + limits + "}\n"
        program += "static int validate(struct queue_limits *lim) {\n" + textwrap.dedent(sector_check) + textwrap.dedent(segment_check) + "\nreturn 0; }\n"
        program += "int main(void) { struct mmc_host h, *host = &h; struct queue_limits lim; setup(host);\n"
        program += sector_limit + "\nlim.max_segment_size = host->max_seg_size;\n"
        program += r'''
assert(host->max_segs == 1 && host->max_blk_size == 512);
assert(host->max_blk_count == 8 && host->max_req_size == 4096);
assert(validate(&lim) == 0);
lim.max_hw_sectors = 1; assert(validate(&lim) == -EINVAL);
lim.max_hw_sectors = 8; lim.max_segment_size = 512; assert(validate(&lim) == -EINVAL);
return 0; }
'''
        with tempfile.TemporaryDirectory() as temp:
            c = Path(temp) / "limits.c"
            exe = Path(temp) / "limits"
            c.write_text(program)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

    def test_page_reads_split_into_bounded_sector_commands(self):
        source = driver_source()
        helper = source[source.index("static int tanmatsu_sd_read_request"):source.index("static void tanmatsu_sd_work")]
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
#define U32_MAX UINT32_MAX
#define SD_MAX_BLOCKS 8
#define MMC_READ_SINGLE_BLOCK 17
#define MMC_READ_MULTIPLE_BLOCK 18
#define MMC_WRITE_BLOCK 24
#define MMC_WRITE_MULTIPLE_BLOCK 25
#define MMC_DATA_WRITE (1U << 8)
#define MMC_STOP_TRANSMISSION 12
#define MMC_DATA_READ (1U << 9)
#define R1_STATUS(x) ((x) & 0xFFF9A000)
struct mmc_data { unsigned flags, blocks, blksz, bytes_xfered; unsigned char *sg; };
struct mmc_command { unsigned opcode, arg, resp[4]; int error; struct mmc_data *data; };
struct mmc_request { struct mmc_command *cmd, *sbc, *stop; struct mmc_data *data; };
struct mmc_card { bool blockaddr; };
struct mmc_host { struct mmc_card *card; };
struct tanmatsu_sd { struct mmc_host *mmc; };
static bool mmc_card_is_blockaddr(struct mmc_card *card) { return card->blockaddr; }
static unsigned calls, fail_at, status_at, arguments[8];
/* The DMA path is tested separately; model one physical CMD17 here. */
static int tanmatsu_sd_transfer(struct tanmatsu_sd *h, struct mmc_command *cmd, unsigned offset)
{
    (void)h;
    assert(cmd->opcode == (cmd->data->flags == MMC_DATA_WRITE ? MMC_WRITE_BLOCK : MMC_READ_SINGLE_BLOCK));
    assert(cmd->data->blocks == 1 && cmd->data->blksz == 512);
    assert(cmd->data->flags == MMC_DATA_READ || cmd->data->flags == MMC_DATA_WRITE);
    assert(calls < 8);
    arguments[calls++] = cmd->arg;
    if (calls == fail_at) return -ETIMEDOUT;
    memset(cmd->data->sg + offset, calls, 512);
    cmd->resp[0] = calls == status_at ? (1U << 31) : 0;
    cmd->data->bytes_xfered = 512;
    return 0;
}
'''
        main = r'''
int main(void)
{
    struct mmc_card card = { .blockaddr = true };
    struct mmc_host mmc = { .card = &card };
    struct tanmatsu_sd h = { .mmc = &mmc };
    unsigned char buffer[4097];
    struct mmc_data data = { .flags = MMC_DATA_READ, .blocks = 8, .blksz = 512, .sg = buffer };
    struct mmc_command cmd = { .opcode = MMC_READ_MULTIPLE_BLOCK, .arg = 100, .data = &data };
    struct mmc_command stop = { .opcode = MMC_STOP_TRANSMISSION };
    struct mmc_request mrq = { .cmd = &cmd, .data = &data, .stop = &stop };
    for (unsigned writing = 0; writing <= 1; writing++) {
    cmd.opcode = writing ? MMC_WRITE_MULTIPLE_BLOCK : MMC_READ_MULTIPLE_BLOCK;
    data.flags = writing ? MMC_DATA_WRITE : MMC_DATA_READ;
    for (int blockaddr = 0; blockaddr <= 1; blockaddr++) {
        card.blockaddr = blockaddr;
        for (unsigned blocks = 2; blocks <= 8; blocks++) {
            calls = 0; data.blocks = blocks; data.bytes_xfered = 0;
            stop.error = -EIO; stop.resp[0] = UINT32_MAX;
            memset(buffer, 0xee, sizeof(buffer));
            assert(tanmatsu_sd_read_request(&h, &mrq) == 0);
            assert(calls == blocks && data.bytes_xfered == blocks * 512);
            assert(!stop.error && !stop.resp[0]);
            assert(cmd.opcode == (writing ? MMC_WRITE_MULTIPLE_BLOCK : MMC_READ_MULTIPLE_BLOCK) && cmd.arg == 100);
            for (unsigned i = 0; i < blocks; i++) {
                assert(arguments[i] == 100 + i * (blockaddr ? 1 : 512));
                for (unsigned j = 0; j < 512; j++) assert(buffer[i * 512 + j] == i + 1);
            }
            assert(buffer[blocks * 512] == 0xee);
        }
    }
    }
    cmd.opcode = MMC_READ_MULTIPLE_BLOCK; data.flags = MMC_DATA_READ;
    /* Fail at every sector; never credit a partially completed page. */
    for (unsigned fault = 1; fault <= 8; fault++) {
        calls = 0; fail_at = fault; data.bytes_xfered = 4096;
        assert(tanmatsu_sd_read_request(&h, &mrq) == -ETIMEDOUT);
        assert(calls == fault && !data.bytes_xfered);
    }
    fail_at = 0; calls = 0; status_at = 3;
    assert(tanmatsu_sd_read_request(&h, &mrq) == -EIO);
    assert(calls == 3 && !data.bytes_xfered);
    status_at = 0;
    calls = 0; data.blocks = 9;
    assert(tanmatsu_sd_read_request(&h, &mrq) == -EINVAL && !calls);
    data.blocks = 8; data.flags = 1U << 8;
    assert(tanmatsu_sd_read_request(&h, &mrq) == -EROFS && !calls);
    data.flags = MMC_DATA_READ; cmd.arg = UINT32_MAX;
    assert(tanmatsu_sd_read_request(&h, &mrq) == -EINVAL && !calls);
    cmd.arg = 100; data.blksz = 64;
    assert(tanmatsu_sd_read_request(&h, &mrq) == -EINVAL && !calls);
    data.blksz = 512; mrq.sbc = &stop;
    assert(tanmatsu_sd_read_request(&h, &mrq) == -EOPNOTSUPP && !calls);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            c = Path(temp) / "page.c"
            exe = Path(temp) / "page"
            c.write_text(prefix + helper + main)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

    def test_dma_cache_ownership_completion_and_faults(self):
        self.check_dma_fixture(driver_source(), False)
        no_initial_reset = driver_source().replace(
            "err = tanmatsu_sd_dma_halt(h);\n\t\tif (err)\n\t\t\treturn err;\n\t\terr = tanmatsu_sd_reset(h, SDMMC_CTRL_FIFO_RESET);",
            "err = tanmatsu_sd_reset(h, SDMMC_CTRL_FIFO_RESET);")
        self.check_dma_fixture(no_initial_reset, True, "!engine_needs_reset")
        no_coherence_guard = driver_source().replace("if (!dma_need_sync(dev, h->desc_dma))", "if (false)")
        self.check_dma_fixture(no_coherence_guard, True, "tanmatsu_sd_dma_map(&h, &dev) == -EINVAL")

    def test_device_tree_selects_noncoherent_dma_with_pinned_of_code(self):
        kernel = ROOT / "build/linux-6.18.35"
        if not kernel.exists():
            self.skipTest("pinned Linux source is not prepared")
        source = (kernel / "drivers/of/address.c").read_text()
        helper = source[source.index("bool of_dma_is_coherent("):source.index("EXPORT_SYMBOL_GPL(of_dma_is_coherent)")]
        self.assertIn("CONFIG_ARCH_DMA_DEFAULT_COHERENT=y", (ROOT / "build/kernel/.config").read_text())
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#define __free(x)
struct device_node { const char *property; struct device_node *parent; };
static bool dma_default_coherent = true; /* Pinned RISC-V config. */
static struct device_node *of_node_get(struct device_node *n) { return n; }
static struct device_node *of_get_next_dma_parent(struct device_node *n) { return n->parent; }
static bool of_property_read_bool(struct device_node *n, const char *p)
{ return n->property && !strcmp(n->property, p); }
'''
        main = r'''
int main(void) {
    struct device_node parent = {0}, sd = {0, &parent};
    assert(of_dma_is_coherent(&sd)); /* Missing declaration skips cache sync. */
    sd.property = "dma-noncoherent"; assert(!of_dma_is_coherent(&sd));
    parent.property = "dma-coherent"; assert(!of_dma_is_coherent(&sd));
    sd.property = "dma-coherent"; assert(of_dma_is_coherent(&sd));
    sd.property = BOARD_PROPERTY;
    assert(!of_dma_is_coherent(&sd));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            dtb = Path(temp) / "board.dtb"
            subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb),
                            str(ROOT / "linux/tanmatsu.dts")], check=True)
            properties = subprocess.check_output(["fdtget", "-p", str(dtb), "/soc/mmc@50083000"], text=True).splitlines()
            coherence = [p for p in properties if p in ("dma-coherent", "dma-noncoherent")]
            self.assertEqual(coherence, ["dma-noncoherent"])
            c, exe = Path(temp) / "coherence.c", Path(temp) / "coherence"
            c.write_text(prefix + helper + main.replace("BOARD_PROPERTY", '"' + coherence[0] + '"'))
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=5)

    def check_dma_fixture(self, source, expect_failure, failure_text="SDMMC_CTRL/4"):
        kernel = ROOT / "build/linux-6.18.35"
        if not kernel.exists():
            self.skipTest("pinned Linux source is not prepared")
        definitions = source[source.index("#define SD_RATE"):source.index("/* Privileged boot helper")]
        writable = source[source.index("static bool tanmatsu_sd_writable"):source.index("static ssize_t write_ranges_store")]
        policy = source[source.index("static bool tanmatsu_sd_allowed"):source.index("static int tanmatsu_sd_reset")]
        reset = source[source.index("static int tanmatsu_sd_reset"):source.index("static int tanmatsu_sd_clock_update")]
        helpers = source[source.index("/* Stop both the bus master"):source.index("static void tanmatsu_sd_ios")]
        fingerprint = source[source.index("static u32 tanmatsu_sd_fingerprint"):source.index("/* One deadline")]
        transfer = source[source.index("static int tanmatsu_sd_transfer"):source.index("/* The block layer requires")]
        headers = "\n".join((kernel / f"include/linux/mmc/{name}.h").read_text() for name in ("core", "mmc", "sd"))
        constants = []
        for name in sorted(set(re.findall(r"\b(?:MMC|SD)_[A-Z0-9_]+\b", policy + transfer + " MMC_ALL_SEND_CID"))):
            match = re.search(r"^#define\s+" + name + r"\s+(.+)$", headers, re.M)
            if match:
                constants.append(f"#define {name} {match[1]}")
        dw = (kernel / "drivers/mmc/host/dw_mmc.h").read_text()
        registers = dw[dw.index("#define SDMMC_CTRL\t"):dw.index("/* FIFO register access")]
        fixture = (ROOT / "tests/fixtures/host-sd-dma.c").read_text()
        fixture = fixture.replace("/* DRIVER_DEFINITIONS */", registers + "\n".join(constants) + "\n" + definitions)
        fixture = fixture.replace("/* DRIVER_FUNCTIONS */", writable + policy + reset + helpers + fingerprint + transfer)
        with tempfile.TemporaryDirectory() as temp:
            c = Path(temp) / "dma.c"
            exe = Path(temp) / "dma"
            c.write_text(fixture)
            subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], cwd=temp, capture_output=True, text=True, timeout=5)
            if expect_failure:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(failure_text, result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)

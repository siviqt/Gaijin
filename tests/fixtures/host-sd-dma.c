/* Native register/cache model around the actual kernel IDMAC helpers. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef uint8_t u8;
typedef uint32_t __le32;
typedef uint64_t dma_addr_t;
typedef long ktime_t;
#define __iomem
#define __aligned(n) __attribute__((aligned(n)))
#define BIT(n) (1U << (n))
#define PAGE_SIZE 4096U
#define ALIGN(n,a) (((n) + (a) - 1) & ~((a) - 1U))
#define lower_32_bits(n) ((uint32_t)(n))
#define cpu_to_le32(n) (n)
#define le32_to_cpu(n) (n)
#define DMA_BIT_MASK(n) ((1ULL << (n)) - 1)
#define DMA_FROM_DEVICE 2
#define DMA_BIDIRECTIONAL 0
#define mmc_dev(h) (h)
struct device { int unused; };
struct work_struct { int unused; };
struct mmc_card { bool blockaddr; };
struct mmc_host { struct mmc_card *card; };
struct mutex { int unused; };
static void mutex_lock(struct mutex *m) { (void)m; }
static void mutex_unlock(struct mutex *m) { (void)m; }
static bool mmc_card_is_blockaddr(struct mmc_card *c) { return c->blockaddr; }
struct mmc_data { unsigned flags, blocks, blksz, bytes_xfered, sg_len; void *sg; };
struct mmc_command { unsigned opcode, arg, flags, resp[4]; struct mmc_data *data; };
/* DRIVER_DEFINITIONS */
static uint32_t regs[256];
static struct tanmatsu_sd *host;
static struct tanmatsu_sd_desc device_desc;
static unsigned char device_data[512];
static int mode, writes, reports, maps, map_fail, mask_fail, corrupt_sg;
static int active, launched, reset_stuck, prepared, barrier, data_sync, desc_sync;
static int data_live, desc_live, engine_needs_reset, fail_halt;
static int coherent;
static long tick;
static void dev_info(void *dev, const char *format, ...)
{ (void)dev; (void)format; reports++; }
#define dev_err dev_info
static long ktime_get(void) { return tick++; }
static long ktime_add_ms(long t, int ms) { (void)ms; return t + 20; }
static int ktime_compare(long a, long b) { return (a > b) - (a < b); }
static void cpu_relax(void) {}
static int dma_set_mask_and_coherent(struct device *dev, uint64_t mask)
{ (void)dev; assert(mask == 0xffffffffULL); return mask_fail ? -EIO : 0; }
static dma_addr_t dma_map_single(struct device *dev, void *ptr, unsigned size, int direction)
{
    (void)dev; assert(!active); maps++;
    assert((uintptr_t)ptr % 64 == 0);
    if (maps == map_fail) return UINT64_MAX;
    if (ptr == host->buffer) {
        assert(size == 512 && direction == DMA_BIDIRECTIONAL);
        data_live = 1; memcpy(device_data, ptr, size); return 0x48001000;
    }
    assert(ptr == &host->desc && size == 64 && direction == DMA_BIDIRECTIONAL);
    desc_live = 1; memcpy(&device_desc, ptr, size); return 0x48002000;
}
static int dma_mapping_error(struct device *dev, dma_addr_t address)
{ (void)dev; return address == UINT64_MAX; }
static inline bool dma_need_sync(struct device *dev, dma_addr_t address)
{ (void)dev; (void)address; return !coherent; }
static void dma_unmap_single(struct device *dev, dma_addr_t address, unsigned size, int direction)
{
    (void)dev; assert(!active);
    if (address == 0x48001000) {
        assert(data_live && size == 512 && direction == DMA_BIDIRECTIONAL); data_live = 0;
    } else {
        assert(address == 0x48002000 && desc_live && size == 64 && direction == DMA_BIDIRECTIONAL); desc_live = 0;
    }
}
static void dma_sync_single_for_cpu(struct device *dev, dma_addr_t address, unsigned size, int direction)
{
    (void)dev; assert(!active);
    if (coherent) return; /* Linux dma-direct skips cache ops in this case. */
    if (address == 0x48001000) {
        assert(data_live && size == 512 && direction == DMA_BIDIRECTIONAL);
        memcpy(host->buffer, device_data, size);
    } else {
        assert(address == 0x48002000 && desc_live && size == 64 && direction == DMA_BIDIRECTIONAL);
        memcpy(&host->desc, &device_desc, size);
    }
}
static void dma_sync_single_for_device(struct device *dev, dma_addr_t address, unsigned size, int direction)
{
    (void)dev; assert(!active);
    if (coherent) return;
    if (address == 0x48001000) {
        assert(data_live && size == 512 && direction == DMA_BIDIRECTIONAL);
        memcpy(device_data, host->buffer, size); data_sync = 1;
    } else {
        assert(address == 0x48002000 && desc_live && size == 64 && direction == DMA_BIDIRECTIONAL);
        memcpy(&device_desc, &host->desc, size); desc_sync = 1;
    }
}
static void dma_wmb(void) { assert(data_sync && desc_sync); barrier = 1; }
static void dma_rmb(void) { assert(!active); }
static unsigned sg_pcopy_from_buffer(void *dst, unsigned n, const void *src, unsigned length, unsigned offset)
{ (void)n; assert(!active); memcpy((char *)dst + offset, src, length); return length; }
static unsigned sg_pcopy_to_buffer(const void *src, unsigned n, void *dst, unsigned length, unsigned offset)
{ (void)n; assert(!active); memcpy(dst, (char *)src + offset, length); if (corrupt_sg) ((char *)dst)[0] ^= 1; return length; }
static unsigned readl(void *reg)
{
    unsigned offset = (char *)reg - (char *)regs;
    assert(offset != DATA_240A_OFFSET); /* No CPU FIFO access is allowed. */
    if (offset == SDMMC_RINTSTS && launched) {
        if (mode == 1) return SDMMC_INT_RTO;
        if (mode == 2) return SDMMC_INT_DCRC;
        if (mode == 4) return SDMMC_INT_CMD_DONE;
        return SDMMC_INT_CMD_DONE | SDMMC_INT_DATA_OVER;
    }
    if (offset == SDMMC_STATUS && mode == 11) return SDMMC_STATUS_BUSY;
    return *(unsigned *)reg;
}
static void writel(unsigned value, void *reg)
{
    unsigned offset = (char *)reg - (char *)regs;
    writes++;
    if (offset == SDMMC_RINTSTS || offset == SDMMC_IDSTS) { *(unsigned *)reg &= ~value; return; }
    *(unsigned *)reg = value;
    if (offset == SDMMC_BMOD && value & SDMMC_IDMAC_SWRESET) {
        if (!reset_stuck) { *(unsigned *)reg = 0; active = 0; engine_needs_reset = 0; }
    }
    if (offset == SDMMC_CTRL && value & SDMMC_CTRL_ALL_RESET_FLAGS) *(unsigned *)reg = 0;
    if (offset == SDMMC_BMOD && value & SDMMC_IDMAC_ENABLE)
        assert(regs[SDMMC_CTRL/4] == (SDMMC_CTRL_DMA_ENABLE | SDMMC_CTRL_USE_IDMAC));
    if (offset == SDMMC_PLDMND) {
        assert(!engine_needs_reset);
        assert(regs[SDMMC_IDINTEN/4] == (SDMMC_IDMAC_INT_NI | SDMMC_IDMAC_INT_RI | SDMMC_IDMAC_INT_TI));
        assert(!(regs[SDMMC_CTRL/4] & SDMMC_CTRL_INT_ENABLE));
        assert(barrier && data_sync && desc_sync);
        assert(regs[SDMMC_DBADDR/4] == 0x48002000);
        assert(device_desc.flags == 0x8000001cU);
        assert(device_desc.buffer == 0x48001000 && !device_desc.next);
        assert(regs[SDMMC_CTRL/4] == (SDMMC_CTRL_DMA_ENABLE | SDMMC_CTRL_USE_IDMAC));
        assert(regs[SDMMC_BMOD/4] == (SDMMC_IDMAC_FB | SDMMC_IDMAC_ENABLE));
        active = prepared = 1; barrier = data_sync = desc_sync = 0;
    }
    if (offset == SDMMC_CMD) {
        unsigned length = regs[SDMMC_BYTCNT/4];
        launched = 1;
        if (fail_halt) reset_stuck = 1;
        bool writing = !!(value & SDMMC_CMD_DAT_WR);
        if (length) {
            assert(prepared && active && device_desc.size == ALIGN(length, 4));
            if (!writing)
                for (unsigned i = 0; i < length; i++) device_data[i] = mode == 10 ? 0 : (i & 255);
            else
                for (unsigned i = 0; i < length; i++) assert(device_data[i] == (i & 255));
            if (!writing && !regs[SDMMC_CMDARG/4] && length == 512 && mode != 10) { device_data[510] = 0x55; device_data[511] = 0xaa; }
            if (mode != 7) device_desc.flags &= ~SD_DESC_OWN;
            if (mode == 8) device_desc.flags |= SD_DESC_ERROR;
            regs[SDMMC_IDSTS/4] = mode == 3 ? 0 : (writing ? SDMMC_IDMAC_INT_TI : SDMMC_IDMAC_INT_RI);
            if (mode == 5) regs[SDMMC_IDSTS/4] = SDMMC_IDMAC_INT_FBE;
            if (mode == 6) regs[SDMMC_IDSTS/4] = SDMMC_IDMAC_INT_DU;
            regs[SDMMC_TCBCNT/4] = length;
            regs[SDMMC_TBBCNT/4] = mode == 9 ? 0 : length;
        }
    }
}
#define readl_poll_timeout(address, value, condition, delay, timeout) \
    ({ int tries = 0; (void)(delay); (void)(timeout); \
       do { (value) = readl(address); if (condition) break; } while (++tries < 3); \
       (condition) ? 0 : -ETIMEDOUT; })
/* DRIVER_FUNCTIONS */
static void setup(struct tanmatsu_sd *h)
{
    assert(!active && !data_live && !desc_live);
    memset(h, 0, sizeof(*h)); memset(regs, 0, sizeof(regs));
    host = h; h->regs = regs;
    mode = writes = reports = maps = map_fail = mask_fail = corrupt_sg = 0;
    launched = reset_stuck = prepared = barrier = data_sync = desc_sync = tick = fail_halt = 0;
    engine_needs_reset = 1; /* Loader left an old suspended descriptor chain. */
    coherent = 0;
}
int main(void)
{
    struct tanmatsu_sd h;
    struct mmc_card card = { .blockaddr = true };
    struct mmc_host mmc = { .card = &card };
    struct device dev;
    unsigned char out[1025];
    struct mmc_data data = { .flags = MMC_DATA_READ, .blocks = 1, .blksz = 512, .sg_len = 1, .sg = out };
    struct mmc_command cmd = { .opcode = MMC_READ_SINGLE_BLOCK, .arg = 1, .flags = MMC_RSP_PRESENT | MMC_RSP_CRC, .data = &data };
    assert(sizeof(struct tanmatsu_sd_desc) == 64);
    setup(&h); mask_fail = 1;
    assert(tanmatsu_sd_dma_map(&h, &dev) == -EIO && maps == 0);
    for (int fail = 1; fail <= 2; fail++) {
        setup(&h); map_fail = fail;
        assert(tanmatsu_sd_dma_map(&h, &dev) == -ENOMEM);
        assert(!data_live && !desc_live);
    }
    setup(&h);
    assert(tanmatsu_sd_dma_map(&h, &dev) == 0);
    /* Both mappings persist, with explicit CPU/device ownership on every I/O. */
    for (unsigned length = 1; length <= 512; length++) {
        mode = tick = launched = 0; data.blksz = length; data.bytes_xfered = 0;
        memset(out, 0xee, sizeof(out));
        assert(tanmatsu_sd_transfer(&h, &cmd, 4) == 0);
        assert(data.bytes_xfered == length && !active);
        for (unsigned j = 0; j < length; j++) assert(out[4+j] == (j & 255));
        assert(out[3] == 0xee && out[length+4] == 0xee);
    }
    assert(maps == 2);
    data.blksz = 512;
    for (int fault = 1; fault <= 9; fault++) {
        mode = fault; tick = launched = 0; data.bytes_xfered = 0;
        assert(tanmatsu_sd_transfer(&h, &cmd, 0) < 0);
        assert(data.bytes_xfered == 0 && !active && tick < 30);
    }
    for (unsigned op = 0; op < 64; op++) {
        if (!tanmatsu_sd_allowed(op, &data)) {
            cmd.opcode = op; writes = 0;
            assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EROFS && !writes);
        }
    }
    cmd.opcode = MMC_READ_SINGLE_BLOCK; mode = tick = launched = 0;
    data.flags = 1U << 8; writes = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EROFS && !writes);
    /* CMD24 is denied before MMIO until the exact file sector is registered. */
    h.mmc = &mmc; cmd.opcode = MMC_WRITE_BLOCK;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EROFS && !writes);
    h.ranges[0] = (struct tanmatsu_sd_range){ 100, 8 }; h.nr_ranges = 1;
    for (unsigned i = 0; i < 512; i++) out[4+i] = i & 255;
    cmd.arg = 100; mode = tick = launched = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 4) == 0 && data.bytes_xfered == 512);
    for (unsigned i = 0; i < 512; i++) assert(out[4+i] == (i & 255));
    for (unsigned arg = 99; arg <= 108; arg++) {
        cmd.arg = arg; mode = tick = launched = writes = 0;
        if (arg < 100 || arg >= 108)
            assert(tanmatsu_sd_transfer(&h, &cmd, 4) == -EROFS && !writes);
        else
            assert(tanmatsu_sd_transfer(&h, &cmd, 4) == 0);
    }
    card.blockaddr = false;
    assert(tanmatsu_sd_writable(&h, 100*512));
    assert(!tanmatsu_sd_writable(&h, 100*512+1));
    assert(!tanmatsu_sd_writable(&h, 99*512));
    card.blockaddr = true; cmd.arg = 100;
    for (int fault = 1; fault <= 11; fault++) {
        if (fault == 10) continue;
        mode = fault; tick = launched = 0; data.bytes_xfered = 0;
        assert(tanmatsu_sd_transfer(&h, &cmd, 4) < 0);
        assert(!active && !data.bytes_xfered && tick < 30);
    }
    cmd.opcode = MMC_READ_SINGLE_BLOCK; cmd.arg = 1;
    data.flags = MMC_DATA_READ; data.blocks = 2; writes = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EROFS && !writes);
    data.blocks = 1;
    cmd.arg = 0; mode = 10; reports = tick = launched = 0; data.bytes_xfered = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EILSEQ && reports == 1);
    assert(data.bytes_xfered == 0);
    tick = launched = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EILSEQ && reports == 1);
    mode = tick = launched = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == 0 && out[510] == 0x55 && out[511] == 0xaa);
    corrupt_sg = 1; tick = launched = 0; data.bytes_xfered = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EIO && !data.bytes_xfered);
    corrupt_sg = 0;
    /* Reset failure never hands active DMA memory back to the CPU or unmaps it. */
    fail_halt = 1; tick = launched = 0; data.bytes_xfered = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -ETIMEDOUT && h.dead && active);
    writes = 0;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == -EIO && !writes);
    assert(data_live && desc_live);
    reset_stuck = fail_halt = 0;
    assert(tanmatsu_sd_dma_halt(&h) == 0 && !active);
    tanmatsu_sd_dma_release(&h);
    assert(!data_live && !desc_live);
    h.dead = false; mode = tick = launched = 0; cmd.data = NULL;
    cmd.opcode = MMC_ALL_SEND_CID; cmd.flags = MMC_RSP_PRESENT | MMC_RSP_136;
    regs[SDMMC_RESP0/4] = 10; regs[SDMMC_RESP1/4] = 20; regs[SDMMC_RESP2/4] = 30; regs[SDMMC_RESP3/4] = 40;
    assert(tanmatsu_sd_transfer(&h, &cmd, 0) == 0);
    assert(cmd.resp[0] == 40 && cmd.resp[1] == 30 && cmd.resp[2] == 20 && cmd.resp[3] == 10);
    /* Wrong OF coherence must fail before publishing a DMA descriptor. */
    setup(&h); coherent = 1;
    assert(tanmatsu_sd_dma_map(&h, &dev) == -EINVAL);
    assert(!data_live && !desc_live && !active && !writes);
    return 0;
}

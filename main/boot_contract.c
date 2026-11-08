// SPDX-License-Identifier: MIT
#include "boot_contract.h"
#include <libfdt.h>
#include <string.h>

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static uint64_t le64(const uint8_t *p) { return le32(p) | (uint64_t)le32(p + 4) << 32; }

int m0_parse_manifest(const uint8_t *data, size_t size, struct m0_manifest *out) {
    // magic, format, board revision, boot-contract version, flags, three (length, hash) records
    if (size != M0_MANIFEST_BYTES || memcmp(data, "GAIJIN1\0", 8) ||
        le32(data + 8) != 1 || le32(data + 12) != 7 ||
        le32(data + 16) != 1 || le32(data + 20) != 0 ||
        le32(data + 132) != 0 || le32(data + 136) != 0) return -1;
    struct m0_asset *assets[] = {&out->image, &out->dtb, &out->initrd};
    for (unsigned i = 0; i < 3; ++i) {
        assets[i]->bytes = le32(data + 24 + 36 * i);
        memcpy(assets[i]->sha256, data + 28 + 36 * i, 32);
    }
    if (out->image.bytes < 64 || out->image.bytes > M0_KERNEL_LIMIT ||
        out->dtb.bytes < 40 || out->dtb.bytes > M0_DTB_CAPACITY - 1024 ||
        out->initrd.bytes > M0_INITRD_LIMIT) return -1;
    if (!out->initrd.bytes) {
        const uint8_t zero[32] = {0};
        if (memcmp(out->initrd.sha256, zero, 32)) return -1;
    }
    return 0;
}

int m0_check_image(const uint8_t *header, size_t header_bytes,
                   uint32_t file_bytes, uint32_t *memory_bytes) {
    // Flat little-endian RV32 Image v0.2, linked at RAM_BASE, without decompression.
    if (header_bytes < 64 || file_bytes < 64 || file_bytes > M0_KERNEL_LIMIT ||
        le64(header + 8) != 0 || le64(header + 24) != 0 ||
        le32(header + 32) != 2 || memcmp(header + 48, "RISCV\0\0\0", 8) ||
        memcmp(header + 56, "RSC\x05", 4)) return -1;
    uint64_t memory = le64(header + 16);
    if (memory < file_bytes || memory > M0_KERNEL_LIMIT) return -1;
    *memory_bytes = (uint32_t)memory;
    return 0;
}

int m0_prepare_dtb(void *dtb, size_t file_bytes, uint32_t initrd_bytes) {
    if (file_bytes > M0_DTB_CAPACITY - 1024 || initrd_bytes > M0_INITRD_LIMIT ||
        fdt_check_full(dtb, file_bytes) || fdt_totalsize(dtb) != file_bytes ||
        fdt_node_check_compatible(dtb, 0, "tanmatsu,gaijin-v1")) return -1;
    int len = 0;
    const fdt32_t *rev = fdt_getprop(dtb, 0, "tanmatsu,board-revision", &len);
    if (!rev || len != 4 || fdt32_to_cpu(*rev) != 7 ||
        fdt_address_cells(dtb, 0) != 1 || fdt_size_cells(dtb, 0) != 1 ||
        fdt_num_mem_rsv(dtb) != 0) return -1;
    // Fixed display memory must remain reserved even on the USB-only path.
    // The loader writes these addresses before Linux consumes reserved-memory.
    int display_memory = fdt_path_offset(dtb, "/reserved-memory/framebuffer@48e00000");
    const fdt32_t *fb_region = fdt_getprop(dtb, display_memory, "reg", &len);
    if (!fb_region || len != 8 || fdt32_to_cpu(fb_region[0]) != M5_FB_BASE ||
        fdt32_to_cpu(fb_region[1]) != M5_FB_RESERVE ||
        fdt_getprop(dtb, display_memory, "reusable", NULL)) return -1;
    int display = fdt_path_offset(dtb, "/soc/framebuffer@50081000");
    const fdt32_t *fb_memory = fdt_getprop(dtb, display, "memory-region", &len);
    if (display < 0 || fdt_node_check_compatible(dtb, display, "tanmatsu,rev7-fb") ||
        !fb_memory || len != 4 ||
        fdt_node_offset_by_phandle(dtb, fdt32_to_cpu(*fb_memory)) != display_memory) return -1;
    const char *status = fdt_getprop(dtb, display, "status", &len);
    if (!status || len != sizeof("disabled") || memcmp(status, "disabled", sizeof("disabled"))) return -1;
    // Accept only the single-core board DTB, never a different board's peripheral map.
    int cpu = fdt_path_offset(dtb, "/cpus/cpu@0");
    if (cpu < 0 || fdt_path_offset(dtb, "/cpus/cpu@1") >= 0) return -1;
    if (fdt_open_into(dtb, dtb, M0_DTB_CAPACITY)) return -1;
    int memory = fdt_path_offset(dtb, "/memory@48000000");
    const fdt32_t region[] = {cpu_to_fdt32(M0_RAM_BASE), cpu_to_fdt32(M0_RAM_SIZE)};
    if (memory < 0 || fdt_setprop(dtb, memory, "reg", region, sizeof(region))) return -1;
    int chosen = fdt_path_offset(dtb, "/chosen");
    if (chosen < 0 || fdt_setprop_string(dtb, chosen, "bootargs", M0_BOOTARGS)) return -1;
    // Offsets can move after every structural edit; reacquire before each write.
    chosen = fdt_path_offset(dtb, "/chosen");
    if (initrd_bytes) {
        if (fdt_setprop_u32(dtb, chosen, "linux,initrd-start", M0_INITRD_BASE)) return -1;
        chosen = fdt_path_offset(dtb, "/chosen");
        if (fdt_setprop_u32(dtb, chosen, "linux,initrd-end", M0_INITRD_BASE + initrd_bytes)) return -1;
    } else {
        int res = fdt_delprop(dtb, chosen, "linux,initrd-start");
        if (res && res != -FDT_ERR_NOTFOUND) return -1;
        chosen = fdt_path_offset(dtb, "/chosen");
        res = fdt_delprop(dtb, chosen, "linux,initrd-end");
        if (res && res != -FDT_ERR_NOTFOUND) return -1;
    }
    // Reserve the entire FDT scratch window. Linux reserves initrd itself from /chosen.
    return fdt_add_mem_rsv(dtb, M0_DTB_BASE, M0_DTB_CAPACITY);
}

int m5_enable_display(void *dtb) {
    int display = fdt_path_offset(dtb, "/soc/framebuffer@50081000");
    if (display < 0 || fdt_node_check_compatible(dtb, display, "tanmatsu,rev7-fb")) return -1;
    return fdt_setprop_string(dtb, display, "status", "okay");
}


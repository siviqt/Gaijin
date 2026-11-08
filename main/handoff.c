// SPDX-License-Identifier: Apache-2.0 OR GPL-2.0-only
// PMP / direct M-mode entry adapted from mrbreaker/why2025-linux
// bd790395c14968a27c00f962c9cd08d66f39de24, linux-native/main/main.c.
#include "boot_contract.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_private/hw_stack_guard.h"
#include "hal/cache_hal.h"
#include "hal/systimer_ll.h"
#include "soc/clic_reg.h"
#include "soc/systimer_struct.h"

void IRAM_ATTR __attribute__((noreturn)) m0_handoff(uint32_t image_bytes, uint32_t initrd_bytes) {
    __asm__ volatile("csrci mstatus, 8\ncsrw mie, zero" ::: "memory");
    esp_cpu_stall(1);
    esp_hw_stack_guard_monitor_stop();
    for (unsigned i = 0; i < 3; ++i) {
        systimer_ll_enable_alarm(&SYSTIMER, i, false);
        systimer_ll_enable_alarm_int(&SYSTIMER, i, false);
        systimer_ll_clear_alarm_int(&SYSTIMER, i);
    }
    // All CLIC slots masked. Kernel replaces vectors, levels and interrupt routing.
    for (unsigned i = 0; i < 48; ++i) {
        *(volatile uint8_t *)BYTE_CLIC_INT_IE_REG(i) = 0;
        *(volatile uint8_t *)BYTE_CLIC_INT_IP_REG(i) = 0;
    }
    // Leave IDF's PSRAM MMU/cache/clocks enabled. Running entirely in SRAM here
    // allows writeback then invalidation of both data and instruction aliases.
    // Cover the complete final cache line as well as complete asset bytes.
    image_bytes = (image_bytes + 63u) & ~63u;
    initrd_bytes = (initrd_bytes + 63u) & ~63u;
    cache_hal_writeback_addr(M0_RAM_BASE, image_bytes);
    cache_hal_invalidate_addr(M0_RAM_BASE, image_bytes);
    cache_hal_writeback_addr(M0_DTB_BASE, M0_DTB_CAPACITY);
    cache_hal_invalidate_addr(M0_DTB_BASE, M0_DTB_CAPACITY);
    if (initrd_bytes) {
        cache_hal_writeback_addr(M0_INITRD_BASE, initrd_bytes);
        cache_hal_invalidate_addr(M0_INITRD_BASE, initrd_bytes);
    }
    // RV32 PMP entry 9: unlocked RWX NAPOT 64-MiB external-memory aperture.
    // Linux receives only the physically populated 32 MiB in the DTB.
    unsigned long cfg;
    __asm__ volatile("csrw pmpaddr9, %0" :: "r"(0x127fffffUL) : "memory");
    __asm__ volatile("csrr %0, pmpcfg2" : "=r"(cfg));
    cfg = (cfg & ~(0xffUL << 8)) | (0x1fUL << 8);
    __asm__ volatile("csrw pmpcfg2, %0" :: "r"(cfg) : "memory");
    __asm__ volatile("fence rw, rw\nfence.i\nli a0, 0\nmv a1, %0\njr %1"
                     :: "r"(M0_DTB_BASE), "r"(M0_RAM_BASE) : "a0", "a1", "memory");
    __builtin_unreachable();
}

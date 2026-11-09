// SPDX-License-Identifier: MIT
#include "boot_contract.h"
#include "bootloader_common.h"
#include "sd.h"
#include "home.h"
#include "gaijin_home.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "psa/crypto.h"
#include "sdkconfig.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#if !CONFIG_IDF_TARGET_ESP32P4 || !CONFIG_SPIRAM_USE_MEMMAP || !CONFIG_FREERTOS_UNICORE
#error "M1 requires P4, unmanaged PSRAM and a single IDF core"
#endif
#if CONFIG_SPIRAM_XIP_FROM_PSRAM || CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY || CONFIG_SPIRAM_ALLOW_NOINIT_SEG_EXTERNAL_MEMORY || CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM
#error "Loader code/data must stay outside Linux PSRAM"
#endif
#if CONFIG_ESP_INT_WDT || CONFIG_ESP_TASK_WDT_EN || !CONFIG_BOOTLOADER_CUSTOM_RESERVE_RTC || CONFIG_BOOTLOADER_CUSTOM_RESERVE_RTC_IN_CRC || CONFIG_BOOTLOADER_WDT_DISABLE_IN_USER_CODE
#error "M1 handoff requires disabled IDF watchdogs and retained AppFS boot selection"
#endif

#define BOOT_DIR "/sd/apps/" GAIJIN_APP_ID "/boot/"
static const char *TAG = "Gaijin";
extern esp_err_t gaijin_display_initialize(void);
extern void m0_handoff(uint32_t image_memory_bytes, uint32_t initrd_bytes) __attribute__((noreturn));

static int display_test_requested(void)
{
    static const char expected[] = "gaijin-display-test-v1\n";
    char data[sizeof(expected)];
    FILE *file = fopen("/sd/gaijin/display-test", "rb");
    if (!file) return 0;
    size_t bytes = fread(data, 1, sizeof(data), file);
    int ok = !ferror(file) && bytes == sizeof(expected) - 1 &&
             !memcmp(data, expected, sizeof(expected) - 1);
    if (fclose(file)) ok = 0;
    if (!ok) ESP_LOGW(TAG, "Ignoring invalid /gaijin/display-test marker");
    return ok;
}

static void clear_appfs_selection(void) {
    // Same retained-memory contract as BSP v1.6.4 restart_to_launcher.
    rtc_retain_mem_t *mem = bootloader_common_get_rtc_retain_mem();
    memset(mem->custom, 0, sizeof(uint64_t));
    /* The supported AppFS argument is consumed exactly once, before SD I/O. */
    mem->custom[12]=0;
    memset(mem->custom+GH_APPFS_ARG_OFFSET,0,GH_APPFS_ARG_BYTES);
    __asm__ volatile("fence rw, rw" ::: "memory");
}

static void fail(const char *reason) __attribute__((noreturn));
static void fail(const char *reason) {
    ESP_LOGE(TAG, "%s; reset to launcher", reason);
    clear_appfs_selection();
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
}

static void load_asset(const char *name, const struct m0_asset *asset, uint32_t address) {
    char path[128];
    snprintf(path, sizeof(path), BOOT_DIR "%s", name);
    ESP_LOGI(TAG, "load %s: %" PRIu32 " bytes -> 0x%08" PRIx32, name, asset->bytes, address);
    FILE *file = fopen(path, "rb");
    if (!file) fail(path);
    psa_hash_operation_t hash = PSA_HASH_OPERATION_INIT;
    if (psa_hash_setup(&hash, PSA_ALG_SHA_256) != PSA_SUCCESS) fail("SHA256 start failed");
    uint8_t *destination = (uint8_t *)(uintptr_t)address;
    uint32_t remaining = asset->bytes;
    // fread's internal buffers stay in SRAM; no heap allocation ever uses PSRAM.
    while (remaining) {
        size_t count = remaining > 4096 ? 4096 : remaining;
        if (fread(destination, 1, count, file) != count) fail("short read / SD error");
        if (psa_hash_update(&hash, destination, count) != PSA_SUCCESS) fail("SHA256 update failed");
        destination += count;
        remaining -= count;
    }
    int extra = fgetc(file);
    int io_error = ferror(file);
    int close_error = fclose(file);
    if (extra != EOF || io_error || close_error) fail("asset length / SD error");
    uint8_t digest[32];
    size_t digest_bytes = 0;
    if (psa_hash_finish(&hash, digest, sizeof(digest), &digest_bytes) != PSA_SUCCESS ||
        digest_bytes != sizeof(digest)) fail("SHA256 finish failed");
    if (memcmp(digest, asset->sha256, sizeof(digest))) fail("asset SHA256 mismatch");
}

void app_main(void) {
    rtc_retain_mem_t *retained = bootloader_common_get_rtc_retain_mem();
    _Static_assert(offsetof(rtc_retain_mem_t, custom) == 12, "AppFS RTC layout changed");
    _Static_assert(sizeof(retained->custom)>=GH_APPFS_ARG_OFFSET+GH_APPFS_ARG_BYTES,"AppFS argument does not fit");
    char home_argument[128];
    int appfs_fd=gaijin_home_boot_selection(retained->custom,sizeof(retained->custom),home_argument);
    clear_appfs_selection();
    ESP_LOGI(TAG, "Gaijin M5 contract=1 board=7 BSP=1.6.4 launcher=0.2.8 radio=3.4.0 MCU=r9 (target)");
    if (esp_psram_get_size() != M0_RAM_SIZE) fail("requires exactly 32 MiB PSRAM");
    if (psa_crypto_init() != PSA_SUCCESS) fail("crypto init failed");
    ESP_LOGI(TAG, "PSRAM=32 MiB unmanaged; hart0 M-mode; hart1 held by IDF unicore startup");
    if (gaijin_sd_mount() != ESP_OK)
        fail("SD mount failed (format disabled)");
    gaijin_sd_reference();
    uint8_t data[M0_MANIFEST_BYTES];
    FILE *manifest_file = fopen(BOOT_DIR "manifest.bin", "rb");
    if (!manifest_file) fail("boot/manifest.bin missing");
    size_t size = fread(data, 1, sizeof(data), manifest_file);
    int extra = fgetc(manifest_file);
    int read_error = ferror(manifest_file);
    int close_error = fclose(manifest_file);
    struct m0_manifest manifest;
    if (read_error || close_error || extra != EOF || m0_parse_manifest(data, size, &manifest))
        fail("invalid boot manifest / unsupported board or format");
    load_asset("Image", &manifest.image, M0_RAM_BASE);
    uint32_t image_memory_bytes;
    if (m0_check_image((const uint8_t *)(uintptr_t)M0_RAM_BASE, 64,
                       manifest.image.bytes, &image_memory_bytes)) fail("unsupported RV32 flat Image");
    // Linux Image's memory footprint includes BSS, which objcopy omits from disk.
    memset((void *)(uintptr_t)(M0_RAM_BASE + manifest.image.bytes), 0,
           image_memory_bytes - manifest.image.bytes);
    load_asset("tanmatsu.dtb", &manifest.dtb, M0_DTB_BASE);
    if (manifest.initrd.bytes) load_asset("initramfs", &manifest.initrd, M0_INITRD_BASE);
    if (m0_prepare_dtb((void *)(uintptr_t)M0_DTB_BASE, manifest.dtb.bytes, manifest.initrd.bytes))
        fail("DTB validation / patch failed");
    if ((uintptr_t)retained->custom != UINT32_C(0x5010800c))
        fail("AppFS retained-memory address changed");
    if (gaijin_display_initialize() == ESP_OK) {
        if (m5_enable_display((void *)(uintptr_t)M0_DTB_BASE)) fail("framebuffer DTB update failed");
    } else {
        ESP_LOGW(TAG, "Display initialization failed; USB-only login");
    }
    if (display_test_requested()) gaijin_display_progress_test();
    if (gaijin_display_progress_stop() ||
        gaijin_home_prepare((void *)(uintptr_t)M0_DTB_BASE,appfs_fd,home_argument) ||
        gaijin_display_progress_stop()) fail("home bootstrap / display teardown failed");
    // All FAT files and loader progress DMA are closed before Linux ownership.
    if (gaijin_sd_unmount() != ESP_OK) fail("SD unmount failed");
    ESP_LOGI(TAG, "SD card unmounted");
    ESP_LOGI(TAG, "verified Image memory=%" PRIu32 "; DTB=0x%08" PRIx32 "; initrd=%" PRIu32,
             image_memory_bytes, M0_DTB_BASE, manifest.initrd.bytes);
    ESP_LOGI(TAG, "handoff -> Linux 0x%08" PRIx32 " a0=0 a1=0x%08" PRIx32 "; reset recovers launcher",
             M0_RAM_BASE, M0_DTB_BASE);
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(100));
    m0_handoff(image_memory_bytes, manifest.initrd.bytes);
}

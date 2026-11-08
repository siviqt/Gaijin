// SPDX-License-Identifier: MIT
// Read-only reference using the loader's FatFS volume and its disk driver.
#include "sd.h"
#include "boot_contract.h"
#include "ff.h"
#include "diskio.h"
#include "esp_attr.h"
#include "esp_log.h"
#include <inttypes.h>
#include <stdint.h>

void gaijin_sd_reference(void) {
    // This loader mounts exactly one FatFS volume (the slot 0 card).
    // Use a real open file to obtain its physical drive rather than guessing it.
    static FIL file;
    static DRAM_ATTR uint8_t sector[512] __attribute__((aligned(64)));
    FRESULT result = f_open(&file, "0:/apps/" GAIJIN_APP_ID "/boot/manifest.bin", FA_READ);
    if (result != FR_OK) {
        ESP_LOGE("SD reference", "read-only manifest open failed: %d", result);
        return;
    }
    BYTE drive = file.obj.fs->pdrv;
    unsigned long volume_start = (unsigned long)file.obj.fs->volbase;
    DRESULT read_result = disk_read(drive, sector, 0, 1);
    f_close(&file);
    if (read_result != RES_OK) {
        ESP_LOGE("SD reference", "disk_read LBA0 failed: %d", read_result);
        return;
    }
    uint32_t hash = 2166136261U;
    unsigned nonzero = 0;
    for (unsigned i = 0; i < sizeof(sector); i++) {
        hash = (hash ^ sector[i]) * 16777619U;
        nonzero += sector[i] != 0;
    }
    ESP_LOGI("SD reference", "loader LBA0 fnv1a=%08" PRIx32 " nonzero=%u signature=%02x%02x FAT-start=%lu",
             hash, nonzero, sector[510], sector[511], volume_start);
    ESP_LOG_BUFFER_HEX_LEVEL("SD reference LBA0", sector, 32, ESP_LOG_INFO);
}

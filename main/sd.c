// SPDX-License-Identifier: MIT
#include "sd.h"
#include "driver/gpio.h"
#include "driver/sdmmc_host.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/ldo_ll.h"
#include "sd_pwr_ctrl_by_on_chip_ldo.h"

#define SD_MOUNT_POINT "/sd"
#define SD_LDO_CHANNEL 4
#define SD_LDO_PMU_INDEX 4
#define SD_POWER_OFF_MS 4000
#define SD_POWER_SETTLE_MS 150

static const char *TAG = "Gaijin SD reset";
static sdmmc_card_t *card;

static esp_err_t power_cycle(sd_pwr_ctrl_handle_t *power)
{
    // Rev7 CMD/DAT/CLK pull-ups use VSDCARD. Isolate the host pins so
    // they cannot drive current into the socket while that rail is down.
    gpio_config_t pins = {
        .pin_bit_mask = (UINT64_C(1) << 45) - (UINT64_C(1) << 39),
        .mode = GPIO_MODE_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&pins), TAG, "isolate slot 0 pins");

    // Take software ownership even if the previous stage used another
    // source. Clear the 3.3 V bypass as well as disabling the regulator.
    const int unit = LDO_ID2UNIT(SD_LDO_CHANNEL);
    ldo_ll_set_owner(unit, LDO_LL_UNIT_OWNER_SW);
    ldo_ll_adjust_voltage(unit, 0, 0, false);
    ldo_ll_enable(unit, false);
    pmu_ext_ldo_reg_t rail = { .val = PMU.ext_ldo[SD_LDO_PMU_INDEX].pmu_ext_ldo.val };
    ESP_RETURN_ON_FALSE(rail.force_tieh_sel && !rail.tieh_sel && !rail.tieh && !rail.xpd,
                        ESP_ERR_INVALID_STATE, TAG, "LDO4 disable readback failed");
    // Socket VDD has a 1 MOhm bleed and 1.1 uF nominal board capacitance.
    // Allow time to discharge even when the card draws little current.
    ESP_LOGI(TAG, "LDO4 disabled, bypass off; discharge wait %d ms", SD_POWER_OFF_MS);
    vTaskDelay(pdMS_TO_TICKS(SD_POWER_OFF_MS));

    const sd_pwr_ctrl_ldo_config_t config = { .ldo_chan_id = SD_LDO_CHANNEL };
    ESP_RETURN_ON_ERROR(sd_pwr_ctrl_new_on_chip_ldo(&config, power), TAG, "restore LDO4");
    rail.val = PMU.ext_ldo[SD_LDO_PMU_INDEX].pmu_ext_ldo.val;
    ESP_RETURN_ON_FALSE(rail.force_tieh_sel && !rail.tieh_sel && rail.tieh && rail.xpd,
                        ESP_ERR_INVALID_STATE, TAG, "LDO4 enable readback failed");
    vTaskDelay(pdMS_TO_TICKS(SD_POWER_SETTLE_MS));
    ESP_LOGI(TAG, "LDO4 restored at 3.3 V; initializing slot 0");
    return ESP_OK;
}

esp_err_t gaijin_sd_mount(void)
{
    // Keep the BSP's native slot wiring and transfer parameters. Mount
    // directly so the BSP does not perform a second, partial reset.
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.slot = SDMMC_HOST_SLOT_0;
    host.max_freq_khz = SDMMC_FREQ_HIGHSPEED;
    ESP_RETURN_ON_ERROR(power_cycle(&host.pwr_ctrl_handle), TAG, "power cycle");

    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.clk = GPIO_NUM_43;
    slot.cmd = GPIO_NUM_44;
    slot.d0 = GPIO_NUM_39;
    slot.d1 = GPIO_NUM_40;
    slot.d2 = GPIO_NUM_41;
    slot.d3 = GPIO_NUM_42;
    slot.width = 4;
    const esp_vfs_fat_mount_config_t config = { .max_files = 2 };
    // ESP-IDF queries the allocation size when it uses this bounce buffer.
    // It must be allocated from the DMA-capable internal heap.
    host.dma_aligned_buffer = heap_caps_malloc(4 * 512, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    ESP_RETURN_ON_FALSE(host.dma_aligned_buffer, ESP_ERR_NO_MEM, TAG, "allocate SD DMA buffer");
    esp_err_t err = esp_vfs_fat_sdmmc_mount(SD_MOUNT_POINT, &host, &slot, &config, &card);
    if (err != ESP_OK) heap_caps_free(host.dma_aligned_buffer);
    return err;
}

esp_err_t gaijin_sd_unmount(void)
{
    // The Linux host checks for the retained 3.3 V I/O rail at probe.
    // Release the IDF host and FAT mount while leaving LDO4 on.
    void *dma_buffer = card->host.dma_aligned_buffer;
    esp_err_t err = esp_vfs_fat_sdcard_unmount(SD_MOUNT_POINT, card);
    if (err == ESP_OK) {
        heap_caps_free(dma_buffer);
        card = NULL;
    }
    return err;
}

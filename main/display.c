// SPDX-License-Identifier: Apache-2.0
// DPI setup adapted from ESP-IDF v6.0.2 esp_lcd_panel_dpi.c,
// SPDX-FileCopyrightText: 2023-2026 Espressif Systems (Shanghai) CO LTD
#include "boot_contract.h"
#include "display_commands.h"
#include "driver/gpio.h"
#include "esp_cache.h"
#include "esp_check.h"
#include "esp_clk_tree.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_io.h"
#include "esp_ldo_regulator.h"
#include "esp_private/dw_gdma.h"
#include "esp_private/esp_clk_tree_common.h"
#include "esp_private/periph_ctrl.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/dw_gdma_ll.h"
#include "hal/mipi_dsi_hal.h"
#include "hal/mipi_dsi_ll.h"
#include <string.h>

static const char *TAG = "Gaijin display";

esp_err_t gaijin_display_initialize(void)
{
    static esp_ldo_channel_handle_t ldo;
    static dw_gdma_channel_handle_t channel;
    esp_lcd_dsi_bus_handle_t bus;
    esp_lcd_panel_io_handle_t io;
    esp_ldo_channel_config_t power = { .chan_id = 3, .voltage_mv = 2500 };
    ESP_RETURN_ON_ERROR(esp_ldo_acquire_channel(&power, &ldo), TAG, "DSI power");
    esp_lcd_dsi_bus_config_t bus_config = {
        .bus_id = 0, .num_data_lanes = 2, .lane_bit_rate_mbps = 500,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_dsi_bus(&bus_config, &bus), TAG, "DSI bus");
    esp_lcd_dbi_io_config_t dbi = { .virtual_channel = 0, .lcd_cmd_bits = 8, .lcd_param_bits = 8 };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_dbi(bus, &dbi, &io), TAG, "DSI commands");
    gpio_config_t reset = { .pin_bit_mask = 1ULL << 14, .mode = GPIO_MODE_OUTPUT };
    ESP_RETURN_ON_ERROR(gpio_config(&reset), TAG, "panel reset GPIO");
    gpio_set_level(14, 1);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(14, 0);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(14, 1);
    vTaskDelay(pdMS_TO_TICKS(120));
    for (unsigned i = 0; i < sizeof(tanmatsu_display_init_sequence) / sizeof(tanmatsu_display_init_sequence[0]); i++) {
        const st7701_lcd_init_cmd_t *cmd = &tanmatsu_display_init_sequence[i];
        ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(io, cmd->cmd, cmd->data, cmd->data_bytes), TAG, "panel command");
        if (cmd->delay_ms) vTaskDelay(pdMS_TO_TICKS(cmd->delay_ms));
    }
    ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(io, LCD_CMD_MADCTL, (uint8_t[]){0}, 1), TAG, "RGB order");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(io, LCD_CMD_COLMOD, (uint8_t[]){0x55}, 1), TAG, "RGB565");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_io_tx_param(io, LCD_CMD_TEOFF, NULL, 0), TAG, "TE off");

    // Public HAL context for the already initialized bus; no second PHY reset.
    mipi_dsi_hal_context_t hal = {
        .host = MIPI_DSI_LL_GET_HOST(0), .bridge = MIPI_DSI_LL_GET_BRG(0),
        .lane_bit_rate_mbps = 500,
    };
    uint32_t hz;
    ESP_RETURN_ON_ERROR(esp_clk_tree_src_get_freq_hz(MIPI_DSI_DPI_CLK_SRC_DEFAULT,
                        ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &hz), TAG, "DPI clock");
    uint32_t div = mipi_dsi_hal_host_dpi_calculate_divider(&hal, hz / 1000000.0f, 30);
    ESP_RETURN_ON_ERROR(esp_clk_tree_enable_src((soc_module_clk_t)MIPI_DSI_DPI_CLK_SRC_DEFAULT, true), TAG, "DPI source");
    PERIPH_RCC_ATOMIC() {
        mipi_dsi_ll_set_dpi_clock_source(0, MIPI_DSI_DPI_CLK_SRC_DEFAULT);
        mipi_dsi_ll_set_dpi_clock_div(0, div);
        mipi_dsi_ll_enable_dpi_clock(0, true);
    }
    mipi_dsi_host_ll_dpi_set_vcid(hal.host, 0);
    mipi_dsi_host_ll_dpi_set_color_coding(hal.host, LCD_COLOR_FMT_RGB565, 0);
    mipi_dsi_host_ll_dpi_set_timing_polarity(hal.host, false, false, false, false, false);
    mipi_dsi_host_ll_dpi_enable_lp_horizontal_timing(hal.host, true, true);
    mipi_dsi_host_ll_dpi_enable_lp_vertical_timing(hal.host, true, true, true, true);
    mipi_dsi_host_ll_dpi_enable_lp_command(hal.host, true);
    mipi_dsi_host_ll_dpi_enable_frame_ack(hal.host, true);
    mipi_dsi_host_ll_dpi_set_video_burst_type(hal.host, MIPI_DSI_LL_VIDEO_BURST_WITH_SYNC_PULSES);
    mipi_dsi_host_ll_dpi_set_video_packet_pixel_num(hal.host, 480);
    mipi_dsi_host_ll_dpi_set_trunks_num(hal.host, 0);
    mipi_dsi_host_ll_dpi_set_null_packet_size(hal.host, 0);
    mipi_dsi_hal_host_dpi_set_horizontal_timing(&hal, 40, 40, 480, 30);
    mipi_dsi_hal_host_dpi_set_vertical_timing(&hal, 16, 16, 800, 16);
    mipi_dsi_brg_ll_set_num_pixel_bits(hal.bridge, M5_FB_BYTES * 8);
    mipi_dsi_brg_ll_set_underrun_discard_count(hal.bridge, 480);
    mipi_dsi_brg_ll_set_input_color_format(hal.bridge, LCD_COLOR_FMT_RGB565);
    mipi_dsi_brg_ll_set_output_color_format(hal.bridge, LCD_COLOR_FMT_RGB565, 0);
    mipi_dsi_brg_ll_set_flow_controller(hal.bridge, MIPI_DSI_LL_FLOW_CONTROLLER_DMA);
    mipi_dsi_brg_ll_set_multi_block_number(hal.bridge, 1);
    mipi_dsi_brg_ll_set_burst_len(hal.bridge, 256);
    mipi_dsi_brg_ll_set_empty_threshold(hal.bridge, 768);
    mipi_dsi_brg_ll_enable_interrupt(hal.bridge, UINT32_MAX, false);
    mipi_dsi_brg_ll_enable(hal.bridge, true);
    mipi_dsi_brg_ll_update_dpi_config(hal.bridge);

    // The loader allocates no PSRAM heap and installs no display ISR.
    // DW-GDMA channel 0 is configured here and first started by Linux.
    dw_gdma_channel_alloc_config_t dma = {
        .src = { .block_transfer_type = DW_GDMA_BLOCK_TRANSFER_LIST, .role = DW_GDMA_ROLE_MEM,
                 .handshake_type = DW_GDMA_HANDSHAKE_HW, .num_outstanding_requests = 5 },
        .dst = { .block_transfer_type = DW_GDMA_BLOCK_TRANSFER_LIST, .role = DW_GDMA_ROLE_PERIPH_DSI,
                 .handshake_type = DW_GDMA_HANDSHAKE_HW, .num_outstanding_requests = 2 },
        .flow_controller = DW_GDMA_FLOW_CTRL_SELF, .chan_priority = 1,
    };
    ESP_RETURN_ON_ERROR(dw_gdma_new_channel(&dma, &channel), TAG, "display DMA");
    dw_gdma_link_list_item_t *desc = (void *)M5_FB_DESC;
    memset((void *)M5_FB_BASE, 0, M5_FB_RESERVE);
    dw_gdma_block_transfer_config_t transfer = {
        .src = { .addr = M5_FB_BASE, .burst_mode = DW_GDMA_BURST_MODE_INCREMENT,
                 .burst_items = DW_GDMA_BURST_ITEMS_512, .burst_len = 16, .width = DW_GDMA_TRANS_WIDTH_64 },
        .dst = { .addr = MIPI_DSI_BRG_MEM_BASE, .burst_mode = DW_GDMA_BURST_MODE_FIXED,
                 .burst_items = DW_GDMA_BURST_ITEMS_256, .burst_len = 16, .width = DW_GDMA_TRANS_WIDTH_64 },
        .size = M5_FB_BYTES / 8,
    };
    ESP_RETURN_ON_ERROR(dw_gdma_lli_config_transfer(desc, &transfer), TAG, "DMA descriptor");
    dw_gdma_ll_lli_set_block_markers(desc, false, true, true);
    ESP_RETURN_ON_ERROR(esp_cache_msync((void *)M5_FB_BASE, M5_FB_RESERVE,
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M), TAG, "framebuffer cache");
    dw_gdma_ll_channel_enable_intr_propagation(&DW_GDMA, 0, UINT32_MAX, false);
    dw_gdma_ll_channel_set_link_list_master_port(&DW_GDMA, 0, DW_GDMA_LL_MASTER_PORT_MEMORY);
    dw_gdma_ll_channel_set_link_list_head_addr(&DW_GDMA, 0, M5_FB_DESC);
    mipi_dsi_host_ll_enable_video_mode(hal.host, true);
    mipi_dsi_brg_ll_enable_dpi_output(hal.bridge, false);
    mipi_dsi_brg_ll_update_dpi_config(hal.bridge);
    ESP_LOGI(TAG, "480x800 RGB565 at 0x%08x; Linux owns DMA refresh", (unsigned)M5_FB_BASE);
    return ESP_OK;
}

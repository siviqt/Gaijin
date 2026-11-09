// SPDX-License-Identifier: Apache-2.0
// DPI setup adapted from ESP-IDF v6.0.2 esp_lcd_panel_dpi.c,
// SPDX-FileCopyrightText: 2023-2026 Espressif Systems (Shanghai) CO LTD
#include "boot_contract.h"
#include "home.h"
#include "esp_attr.h"
#include "esp_intr_alloc.h"
#include "esp_timer.h"
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
#include "soc/interrupts.h"
#include <inttypes.h>
#include <string.h>

static const char *TAG = "Gaijin display";
static bool progress_ready;
static esp_err_t progress_sync_range(void *address, size_t bytes);

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

    // The loader allocates no PSRAM heap. HOME temporarily owns scanout;
    // its explicitly owned interrupt is removed before Linux takes channel 0.
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
    ESP_RETURN_ON_ERROR(progress_sync_range((void *)M5_FB_BASE, M5_FB_RESERVE), TAG, "framebuffer cache");
    dw_gdma_ll_channel_enable_intr_propagation(&DW_GDMA, 0, UINT32_MAX, false);
    dw_gdma_ll_channel_set_link_list_master_port(&DW_GDMA, 0, DW_GDMA_LL_MASTER_PORT_MEMORY);
    dw_gdma_ll_channel_set_link_list_head_addr(&DW_GDMA, 0, M5_FB_DESC);
    mipi_dsi_host_ll_enable_video_mode(hal.host, true);
    mipi_dsi_brg_ll_enable_dpi_output(hal.bridge, false);
    mipi_dsi_brg_ll_update_dpi_config(hal.bridge);
    ESP_LOGI(TAG, "480x800 RGB565 at 0x%08x; Linux owns DMA refresh", (unsigned)M5_FB_BASE);
    progress_ready = true;
    return ESP_OK;
}

/* Loader-only scanout. All ISR state/code lives in internal RAM, and the
 * owned interrupt is disabled/freed without deleting the SDK DMA channel.
 * Linux inherits its existing hardware configuration and reserved buffer. */
#define PROGRESS_DMA_DONE DW_GDMA_LL_CHANNEL_EVENT_DMA_TFR_DONE
#define PROGRESS_DMA_ERRORS (0x1ffU << 5)
#define PROGRESS_DMA_EVENTS (PROGRESS_DMA_DONE | PROGRESS_DMA_ERRORS)
#define PROGRESS_CACHE_LINE 64U
#define PROGRESS_CACHE_CHUNK 4096U
#define PROGRESS_UPDATE_US 100000
#define PROGRESS_DRAIN_US 100000

enum progress_failure { PROGRESS_OK, PROGRESS_CACHE, PROGRESS_DMA, PROGRESS_IRQ, PROGRESS_DRAIN };
static DRAM_ATTR struct {
    volatile bool running;
    bool started;
    volatile enum progress_failure fault;
    dw_gdma_link_list_item_t descriptor;
    volatile uint32_t frames, dma_errors, cache_errors, underruns, dma_status;
    int64_t last_frame_us, max_frame_us, last_draw_us;
    uint32_t draws, sync_bytes;
    unsigned percent;
} progress;
static intr_handle_t progress_irq;
static portMUX_TYPE progress_lock = portMUX_INITIALIZER_UNLOCKED;

static esp_err_t progress_sync_range(void *address, size_t bytes)
{
    if (((uintptr_t)address | bytes) & (PROGRESS_CACHE_LINE - 1)) return ESP_ERR_INVALID_ARG;
    unsigned char *at = address;
    while (bytes) {
        size_t chunk = bytes > PROGRESS_CACHE_CHUNK ? PROGRESS_CACHE_CHUNK : bytes;
        esp_err_t result = esp_cache_msync(at, chunk, ESP_CACHE_MSYNC_FLAG_DIR_C2M);
        if (result != ESP_OK) return result;
        progress.sync_bytes += chunk;
        at += chunk;
        bytes -= chunk;
    }
    return ESP_OK;
}

static void IRAM_ATTR progress_quiesce(void)
{
    progress.running = false;
    dw_gdma_ll_channel_enable_intr_propagation(&DW_GDMA, 0, UINT32_MAX, false);
    dw_gdma_ll_channel_enable_intr_generation(&DW_GDMA, 0, UINT32_MAX, false);
    mipi_dsi_brg_ll_enable_dpi_output(MIPI_DSI_LL_GET_BRG(0), false);
    mipi_dsi_brg_ll_update_dpi_config(MIPI_DSI_LL_GET_BRG(0));
    dw_gdma_ll_channel_enable(&DW_GDMA, 0, false);
}

static void IRAM_ATTR progress_observe_underrun(void)
{
    /* int_st is masked because no bridge IRQ is installed; read raw state. */
    if (MIPI_DSI_LL_GET_BRG(0)->int_raw.val & MIPI_DSI_BRG_LL_EVENT_UNDERRUN) {
        progress.underruns++;
        mipi_dsi_brg_ll_clear_interrupt_status(MIPI_DSI_LL_GET_BRG(0), MIPI_DSI_BRG_LL_EVENT_UNDERRUN);
    }
}

static int IRAM_ATTR progress_rearm(void)
{
    dw_gdma_link_list_item_t *desc = (void *)M5_FB_DESC;
    if (esp_cache_msync(desc, 64, ESP_CACHE_MSYNC_FLAG_DIR_M2C) != ESP_OK) goto cache_error;
    memcpy(desc, &progress.descriptor, sizeof(progress.descriptor));
    if (esp_cache_msync(desc, 64, ESP_CACHE_MSYNC_FLAG_DIR_C2M) != ESP_OK) goto cache_error;
    dw_gdma_ll_channel_set_link_list_head_addr(&DW_GDMA, 0, M5_FB_DESC);
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    dw_gdma_ll_channel_enable(&DW_GDMA, 0, true);
    return 0;
cache_error:
    progress.cache_errors++;
    progress.fault = PROGRESS_CACHE;
    progress_quiesce();
    return -1;
}

static void IRAM_ATTR progress_interrupt(void *unused)
{
    (void)unused;
    uint32_t status = dw_gdma_ll_channel_get_intr_status(&DW_GDMA, 0);
    dw_gdma_ll_channel_clear_intr(&DW_GDMA, 0, status);
    if (!progress.running) return;
    progress_observe_underrun();
    if (status & PROGRESS_DMA_ERRORS) {
        progress.dma_errors++;
        progress.dma_status |= status;
        progress.fault = PROGRESS_DMA;
        progress_quiesce();
    } else if (status & PROGRESS_DMA_DONE) {
        int64_t now = esp_timer_get_time();
        if (progress.last_frame_us && now - progress.last_frame_us > progress.max_frame_us)
            progress.max_frame_us = now - progress.last_frame_us;
        progress.last_frame_us = now;
        progress.frames++;
        (void)progress_rearm();
    }
}

static void progress_pixel(unsigned x,unsigned y,uint16_t color)
{
    /* fbcon rotate:1 maps landscape (x,y) to native (479-y,x). */
    if(x<800 && y<480) ((uint16_t *)M5_FB_BASE)[x*480+(479-y)]=color;
}
void gaijin_display_progress(unsigned percent)
{
    if (!progress.running) return;
    if (percent > 100) percent = 100;
    if (percent <= progress.percent) return;
    int64_t now = esp_timer_get_time();
    if (percent != 100 && now - progress.last_draw_us < PROGRESS_UPDATE_US) return;
    /* Each landscape column is a native row. Only the newly green portion
     * changes; its 88 bytes occupy two aligned 64-byte cache lines. */
    for (unsigned x = 100 + progress.percent * 6; x < 100 + percent * 6; x++) {
        if (!progress.running) return;
        for (unsigned y = 244; y < 288; y++) progress_pixel(x, y, 0x07e0);
        void *row = (uint16_t *)M5_FB_BASE + x * 480 + 192;
        if (progress_sync_range(row, 128) != ESP_OK) {
            portENTER_CRITICAL(&progress_lock);
            progress.cache_errors++;
            progress.fault = PROGRESS_CACHE;
            progress_quiesce();
            portEXIT_CRITICAL(&progress_lock);
            return;
        }
    }
    progress.percent = percent;
    progress.last_draw_us = now;
    progress.draws++;
}
void gaijin_display_progress_start(void)
{
    static const unsigned char letters[4][7]={{17,17,17,31,17,17,17},{14,17,17,17,17,17,14},
        {17,27,21,21,17,17,17},{31,16,16,30,16,16,31}};
    if (!progress_ready || progress.started || progress.fault) return;
    memset(&progress, 0, sizeof(progress));
    progress.started = true;
    memset((void *)M5_FB_BASE,0,M5_FB_BYTES);
    for(unsigned l=0;l<4;l++) for(unsigned y=0;y<7;y++) for(unsigned x=0;x<5;x++)
        if(letters[l][y]&(16>>x)) for(unsigned a=0;a<8;a++) for(unsigned b=0;b<8;b++)
            progress_pixel(304+l*48+x*8+a,136+y*8+b,0xffff);
    for (unsigned y = 240; y < 292; y++) for (unsigned x = 96; x < 704; x++) {
        bool border = y < 244 || y >= 288 || x < 100 || x >= 700;
        progress_pixel(x, y, border ? 0xffff : 0x0841);
    }
    if (progress_sync_range((void *)M5_FB_BASE, M5_FB_BYTES) != ESP_OK) {
        progress.cache_errors++;
        progress.fault = PROGRESS_CACHE;
        goto failed;
    }
    dw_gdma_link_list_item_t *desc = (void *)M5_FB_DESC;
    if (esp_cache_msync(desc, 64, ESP_CACHE_MSYNC_FLAG_DIR_M2C) != ESP_OK) {
        progress.cache_errors++;
        progress.fault = PROGRESS_CACHE;
        goto failed;
    }
    memcpy(&progress.descriptor, desc, sizeof(progress.descriptor));
    /* Linux validates these markers too. Restore the whole descriptor after
     * every hardware writeback instead of trusting stale cached ownership. */
    dw_gdma_ll_lli_set_block_markers(&progress.descriptor, false, true, true);
    if (esp_intr_alloc(ETS_DW_GDMA_INTR_SOURCE,
                       ESP_INTR_FLAG_IRAM | ESP_INTR_FLAG_LEVEL1 | ESP_INTR_FLAG_INTRDISABLED,
                       progress_interrupt, NULL, &progress_irq) != ESP_OK) {
        progress.fault = PROGRESS_IRQ;
        goto failed;
    }
    progress.last_draw_us = esp_timer_get_time();
    progress.draws = 1;
    dw_gdma_ll_channel_enable_intr_generation(&DW_GDMA, 0, UINT32_MAX, false);
    dw_gdma_ll_channel_enable_intr_propagation(&DW_GDMA, 0, UINT32_MAX, false);
    dw_gdma_ll_channel_clear_intr(&DW_GDMA, 0, UINT32_MAX);
    mipi_dsi_brg_ll_clear_interrupt_status(MIPI_DSI_LL_GET_BRG(0), MIPI_DSI_BRG_LL_EVENT_UNDERRUN);
    dw_gdma_ll_channel_enable_intr_generation(&DW_GDMA, 0, PROGRESS_DMA_EVENTS, true);
    dw_gdma_ll_channel_enable_intr_propagation(&DW_GDMA, 0, PROGRESS_DMA_EVENTS, true);
    if (esp_intr_enable(progress_irq) != ESP_OK) {
        progress.fault = PROGRESS_IRQ;
        goto failed;
    }
    /* Prime DMA before DPI starts consuming pixels. The critical section
     * closes the only start/stop race with the completion interrupt. */
    portENTER_CRITICAL(&progress_lock);
    progress.running = true;
    if (!progress_rearm()) {
        mipi_dsi_brg_ll_enable_dpi_output(MIPI_DSI_LL_GET_BRG(0), true);
        mipi_dsi_brg_ll_update_dpi_config(MIPI_DSI_LL_GET_BRG(0));
    }
    portEXIT_CRITICAL(&progress_lock);
    if (!progress.running) goto failed;
    return;
failed:
    (void)gaijin_display_progress_stop();
}
int gaijin_display_progress_stop(void)
{
    if (!progress.started && !progress_irq) return progress.fault ? -1 : 0;
    portENTER_CRITICAL(&progress_lock);
    progress_quiesce();
    portEXIT_CRITICAL(&progress_lock);
    if (progress_irq && esp_intr_disable(progress_irq) != ESP_OK) progress.fault = PROGRESS_IRQ;
    int64_t until = esp_timer_get_time() + PROGRESS_DRAIN_US;
    while (DW_GDMA.chen0.val & 1) {
        if (esp_timer_get_time() >= until) { progress.fault = PROGRESS_DRAIN; break; }
        vTaskDelay(1);
    }
    progress_observe_underrun();
    dw_gdma_ll_channel_clear_intr(&DW_GDMA, 0, UINT32_MAX);
    if (progress_irq) {
        if (esp_intr_free(progress_irq) == ESP_OK) progress_irq = NULL;
        else progress.fault = PROGRESS_IRQ;
    }
    if (progress.started) {
        ESP_LOGI(TAG, "HOME refresh irq v1: frames=%" PRIu32 " underrun-observations=%" PRIu32
                 " dma-errors=%" PRIu32 " cache-errors=%" PRIu32 " dma-status=0x%08" PRIx32
                 " max-frame-us=%" PRId64 " draws=%" PRIu32 " draw-sync-bytes=%" PRIu32
                 " fault=%u irq-released=%u",
                 progress.frames, progress.underruns, progress.dma_errors, progress.cache_errors,
                 progress.dma_status, progress.max_frame_us, progress.draws, progress.sync_bytes,
                 (unsigned)progress.fault, progress_irq == NULL);
    }
    progress.started = false;
    return progress.fault ? -1 : 0;
}

void gaijin_display_progress_test(void)
{
    if (!progress_ready) return;
    ESP_LOGI(TAG, "HOME display test: 5s static, then 5s progress; home files are unchanged");
    gaijin_display_progress_start();
    for (unsigned n = 0; n < 50 && progress.running; n++) vTaskDelay(pdMS_TO_TICKS(100));
    for (unsigned n = 1; n <= 100 && progress.running; n++) {
        vTaskDelay(pdMS_TO_TICKS(50));
        gaijin_display_progress(n);
    }
    (void)gaijin_display_progress_stop();
}

idf_component_register(SRCS "main.c" "boot_contract.c" "handoff.c" "sd.c" "sd_diagnostic.c" "display.c" "home.c" "../common/home_store.c"
    INCLUDE_DIRS "." "../common" "${PROJECT_DIR}/build/home"
    PRIV_REQUIRES badge-bsp libfdt esp_psram esp_driver_sdmmc sdmmc fatfs
                  bootloader_support mbedtls esp_hw_support esp_hal_pmu esp_mm esp_lcd esp_driver_gpio esp_driver_dma esp_lcd_st7701 esp_timer)
if(CONFIG_GAIJIN_USB_EARLYCON)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE GAIJIN_USB_EARLYCON=1)
endif()

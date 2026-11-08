// SPDX-License-Identifier: MIT
#pragma once
#include "esp_err.h"

esp_err_t gaijin_sd_mount(void);
esp_err_t gaijin_sd_unmount(void);
void gaijin_sd_reference(void);

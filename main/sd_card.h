// SPDX-License-Identifier: MIT
#pragma once
#include "sdmmc_cmd.h"
/* Private card access while the IDF loader owns the mounted volume. */
sdmmc_card_t *gaijin_sd_card(void);

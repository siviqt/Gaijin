// SPDX-License-Identifier: MIT
#pragma once
#include <stddef.h>
#include <stdint.h>
/* Consume the standard AppFS argument before any SD mutation. */
int gaijin_home_boot_selection(const void *custom, size_t bytes, char argument[128]);
int gaijin_home_prepare(void *dtb, int appfs_fd, const char *argument);
void gaijin_display_progress_start(void);
void gaijin_display_progress(unsigned percent);
int gaijin_display_progress_stop(void);
void gaijin_display_progress_test(void);

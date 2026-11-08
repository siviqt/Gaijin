GAIJIN_ABI_CHECK_VERSION = 1
GAIJIN_ABI_CHECK_SITE = $(BR2_EXTERNAL_GAIJIN_PATH)/package/gaijin-abi-check
GAIJIN_ABI_CHECK_SITE_METHOD = local
GAIJIN_ABI_CHECK_LICENSE = MIT
GAIJIN_ABI_CHECK_LICENSE_FILES = COPYING
GAIJIN_ABI_CHECK_FLAT_STACKSIZE = 16384

define GAIJIN_ABI_CHECK_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra -Werror -fno-builtin \
		-o $(@D)/gaijin-abi-check $(@D)/abi-check.c $(@D)/uaccess-probe.c $(TARGET_LDFLAGS) -static
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra -Werror \
		-o $(@D)/m5-umode-probe $(@D)/umode-probe.c $(TARGET_LDFLAGS) -static
endef

define GAIJIN_ABI_CHECK_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/gaijin-abi-check $(TARGET_DIR)/usr/bin/gaijin-abi-check
	$(INSTALL) -D -m 0755 $(@D)/m5-umode-probe $(TARGET_DIR)/usr/bin/m5-umode-probe
endef

$(eval $(generic-package))

GAIJIN_STATE_VERSION = 1
GAIJIN_STATE_SITE = $(BR2_EXTERNAL_GAIJIN_PATH)/package/gaijin-state
GAIJIN_STATE_SITE_METHOD = local
GAIJIN_STATE_LICENSE = MIT
GAIJIN_STATE_LICENSE_FILES = COPYING
GAIJIN_STATE_FLAT_STACKSIZE = 32768

define GAIJIN_STATE_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra -Werror \
		-o $(@D)/gaijin-state-map $(@D)/state.c $(TARGET_LDFLAGS) -static
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c11 -Wall -Wextra -Werror \
		-o $(@D)/gaijin-power $(@D)/power.c $(TARGET_LDFLAGS) -static
endef

define GAIJIN_STATE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/gaijin-state-map $(TARGET_DIR)/sbin/gaijin-state-map
	$(INSTALL) -D -m 0755 $(@D)/gaijin-power $(TARGET_DIR)/sbin/gaijin-power
	$(RM) $(TARGET_DIR)/sbin/reboot
	for name in shutdown poweroff halt; do ln -sf gaijin-power $(TARGET_DIR)/sbin/$$name; done
endef

$(eval $(generic-package))

# Buildroot 2025.02.15 embeds quotes in ELF2FLT_FLAGS for custom flat stacks.
# BusyBox embeds those flags inside another double-quoted CFLAGS/EXTRA_LDFLAGS
# assignment, exposing the space before -s to the shell. Pass each elf2flt
# option separately so both the BusyBox environment and nested make survive.
ifeq ($(BR2_BINFMT_FLAT),y)
ELF2FLT_FLAGS = $(if $($(PKG)_FLAT_STACKSIZE),\
	-Wl$(comma)-elf2flt=-r -Wl$(comma)-elf2flt=-s$($(PKG)_FLAT_STACKSIZE),\
	-Wl$(comma)-elf2flt=-r)
endif

# BusyBox's Makefile.flags appends FLTFLAGS=-s 16000, which ld-elf2flt reads
# after its command-line options. A make command-line assignment takes
# precedence over that default and is exported to the linker wrapper.
BUSYBOX_MAKE_OPTS += FLTFLAGS="-s $(BUSYBOX_FLAT_STACKSIZE)"

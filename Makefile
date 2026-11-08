SHELL := /bin/bash
export PYTHONDONTWRITEBYTECODE := 1
IDF_PATH ?= $(HOME)/.espressif/v6.0.2/esp-idf
BADGELINK ?= ../../tanmatsu-sdk/tools/badgelink.sh
.PHONY: all build loader kernel userspace package verify sdcard m1 m2 m3 test install run
all: build
build: loader kernel userspace
	python3 tools/package_current.py
loader:
	IDF_PATH="$(IDF_PATH)" bash tools/build_loader.sh
kernel:
	python3 tools/build_kernel.py $(KERNEL_ARGS)
userspace:
	python3 tools/build_userspace.py $(USERSPACE_ARGS)
package:
	python3 tools/package_current.py
verify:
	python3 tools/package_current.py --check
sdcard m1 m2 m3: build
test:
	python3 -m unittest discover -s tests -v
# Install the exact staged loader; never rebuild one component during upload.
install:
	python3 tools/package_current.py --install --badgelink "$(BADGELINK)"
run:
	"$(BADGELINK)" start com.github.siviq.gaijin

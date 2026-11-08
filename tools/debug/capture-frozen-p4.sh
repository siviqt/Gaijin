#!/usr/bin/env bash
set -euo pipefail
work=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$work/../.." && pwd)
openocd=${GAIJIN_OPENOCD:-openocd}
search=()
if [[ -d "$openocd" ]]; then
    search=(-s "$openocd/share/openocd/scripts")
    openocd="$openocd/bin/openocd"
fi
: "${GAIJIN_DEBUG_SERIAL:?Set GAIJIN_DEBUG_SERIAL to the target board USB/JTAG serial}"
if [[ ! "$GAIJIN_DEBUG_SERIAL" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]]; then
    printf 'GAIJIN_DEBUG_SERIAL must contain six colon-separated hexadecimal bytes.\n' >&2
    exit 2
fi
output=${GAIJIN_TRACE_DIR:-$root/build/boot-trace}
mkdir -p "$output"
log="$output/p4-freeze-$(date +%Y%m%d-%H%M%S).log"
printf 'Writing capture to %s\n' "$log"
sudo -n timeout 15s "$openocd" "${search[@]}" \
    -c 'set ESP_ONLYCPU 1; set ESP_FLASH_SIZE 0; gdb port disabled; tcl port disabled; telnet port disabled' \
    -f board/esp32p4-builtin.cfg -c "adapter serial $GAIJIN_DEBUG_SERIAL" \
    -f "$work/capture-frozen-p4.tcl" 2>&1 | tee "$log"
if ! rg -qi '^pc = 0x[[:xdigit:]]+[[:space:]]*$' "$log"; then
    printf 'No program counter was recorded. Check %s before repeating the boot test.\n' "$log" >&2
    exit 1
fi

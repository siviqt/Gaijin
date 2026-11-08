Application 0.5.19 / AppFS revision 33 includes the unnamed `vi` correction.
On revision 30 the device tests confirmed that named files open successfully,
while bare `vi` freezes both consoles. The user confirmed all revision-33 device
checks passed on 8 November 2026, including the editor and user-access checks.
The LCD flicker investigation remains a separate follow-up.

BusyBox patch `0002-vi-allow-unnamed-buffer.patch` treats a null filename as an
empty buffer and skips the file-open request. Kernel patch
`0002-esp32p4-user-access.patch` overrides the generic NOMMU address check for
ESP32-P4: valid user buffers must fit between `PAGE_OFFSET` and `high_memory`.
The check rejects low unmapped addresses, MMIO, ROM and arithmetic overflow.
It does not provide per-process memory protection on NOMMU.

The same upper bound is supplied as `TASK_SIZE_MAX`. Linux's existing generic
`strncpy_from_user()` and `strnlen_user()` then limit their scan to available
RAM and use `user_read_access_begin()` to validate it. A short NUL-terminated
string near the end of RAM still succeeds even if the requested maximum is
larger. The architecture's checked scalar, copy and clear operations use the
same address check. No privilege, CLIC, panel timing or font change is involved.

The host regressions execute the actual pinned generic Linux string algorithms
with the platform check, testing null/low/MMIO/end-of-RAM pointers, wrapping
lengths, truncation and valid boundary strings. An unchecked-string negative
control must fail. Native BusyBox tests apply the shipped vi patch and use
fatal undefined-behavior checks while editing, saving and reopening named and
unnamed buffers on 80x24 and 80x26 terminals. Another test links the actual built
RV32 kernel string-copy objects into a freestanding harness and runs them under
QEMU with valid, invalid and boundary addresses. These tests do not emulate P4
bus behavior or replace device validation.

After installing revision 33, perform the first pointer tests with
persistence inactive:

```sh
cd /tmp
gaijin-persist stop
gaijin-persist status
gaijin-abi-check --uaccess
```

The probe prints `Uaccess PASS` only after invalid pathname, read and write
addresses return `EFAULT`, followed by successful valid RAM-file I/O. Use it
only on revision 31 or newer: the revision-30 ABI checker does not implement this
option. The normal ABI smoke tests remain available without the option.

Then open bare `vi`, insert a short line, press Escape, save with
`:w /tmp/vi-test`, and exit with `:q`. Reopen `vi /tmp/vi-test` and verify the
line. Repeat on the LCD keyboard and USB console, with scrolling and normal
editing. Capture JTAG before a manual power cut if a whole-device freeze recurs.
The existing capture tool may disturb or recover a frozen CPU, so retain its
first raw state as well as the full attachment.

After the pointer/editor tests pass, enable persistence, save a file under
`/root`, and alternate `shutdown` and `poweroff` across multiple boots. Verify
active persistence and the file after each launch. Investigate remaining LCD
flashes separately by measuring refresh-rearm and cache-maintenance latency;
zero software error counters alone do not establish visual correctness.

Revision 31 is superseded by the [revision-32 application identifier repair](revision32.md).
The identifier below records the typo shipped in revision 31; the correct current
identifier is `com.github.siviqt.gaijin`.

Gaijin 0.5.17 / AppFS revision 31 is the November 8, 2026 source and package
update. Its application identifier is `com.github.siviq.gaijin`; the SD assets,
loader, runtime scripts, package metadata and installation commands use this
same identifier. The metadata declares every boot file and license notice as
an external-storage asset.

The device tests on revision 30 passed for the previously requested storage
and power checks. The user also confirmed that `vi /path/to/file` works, while
bare `vi` freezes both consoles. Revision 31 includes the null-filename guard
and kernel NOMMU pointer checks described in the
[user-access guide](../guides/user-access.md). It retains native application
U-mode, the CLIC correction, the accepted 10x18 font and the existing display
timing. Hardware validation of this new complete image remains pending.

All three components were rebuilt. The pinned userspace verifier accepts the
RV32 soft-float bFLT binaries, startup files and matching SquashFS/initramfs.
All 93 host tests pass with no skips, including checks of the complete packaged
set. The 60 RV32 CLIC scenarios and their negative control pass. Actual compiled
kernel string functions pass the RV32/QEMU pointer-boundary tests, and native
BusyBox tests cover named and unnamed edit/save/reopen at both console sizes.
The compiled target `file_insert` routine branches around `open` when its
filename is null. These checks do not replace Tanmatsu hardware validation.

The source and application trees pass the publication audit. Public captures
have account, host, card and device identifiers redacted, and the original
specification has its private conversation reference removed. Third-party
credits and licenses are retained. The kernel uses `builder@gaijin`, and the
userspace report contains intermediate basenames rather than local paths.

For first device validation, remove the old cached application entry, then
launch Gaijin from the SD card and confirm version 0.5.17 / revision 31. Use
`cd /tmp`, stop persistence, run `gaijin-abi-check --uaccess`, then test bare
`vi`, save a RAM file and reopen it by filename. Repeat on USB and the LCD.
After those checks pass, enable persistence and repeat file-retention tests
across `shutdown` and `poweroff`. The detailed procedure is in the
[user-access guide](../guides/user-access.md).

LCD flicker during boot and rare flashes during normal use or shutdown remain
open. No visual correctness or M5 hardware acceptance is claimed by this release.

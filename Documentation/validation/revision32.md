# Revision 32 application identifier repair

Gaijin 0.5.18 / AppFS revision 32 uses `com.github.siviqt.gaijin` throughout
the loader, SD root startup, SD diagnostic, packaging, Launcher commands and
installation documentation. It retains the revision-31 vi and NOMMU user-access
corrections and the Linux 6.18.35-gaijin-m5 kernel configuration.

## Failure and correction

The revision-31 application repository and freshly copied card used
`apps/com.github.siviqt.gaijin`, while the loader and initramfs contained
`/sd/apps/com.github.siviq.gaijin/boot`. The required manifest was therefore
absent at the loader's compiled path. The loader reports
`boot/manifest.bin missing; reset to launcher` before entering Linux.

All 28 copied files were present, the loader/kernel/initramfs/rootfs hashes
matched the revision-31 release, and all manifest entries and the rootfs checksum
were valid. The persistent ext2 image passed the supported-format, clean-flag
and read-only filesystem checks. The directory disagreement was sufficient to
prevent boot despite valid file contents.

Revision 32 rebuilds the loader and both userspace filesystem images for the
correct identifier. Its increased executable revision allows Launcher to detect
the replacement under the same correct application slug. User state remains
at `/gaijin/userdata.ext2`, outside the replaced application directory.

Packaging now rejects a wrong application directory name, an outdated loader
path or stale SD root/test scripts in the initramfs. The independent
`tools/package_current.py --check-app DIRECTORY` command checks the actual
repository or SD installation along with its manifest and userspace hashes.
Regression tests cover the previous typo and a renamed installation directory.

## Device validation

Host verification results are recorded in
[the revision-32 report](evidence/m5/m5-rev32-host.json).
All 95 host tests pass with no skips. The userspace ABI/filesystem verifier,
60 RV32 CLIC scenarios and their negative control, actual kernel string-object
tests, repository metadata schema and public-tree audit pass.
Hardware boot validation remains pending. Launch the SD application's revision-32
entry, confirm that it loads Image, DTB and initramfs and reaches both login
consoles, then run `m1-smoke`, `m2-sd-test`, `gaijin-persist status` and
`m5-umode-probe`. Continue the [user-access checks](../guides/user-access.md).
The remaining LCD flicker and M5 hardware acceptance stay open.

## Metadata and Penguin icon update

The manually edited repository and SD metadata contained trailing commas after
the last asset, application entry and top-level property. Strict JSON parsing
rejects those commas. The source URL was already present in the committed source;
it is retained near the top of the corrected metadata as
`"repository_url": "https://github.com/siviqt/Gaijin"`.

Only the Penguin `icon16.png`, `icon32.png` and `icon64.png` are shipped, directly
in the application directory. Metadata paths, declared assets, source files,
package checks and installation instructions use that layout. The metadata is
checked against the upstream application repository schema. Firmware and boot
asset bytes retain their verified revision-32 hashes.

#!/bin/sh
set -e
# Drop retired diagnostic tools from incremental Buildroot target trees.
rm -f "$1/sbin/gaijin-console" "$1/sbin/gaijin-boot-report"
# Retain explicit mountpoints in both SquashFS and rescue cpio.
for dir in sd persist home newroot run tmp root usr/local proc sys dev; do
    mkdir -p "$1/$dir"
done

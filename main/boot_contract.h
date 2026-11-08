// SPDX-License-Identifier: MIT
#pragma once
#include <stddef.h>
#include <stdint.h>

#define GAIJIN_APP_ID "com.github.siviq.gaijin"

#define M0_RAM_BASE UINT32_C(0x48000000)
#define M0_RAM_SIZE UINT32_C(0x02000000)
#define M0_KERNEL_LIMIT UINT32_C(0x00800000)
#define M0_DTB_BASE UINT32_C(0x48800000)
#define M0_DTB_CAPACITY UINT32_C(0x00010000)
#define M0_INITRD_BASE UINT32_C(0x49000000)
#define M0_INITRD_LIMIT UINT32_C(0x01000000)
// Gap between DTB and initrd; reserved in the DT, never part of the IDF heap.
#define M5_FB_BASE UINT32_C(0x48e00000)
#define M5_FB_BYTES UINT32_C(768000)
#define M5_FB_DESC (M5_FB_BASE + M5_FB_BYTES)
#define M5_FB_RESERVE UINT32_C(0x000bc000)
#define M0_MANIFEST_BYTES 140
#define M0_BOOTARGS "earlycon=esp32p4usbjtag,mmio32,0x500d2000 console=tty0 console=ttyUJ0,115200n8 fbcon=rotate:1,font:10x18 consoleblank=0 rdinit=/init loglevel=7 panic=0"

struct m0_asset { uint32_t bytes; uint8_t sha256[32]; };
struct m0_manifest { struct m0_asset image, dtb, initrd; };

// Serialized manifest uses little-endian fields; no native struct is read from disk.
int m0_parse_manifest(const uint8_t *data, size_t size, struct m0_manifest *out);
int m0_check_image(const uint8_t *header, size_t header_bytes,
                   uint32_t file_bytes, uint32_t *memory_bytes);
int m0_prepare_dtb(void *dtb, size_t file_bytes, uint32_t initrd_bytes);

int m5_enable_display(void *dtb);

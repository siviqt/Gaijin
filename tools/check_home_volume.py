#!/usr/bin/env python3
"""Read only a FAT16/32 partition's home-provisioning metadata. Never repair it."""
import argparse
import json
import os
import struct


def inspect_partition(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        def sector(number):
            data = os.pread(fd, 512, number * 512)
            if len(data) != 512:
                raise ValueError(f'short read at partition sector {number}')
            return data
        boot = sector(0)
        if boot[510:512] != b'\x55\xaa' or struct.unpack_from('<H', boot, 11)[0] != 512:
            raise ValueError('expected a FAT partition device with 512-byte sectors (not the whole disk)')
        fat16 = struct.unpack_from('<H', boot, 22)[0]
        fat32 = not fat16
        reserved = struct.unpack_from('<H', boot, 14)[0]
        fats = boot[16]
        size = struct.unpack_from('<I', boot, 36)[0] if fat32 else fat16
        if not reserved or fats not in (1, 2) or not size:
            raise ValueError('unsupported FAT geometry')
        state = boot[65 if fat32 else 37]
        extended = struct.unpack_from('<H', boot, 40)[0] if fat32 else 0
        entries = []
        for i in range(fats):
            data = sector(reserved + i * size)
            entries.append(struct.unpack_from('<I' if fat32 else '<H', data, 4 if fat32 else 2)[0])
        mask = 0x0c000000 if fat32 else 0xc000
        meaningful = 0x0fffffff if fat32 else 0xffff
        reasons = []
        if state & 1:
            reasons.append('boot-sector dirty flag is set (expected while mounted read/write on a host)')
        if extended & 0x80:
            reasons.append('FAT mirroring is disabled')
        if any(entry & mask != mask for entry in entries):
            reasons.append('FAT[1] reports an unclean shutdown or I/O error')
        if len({entry & meaningful for entry in entries}) != 1:
            reasons.append('FAT[1] meaningful bits differ between copies')
        return {
            'filesystem': 'FAT32' if fat32 else 'FAT16',
            'sector_bytes': 512, 'cluster_sectors': boot[13],
            'reserved_sectors': reserved, 'fat_copies': fats, 'fat_sectors': size,
            'boot_state': f'0x{state:02x}', 'extended_flags': f'0x{extended:04x}',
            'fat_entry1': [f'0x{entry:08x}' for entry in entries],
            'observations': reasons,
            'read_only': True,
            'note': 'No repair or full consistency check. Host mounting can change the boot dirty flag.'
        }
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('partition', help='partition device or partition image, not a whole disk')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_partition(args.partition), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Cannot inspect FAT metadata: {exc}\n')


if __name__ == '__main__':
    main()

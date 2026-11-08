#!/usr/bin/env python3
"""Read-only check of the exact ext2 format and clean flag required by Gaijin."""
import argparse
from pathlib import Path
import struct
import subprocess


def check_header(image):
    image = Path(image)
    size = image.stat().st_size
    with image.open('rb') as stream:
        stream.seek(1024)
        data = stream.read(1024)
    if len(data) != 1024:
        raise ValueError('truncated ext2 superblock')
    word = lambda offset: struct.unpack_from('<H', data, offset)[0]
    dword = lambda offset: struct.unpack_from('<I', data, offset)[0]
    if (not 16 * 1024 * 1024 <= size <= 256 * 1024 * 1024 or
            word(56) != 0xef53 or dword(24) != 0 or dword(76) != 1 or
            word(88) != 128 or (dword(92), dword(96), dword(100)) != (0, 2, 3) or
            dword(4) * 1024 != size or data[120:132] != b'GAIJIN-STATE'):
        raise ValueError('image does not match the supported Gaijin ext2 format')
    if word(58) != 1:
        raise ValueError(f'ext2 clean flag is {word(58):#06x}; offline e2fsck repair required before use')
    return size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    try:
        check_header(args.image)
        subprocess.run(['/usr/sbin/e2fsck', '-fn', str(args.image)], check=True)
        print('Gaijin state PASS: supported format, clean flag set, filesystem check passed')
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Gaijin state check failed: {exc}\n')


if __name__ == '__main__':
    main()

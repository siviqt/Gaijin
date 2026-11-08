#!/usr/bin/env python3
"""Create a new offline Gaijin ext2 state image; never overwrite an existing path."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from check_state import check_header


def create(output, size_mib=32):
    output = Path(output)
    if not 16 <= size_mib <= 256:
        raise ValueError('state size must be 16..256 MiB (FAT32-safe)')
    if output.exists() or output.is_symlink():
        raise ValueError('refusing to replace existing state; retain it for recovery')
    mke2fs = shutil.which('mke2fs') or '/sbin/mke2fs'
    e2fsck = shutil.which('e2fsck') or '/sbin/e2fsck'
    debugfs = shutil.which('debugfs') or '/sbin/debugfs'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.gaijin-state-') as work:
        root = Path(work) / 'root'
        (root / 'home/root').mkdir(parents=True, mode=0o700)
        (root / 'settings').mkdir()
        (root / 'state-version').write_text('gaijin-state-v1\n')
        image = Path(work) / 'userdata.ext2'
        # Allocate every byte: later writes must never extend or reallocate FAT.
        with image.open('xb') as stream:
            chunk = bytes(1024*1024)
            for _ in range(size_mib):
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        env = dict(os.environ, E2FSPROGS_FAKE_TIME='1793232000')
        subprocess.run([mke2fs, '-q', '-t', 'ext2', '-b', '1024', '-I', '128',
                        '-O', 'none,filetype,sparse_super,large_file', '-L', 'GAIJIN-STATE',
                        '-m', '0', '-E', 'root_owner=0:0', '-d', str(root), str(image)],
                       check=True, env=env)
        # mke2fs -d preserves host ownership. Normalize the explicit state tree.
        commands = ''.join(f'set_inode_field {name} {field} 0\n'
                           for name in ('/home', '/home/root', '/settings', '/state-version')
                           for field in ('uid', 'gid'))
        subprocess.run([debugfs, '-w', '-f', '/dev/stdin', str(image)],
                       input=commands, text=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run([e2fsck, '-fn', str(image)], check=True)
        check_header(image)
        # Exclusive creation also protects against a concurrent creator.
        try:
            with output.open('xb') as dest, image.open('rb') as source:
                shutil.copyfileobj(source, dest)
                dest.flush()
                os.fsync(dest.fileno())
        except FileExistsError:
            raise ValueError('state appeared during creation; refusing to replace it') from None
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='SD root/gaijin/userdata.ext2, outside apps/')
    parser.add_argument('--size-mib', type=int, default=32)
    args = parser.parse_args()
    try:
        print(create(args.output, args.size_mib))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'State creation failed: {exc}\n')


if __name__ == '__main__':
    main()

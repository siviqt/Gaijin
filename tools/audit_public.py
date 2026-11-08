#!/usr/bin/env python3
"""Check a source/release tree for local-machine identifiers before sharing."""
import argparse
import os
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

EXCLUDED = {'.git', '.codex', '.agents', '.aws', '__pycache__', 'build',
            'managed_components', 'backups'}
PATTERNS = {
    'personal home path': re.compile(rb'/home/(?!user/|m3-proof/|root(?:[/\s"\']|$))[A-Za-z0-9_.-]+/'),
    'device MAC/serial': re.compile(rb'(?<![\w:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![\w:])'),
    'device UUID': re.compile(rb'\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b'),
    'local kernel builder': re.compile(rb'(?:^|[\x00\n]|\]\s+)Linux version[^\n]*\((?!builder@gaijin\)|user@host\))[^\s()]+@[^\s()]+\)'),
}


def public_files(root):
    for directory, children, files in os.walk(root):
        children[:] = sorted(name for name in children if name not in EXCLUDED)
        for name in sorted(files):
            yield Path(directory) / name


def audit(root):
    findings = []
    for path in public_files(root):
        if not path.is_file():
            continue
        if path.is_symlink():
            findings.append((str(path.relative_to(root)), 'symlink outside public-file inventory'))
            continue
        payloads = [(path.relative_to(root).as_posix(), path.read_bytes())]
        if path.suffix == '.docx':
            with zipfile.ZipFile(path) as document:
                payloads += [(path.relative_to(root).as_posix() + ':' + name,
                              ''.join(ET.fromstring(document.read(name)).itertext()).encode())
                             for name in document.namelist() if name.endswith('.xml')]
        for name, data in payloads:
            for label, pattern in PATTERNS.items():
                if pattern.search(data):
                    findings.append((name, label))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    findings = audit(args.root)
    for name, label in findings:
        print(f'{name}: {label}')
    if findings:
        parser.exit(1, f'Public-tree audit failed: {len(findings)} finding(s)\n')
    print('Public-tree audit PASS: no local home paths, device identifiers or personal kernel builder detected')


if __name__ == '__main__':
    main()

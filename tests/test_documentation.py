"""Keep source and packaged documentation navigable and public-only."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from package_current import release_files
from audit_public import audit


def heading_ids(text):
    counts = {}
    ids = set()
    for heading in re.findall(r'^#{1,6}\s+(.+)$', text, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        duplicate = counts.get(slug, 0)
        counts[slug] = duplicate + 1
        ids.add(f'{slug}-{duplicate}' if duplicate else slug)
    return ids


class DocumentationTests(unittest.TestCase):
    def test_public_source_has_no_local_machine_identifiers(self):
        self.assertEqual(audit(ROOT), [])

    def test_public_audit_works_on_an_exported_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / 'tools/audit_public.py'
            tool.parent.mkdir()
            tool.write_bytes((ROOT / 'tools/audit_public.py').read_bytes())
            self.assertEqual(audit(root), [])
            (root / 'kernel-banner.txt').write_text('Linux version test (developer@workstation)\n')
            self.assertEqual(audit(root), [('kernel-banner.txt', 'local kernel builder')])

    def test_links_work_in_source_and_release(self):
        inventory = release_files()
        self.assertIn('README.md', inventory)
        self.assertIn('INSTALL.md', inventory)
        for name, source in inventory.items():
            if source.suffix != '.md':
                continue
            for target in re.findall(r'\]\(([^)]+)\)', source.read_text()):
                link = urlsplit(target)
                if link.scheme or link.netloc:
                    continue
                with self.subTest(document=name, target=target):
                    destination = (source.parent / unquote(link.path)).resolve() if link.path else source
                    self.assertTrue(destination.is_file(), f'missing source target: {destination}')
                    release_name = destination.relative_to(ROOT).as_posix()
                    self.assertIn(release_name, inventory, 'source link missing from installation bundle')
                    if link.fragment and destination.suffix == '.md':
                        self.assertIn(unquote(link.fragment), heading_ids(destination.read_text()))

    def test_nested_release_documents_exclude_private_and_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('Documentation/guides/input.md',
                         'Documentation/validation/evidence/release/console.log',
                         'Documentation/validation/evidence/release/current.json',
                         'Documentation/reference/specification.docx',
                         'Documentation/.draft/notes.md',
                         'Documentation/guides/notes.md.bak',
                         'backups/private.json'):
                source = root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b'fixture')
            (root / 'Documentation/private.json').symlink_to(root / 'backups/private.json')
            with patch('package_current.ROOT', root):
                inventory = release_files()
            for name in ('Documentation/guides/input.md',
                         'Documentation/validation/evidence/release/console.log',
                         'Documentation/validation/evidence/release/current.json',
                         'Documentation/reference/specification.docx'):
                self.assertIn(name, inventory)
            for name in ('Documentation/.draft/notes.md', 'Documentation/guides/notes.md.bak',
                         'Documentation/private.json', 'backups/private.json'):
                self.assertNotIn(name, inventory)

    def test_live_evidence_locations_and_hashes(self):
        lock = json.loads((ROOT / 'sources.lock.json').read_text())

        def check(value):
            if isinstance(value, dict):
                if 'logs' in value and 'local_inventory' in value:
                    directory = (ROOT / value['local_inventory']).parent
                    for name, expected in value['logs'].items():
                        with self.subTest(log=name):
                            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), expected)
                for key, item in value.items():
                    if isinstance(item, str) and item.startswith('Documentation/'):
                        with self.subTest(field=key, path=item):
                            source = ROOT / item
                            self.assertTrue(source.is_file())
                            expected = value.get(key + '_sha256')
                            if expected:
                                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected)
                    else:
                        check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(lock)

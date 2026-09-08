import tempfile
import unittest
from pathlib import Path

from prepare_public_release import write_canonical_redirects


class CanonicalRedirectTests(unittest.TestCase):
    def page(self, root, slug, target):
        path = root / 'nfl' / slug / 'index.html'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'<link rel="canonical" href="https://lineupbeat.com/nfl/{target}/">')

    def test_exact_aliases_are_permanent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.page(root, 'old-player', 'new-player')
            self.page(root, 'new-player', 'new-player')
            (root / '_redirects').write_text('/existing /kept 301\n')
            self.assertEqual(write_canonical_redirects(root), 1)
            first = (root / '_redirects').read_text()
            self.assertIn('/nfl/old-player /nfl/new-player/ 301', first)
            self.assertIn('/nfl/old-player/ /nfl/new-player/ 301', first)
            self.assertIn('/existing /kept 301', first)
            write_canonical_redirects(root)
            self.assertEqual(first, (root / '_redirects').read_text())

    def test_missing_target_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.page(root, 'old-player', 'missing')
            with self.assertRaisesRegex(RuntimeError, 'target missing'):
                write_canonical_redirects(root)

    def test_redirect_chains_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.page(root, 'old-player', 'new-player')
            self.page(root, 'new-player', 'third-player')
            with self.assertRaisesRegex(RuntimeError, 'redirect chain|target missing'):
                write_canonical_redirects(root)


if __name__ == '__main__':
    unittest.main()

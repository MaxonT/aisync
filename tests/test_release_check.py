"""tools/release_check.py must pass on the tree (version everywhere, CI glob, npm name in the README)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseCheckTest(unittest.TestCase):
    def test_tree_passes_the_release_checks(self):
        spec = importlib.util.spec_from_file_location("release_check", ROOT / "tools" / "release_check.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        failed = [label for label, ok, _ in mod.checks(False) if not ok]
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()

"""The npm launcher (npm/) runs under Node's built-in test runner; this file calls it so the suite covers it.
Skipped when node is not installed."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NpmWrapperTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_tests_pass(self):
        r = subprocess.run(["node", "--test", "npm/test/shim.test.js"], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])

    def test_npm_version_matches_pyproject(self):
        import json
        import tomllib
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        pkg = json.loads((ROOT / "npm/package.json").read_text())
        self.assertEqual(pkg["version"], pyproject["version"])
        self.assertEqual(pkg["name"], "@mingyang2026/aisync-cli")


if __name__ == "__main__":
    unittest.main()

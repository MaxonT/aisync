"""Build the wheel, install it into a fresh venv, run the installed `aisync` (demo, verify, a real hub).
Uses the host's setuptools when present (offline); otherwise pip fetches it in an isolated build."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WheelTest(unittest.TestCase):
    def test_build_install_run(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wheel = [sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", d / "dist", str(ROOT)]
            r = None
            if importlib.util.find_spec("setuptools"):             # offline first; some distro builds can't
                r = subprocess.run([*wheel, "--no-build-isolation"], capture_output=True, text=True)
            if r is None or r.returncode != 0:
                r = subprocess.run(wheel, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            wheels = list((d / "dist").glob("aisync-0.1.2-*.whl"))
            self.assertEqual(len(wheels), 1, list((d / "dist").iterdir()))
            subprocess.run([sys.executable, "-m", "venv", d / "venv"], check=True)
            pip, exe = d / "venv/bin/pip", d / "venv/bin/aisync"
            subprocess.run([pip, "install", "-q", "--no-deps", "--no-index", str(wheels[0])], check=True)
            env = {**os.environ, "AISYNC_HOME": str(d / "hub"), "AISYNC_NO_NOTIFY": "1",
                   "GIT_CONFIG_GLOBAL": str(d / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
            run = lambda *a, **kw: subprocess.run([exe, *map(str, a)], env=env, capture_output=True, text=True, **kw)

            self.assertEqual(run("--version").stdout.strip(), "aisync 0.1.2")
            out = run("demo", "--dir", d / "demo")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("chain intact", out.stdout)
            self.assertIn("CONFLICT codex", out.stdout)
            demo_env = {**env, "AISYNC_HOME": str(d / "demo")}
            v = subprocess.run([exe, "verify"], env=demo_env, capture_output=True, text=True)
            self.assertEqual((v.returncode, v.stdout.strip()), (0, "journal chain intact"))
            self.assertFalse((d / "hub").exists())             # demo never touches the configured hub

            copy = d / "copy.md"
            copy.write_text("<!-- owner-preferences:start -->\n- r\n<!-- owner-preferences:end -->\n")
            self.assertEqual(run("init", "--seed-from", copy, "--target", f"t={copy}").returncode, 0)
            self.assertEqual(run("log", "-a", "codex", "-m", "wheel works").returncode, 0)
            self.assertEqual(run("rule", "add", "-a", "codex", "always test the wheel too").returncode, 0)
            self.assertEqual(run("sync").returncode, 0)
            self.assertIn("always test the wheel too", copy.read_text())
            self.assertEqual(run("claim", "-a", "codex", "-p", "x", "-m", "w").returncode, 0)
            self.assertEqual(run("claim", "-a", "cursor", "-p", "x", "-m", "w").returncode, 3)
            self.assertEqual(run("status").returncode, 0)
            self.assertEqual(run("verify").returncode, 0)


if __name__ == "__main__":
    unittest.main()

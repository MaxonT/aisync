"""install.sh must report the copy it just installed (by path), not whatever `aisync` comes first on PATH,
and must warn when an older aisync is first. Offline: a fake uv installs a fake aisync; no network."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "docs" / "install.sh"


def script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class InstallReportTest(unittest.TestCase):
    def test_reports_the_new_copy_and_warns_about_an_older_one_first_on_path(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            home, uvbin, oldbin = d / "home", d / "uvbin", d / "oldbin"
            for p in (home, uvbin, oldbin):
                p.mkdir()
            # fake uv: `uv tool dir --bin` names uvbin; `uv tool install ...` drops a fake aisync 9.9.9 there
            script(d / "uv", f'if [ "$1" = tool ] && [ "$2" = dir ]; then echo "{uvbin}"; exit 0; fi\n'
                             f'if [ "$1" = tool ] && [ "$2" = install ]; then\n'
                             f'  printf \'#!/bin/sh\\necho "aisync 9.9.9"\\n\' > "{uvbin}/aisync"; chmod +x "{uvbin}/aisync"; exit 0\n'
                             f'fi\nexit 1\n')
            script(oldbin / "aisync", 'echo "aisync 0.1.0"\n')           # the older copy, first on PATH
            wheel = d / "aisync_cli-9.9.9-py3-none-any.whl"
            wheel.write_text("placeholder")
            # the running Python (3.11+) must be the python3 the script finds; the system python3 is 3.9
            py_dir = os.path.dirname(os.path.realpath(sys.executable))
            env = {"HOME": str(home), "PATH": f"{d}:{oldbin}:{py_dir}:/usr/bin:/bin:/usr/sbin:/sbin"}
            r = subprocess.run(["bash", str(INSTALL), "--from", str(wheel)], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f"installed: aisync 9.9.9 at {uvbin}/aisync", r.stdout)
            self.assertIn(f"note: another aisync comes first on PATH: {oldbin}/aisync (aisync 0.1.0)", r.stdout)
            self.assertNotIn("installed: aisync 0.1.0", r.stdout)


if __name__ == "__main__":
    unittest.main()

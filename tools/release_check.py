#!/usr/bin/env python3
"""What: pre-release checks for aisync: the version is the same everywhere, the CI wheel glob and the npm
launcher match it, the npm README line names the npm package, and the release tag is not taken yet.
Why: each of these broke once in a real release (CI glob after the rename, npm name vs README, a tag reused).
How: python3 tools/release_check.py [--tag]   exit 0 = all good; --tag also fails if vX.Y.Z already exists.
"""
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


def checks(want_tag_free: bool):
    out = []
    version = tomllib.loads(read("pyproject.toml"))["project"]["version"]
    name = tomllib.loads(read("pyproject.toml"))["project"]["name"]
    init = re.search(r'__version__ = "([^"]+)"', read("ai_sync/__init__.py")).group(1)
    npm = json.loads(read("npm/package.json"))
    install_sh = re.search(r'^VERSION="([^"]+)"', read("docs/install.sh"), re.M).group(1)
    out.append(("version: pyproject == __init__", version == init, f"{version} vs {init}"))
    out.append(("version: npm launcher == pyproject", npm["version"] == version, f"{npm['version']} vs {version}"))
    out.append(("version: docs/install.sh == pyproject", install_sh == version, f"{install_sh} vs {version}"))
    wheel = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    ci = read(".github/workflows/ci.yml")
    out.append(("ci glob names the built wheel", f"dist/{name.replace('-', '_')}-*.whl" in ci, wheel))
    out.append(("npm package name appears in README npx line",
                f"npx {npm['name']} " in read("README.md"), npm["name"]))
    if want_tag_free:
        tags = subprocess.run(["git", "-C", str(ROOT), "tag", "-l", f"v{version}"],
                              capture_output=True, text=True).stdout.strip()
        out.append((f"tag v{version} is free", tags == "", tags or "free"))
    return out


def main(argv):
    results = checks("--tag" in argv)
    bad = 0
    for label, ok, detail in results:
        print(("ok    " if ok else "FAIL  ") + f"{label}  ({detail})")
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

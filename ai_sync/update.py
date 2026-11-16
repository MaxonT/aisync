"""`aisync update`: say whether a newer release exists and the command that installs it.

It never installs anything itself: the upgrade command depends on how aisync was installed
(uv, pipx, the install.sh virtualenv, Homebrew or pip), so it prints that one command and stops."""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

REPO = "MaxonT/aisync"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v0.2.5' or '0.2.5' -> (0, 2, 5). None when the text is not a plain release number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def fetch_latest_tag(timeout: float = 5.0) -> str:
    """The newest GitHub release tag. Raises OSError (URLError is one) when GitHub cannot be reached."""
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "aisync-update"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as ex:
        raise OSError(str(ex)) from ex
    return str(data.get("tag_name", ""))


def install_method(module_file: str | Path, executable: str = "") -> str:
    """Guess how this copy was installed from where its package lives."""
    p = str(module_file).replace("\\", "/")
    exe = str(executable).replace("\\", "/")
    if "/uv/tools/" in p:
        return "uv"
    if "/pipx/venvs/" in p:
        return "pipx"
    if "/.local/share/aisync/venv/" in p:
        return "installer"
    if "/Cellar/" in p or "/Cellar/" in exe:          # Homebrew installs under .../Cellar/<formula>/<version>/
        return "brew"
    return "pip"


UPGRADE_COMMANDS = {
    "uv": "uv tool upgrade aisync-cli",
    "pipx": "pipx upgrade aisync-cli",
    "installer": "curl -fsSL https://maxont.github.io/aisync/install.sh | bash",
    "brew": "brew upgrade aisync",
    "pip": f"{sys.executable} -m pip install --upgrade aisync-cli",
}


def check(current: str, fetch: Callable[[], str], method: str) -> tuple[int, str]:
    """Return (exit code, message). 0 = up to date or newer exists, 1 = could not check."""
    try:
        latest_text = fetch()
    except OSError as ex:
        return 1, f"could not check for updates ({ex}). aisync {current} is installed."
    latest, cur = parse_version(latest_text), parse_version(current)
    if latest is None or cur is None:
        return 1, f"could not read the latest release ({latest_text!r}). aisync {current} is installed."
    if latest <= cur:
        return 0, f"aisync {current} is up to date (latest release: {latest_text})."
    return 0, (f"aisync {current} -> {latest_text} is available.\n"
               f"upgrade with: {UPGRADE_COMMANDS[method]}")

#!/usr/bin/env bash
# What: installer for aisync. Installs the `aisync` command from its GitHub Release.
# Why: the terminal shortcut on the install page; one line, safe to read first.
# How:
#   curl -fsSL https://maxont.github.io/aisync/install.sh | bash
#   bash install.sh --dry-run        # show what would happen, change nothing
#   bash install.sh --ref main       # install from a branch, tag or commit instead (needs git)
#   bash install.sh --from FILE.whl  # install a wheel you already have
#
# It installs the Python package with uv, pipx or pip --user, and falls back to its own
# virtualenv in ~/.local/share/aisync when the system Python refuses user installs.
# It never edits shell config, never uses sudo, and never reads any of your files.
set -euo pipefail

VERSION="0.1.0"
REPO="https://github.com/MaxonT/aisync"
SPEC="${REPO}/releases/download/v${VERSION}/aisync-${VERSION}-py3-none-any.whl"
VENV="${HOME}/.local/share/aisync/venv"
BIN_DIR="${HOME}/.local/bin"
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --ref) SPEC="git+${REPO}@${2:?--ref needs a branch, tag or commit}"; shift ;;
    --from) SPEC="${2:?--from needs a wheel file or URL}"; shift ;;
    -h|--help) sed -n '2,13p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else say "+ $*"; "$@"; fi
}

if ! command -v python3 >/dev/null 2>&1; then
  say "error: python3 not found. aisync needs Python 3.11 or newer." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  say "error: Python 3.11+ required, found $(python3 -V 2>&1)." >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  say "error: git not found. aisync keeps its hub in a local git repo, so it needs git." >&2
  say "install git first (macOS: xcode-select --install; Debian/Ubuntu: apt-get install git), then rerun." >&2
  exit 1
fi
say "installing aisync from ${SPEC}"

venv_install() {
  say "installer: private virtualenv in ${VENV} (the system Python refuses pip --user)"
  if ! run python3 -m venv "$VENV"; then
    say "error: python3 cannot create a virtualenv. On Debian/Ubuntu: apt-get install python3-venv, or install uv." >&2
    exit 1
  fi
  run "$VENV/bin/python" -m pip install --quiet --upgrade "$SPEC"
  run mkdir -p "$BIN_DIR"
  run ln -sf "$VENV/bin/aisync" "$BIN_DIR/aisync"
}

if command -v uv >/dev/null 2>&1; then
  say "installer: uv"
  run uv tool install --force "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
  say "installer: pipx"
  run pipx install --force "$SPEC"
elif python3 -m pip --version >/dev/null 2>&1; then
  if python3 -c 'import sys, sysconfig, pathlib; sys.exit(0 if (pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists() else 1)'; then
    venv_install
  else
    say "installer: pip --user"
    run python3 -m pip install --quiet --user --upgrade "$SPEC"
  fi
elif python3 -m venv --help >/dev/null 2>&1; then
  venv_install
else
  say "error: need one of uv, pipx, pip or venv. Install uv (https://docs.astral.sh/uv/) and rerun." >&2
  exit 1
fi

if [ "$DRY" = 1 ]; then
  say "dry-run finished: nothing was changed."
  exit 0
fi

if command -v aisync >/dev/null 2>&1; then
  say "installed: $(aisync --version 2>&1)"
  say "next: aisync demo   (a throwaway hub with a synthetic two-week journal)"
else
  found=""
  for c in "$BIN_DIR/aisync" "$(python3 -m site --user-base 2>/dev/null)/bin/aisync"; do
    if [ -x "$c" ]; then found="$c"; break; fi
  done
  say "installed, but 'aisync' is not on PATH yet."
  say "fix it yourself (this script never edits shell config): add $(dirname "${found:-$BIN_DIR/aisync}") to PATH,"
  say "or run uv tool update-shell / pipx ensurepath."
  if [ -n "$found" ]; then say "until then run it by its full path: $found demo"; fi
fi

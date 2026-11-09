#!/usr/bin/env python3
"""What: privacy scan for the public aisync release. Why: the repo, the sdist and the wheel are public,
so no personal path, name, e-mail or private repo name may ship in any of them.
How: python3 tools/privacy_scan.py [PATH ...]   (no PATH: git ls-files of this repo, then a fresh sdist
and wheel built from it); --history also scans every commit message; exit 1 on any hit.
The guarded words are stored as SHA-256 prefixes, so this file does not publish what it guards."""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {
    "8bb367a8f3ba87581929", "e63b9641f880cc3335b9", "f15c16b99f82d8201767", "35b70246a19cb878499f",
    "7b9dd7061d0e07a296d2", "b6463d839a2dd880fbf0", "ce62d6913dfc9149ab9b", "b7b5122e0216692a394b",
    "9aebd569f8d256fe366f", "7d941509db4cf57a351b", "1cb8cc6d49bb9a9982fa", "92f9ed99b93513392b02",
    "70f693250fd960f8a205", "d0a6da933dd33d8e975d",
}
EXTRA_FILE = ROOT / "tools" / "private_hashes.txt"     # more prefixes, one per line (added by the port script)
PATH_RE = re.compile(r"/Users/\w|/home/(?!runner\b|user\b)\w")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PUBLIC_EMAIL = re.compile(r"noreply@|@users\.noreply\.github\.com$|@example\.(com|org)$|^cursoragent@cursor\.com$")
CJK = re.compile(r"[一-鿿]{2,}")
SKIP_SUFFIX = {".pyc", ".gif", ".png", ".jpg", ".ico", ".whl", ".gz"}


def digest(w: str) -> str:
    return hashlib.sha256(w.encode()).hexdigest()[:20]


def guarded() -> set[str]:
    extra = set()
    if EXTRA_FILE.exists():
        extra = {l.strip() for l in EXTRA_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")}
    return PRIVATE | extra


def words(line: str):
    """Every word and every run of a hyphenated word's parts (with and without hyphens), plus every
    2 to 8 character slice of a run of Chinese characters."""
    for tok in re.findall(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", line.lower()):
        parts = re.split(r"[-.]", tok)
        for i in range(len(parts)):
            for j in range(i + 1, len(parts) + 1):
                for w in {"-".join(parts[i:j]), "".join(parts[i:j]), ".".join(parts[i:j])}:
                    yield w
    for run in CJK.findall(line):
        for n in range(2, min(8, len(run)) + 1):
            for i in range(len(run) - n + 1):
                yield run[i:i + n]


def scan_text(text: str, hashes: set[str] | None = None) -> list[str]:
    hashes = guarded() if hashes is None else hashes
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        why = [f"word#{digest(w)[:8]}" for w in set(words(line)) if digest(w) in hashes]
        why += PATH_RE.findall(line)
        why += [m for m in EMAIL.findall(line) if not PUBLIC_EMAIL.search(m)]
        hits += [f"{n}: {w}" for w in why]
    return hits


def scan_files(items) -> list[str]:
    """items: (label, text) pairs."""
    hashes = guarded()
    return [f"{label}:{h}" for label, text in items for h in scan_text(text, hashes)]


def tree_items(root: Path):
    names = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split("\n")
    for name in filter(None, names):
        p = root / name
        if p.is_file() and p.suffix not in SKIP_SUFFIX:
            yield name, p.read_text(errors="ignore")


def archive_items(path: Path):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if not n.endswith("/") and Path(n).suffix not in SKIP_SUFFIX:
                    yield f"{path.name}!{n}", z.read(n).decode(errors="ignore")
    else:
        with tarfile.open(path) as t:
            for m in t.getmembers():
                if m.isfile() and Path(m.name).suffix not in SKIP_SUFFIX:
                    yield f"{path.name}!{m.name}", t.extractfile(m).read().decode(errors="ignore")


def build(root: Path, out: Path) -> list[Path]:
    subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(out), str(root)],
                   check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "build", "--sdist", "-o", str(out), str(root)],
                   capture_output=True)      # optional: needs `build`; the wheel is the must-have
    return sorted(out.glob("*.whl")) + sorted(out.glob("*.tar.gz"))


def history_items(root: Path, everything: bool = False):
    """Commit messages and authors since the newest tag (all of them with everything=True): what a
    push would publish now. Older commits are already public and can only change by a history rewrite."""
    tag = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--abbrev=0"],
                         capture_output=True, text=True).stdout.strip()
    rng = ["--all"] if everything or not tag else [f"{tag}..HEAD"]
    log = subprocess.run(["git", "-C", str(root), "log", *rng, "--no-merges",
                          "--format=%x00%H%x01%an%x01%ae%x01%cn%x01%ce%n%B"],
                         capture_output=True, text=True, check=True).stdout
    for entry in filter(str.strip, log.split("\0")):
        head, _, body = entry.partition("\n")
        sha, an, ae, cn, ce = head.split("\x01")
        people = [f"{an} <{ae}>"]
        # A hosted rebase merge keeps the author and rewrites the committer name to the account's display
        # name (same address, different name). That name comes from the host, not from what we push.
        if not (ce == ae and cn != an):
            people.append(f"{cn} <{ce}>")
        yield f"commit {sha[:8]}", "\n".join(people) + "\n" + body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--no-build", action="store_true", help="skip the sdist and wheel")
    ap.add_argument("--history", action="store_true", help="also scan commit messages and authors since the last tag")
    ap.add_argument("--history-all", action="store_true", help="scan every commit message, tagged ones too")
    a = ap.parse_args(argv)
    items, built = [], []
    if a.paths:
        for p in a.paths:
            files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
            for f in files:
                if f.suffix in (".whl",) or f.name.endswith(".tar.gz"):
                    items += archive_items(f)
                elif f.suffix not in SKIP_SUFFIX and "__pycache__" not in f.parts:
                    items.append((str(f), f.read_text(errors="ignore")))
    else:
        items += tree_items(a.root)
        if not a.no_build:
            with tempfile.TemporaryDirectory() as tmp:
                built = build(a.root, Path(tmp))
                for b in built:
                    items += archive_items(b)
                built = [b.name for b in built]
    if a.history or a.history_all:
        items += history_items(a.root, a.history_all)
    hits = scan_files(items)
    for h in hits:
        print(h)
    print(f"privacy scan: {len(items)} files, {len(hits)} hits" + (f", built {', '.join(built)}" if built else ""))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())

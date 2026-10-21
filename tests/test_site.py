"""Tests for what people see first: README order, EN/ZH images kept apart, the website's local links,
the demo board page, install.sh and the lineage credits. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
README_ZH = (ROOT / "README.zh-CN.md").read_text()
PAGES = [ROOT / "docs/index.html", ROOT / "docs/install/index.html", ROOT / "docs/board/index.html"]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def local_refs(text: str) -> list[str]:
    refs = re.findall(r'(?:src|href)="([^"#]+)"', text) + re.findall(r"\]\(([^)\s#]+)\)", text)
    return [r for r in refs if not re.match(r"[a-z]+:", r)]


class ReadmeTests(unittest.TestCase):
    def test_first_screen_order(self):
        order = ["docs/assets/banner.svg", "[!NOTE]", "coworkers who never talk", "a star helps", "## Install",
                 "Read https://maxont.github.io/aisync/install and install aisync for me.",
                 "curl -fsSL https://maxont.github.io/aisync/install.sh | bash", "docs/assets/demo.gif",
                 "| **Claims** |", "| **Journal** |", "| **Mid-session news** |", "**Dogfooded:** 1,600+ journal events"]
        pos = [README.find(s) for s in order]
        self.assertNotIn(-1, pos, [s for s, p in zip(order, pos) if p < 0])
        self.assertEqual(pos, sorted(pos))
        table = README[README.find("| | Rules sync tools"):README.find("aisync also does the rules part")]
        self.assertEqual(table.count("\n| **"), 3)

    def test_chinese_readme_mirrors_the_order(self):
        order = ["docs/assets/banner.zh.svg", "[!NOTE]", "从不交流的同事", "点个 star", "## 安装",
                 "install aisync for me.", "install.sh | bash", "docs/assets/demo.zh.gif",
                 "| **认领** |", "| **日志** |", "| **会话中消息** |", "1,600+"]
        pos = [README_ZH.find(s) for s in order]
        self.assertNotIn(-1, pos, [s for s, p in zip(order, pos) if p < 0])
        self.assertEqual(pos, sorted(pos))

    def test_english_and_chinese_images_never_mix(self):
        en_imgs = re.findall(r"docs/assets/[\w.-]+", README)
        zh_imgs = re.findall(r"docs/assets/[\w.-]+", README_ZH)
        self.assertTrue(en_imgs and zh_imgs)
        self.assertFalse([i for i in en_imgs if ".zh." in i])
        self.assertFalse([i for i in zh_imgs if ".zh." not in i])
        index = (ROOT / "docs/index.html").read_text()
        self.assertFalse(re.findall(r'src="[^"]*\.zh\.[^"]*"', index))

    def test_code_lines_fit_the_github_readme_width(self):
        wide = lambda line: sum(2 if "\u3000" <= c <= "\u9fff" or "\uff00" <= c <= "\uffef" else 1 for c in line)
        for name, text in (("README.md", README), ("README.zh-CN.md", README_ZH)):
            for block in re.findall(r"```(?:bash|text)\n(.*?)```", text, re.S):
                for line in block.splitlines():
                    self.assertLessEqual(wide(line), 80, f"{name}: {line}")

    def test_no_endorsement_claims(self):
        banned = re.compile(r"\b(endorsed|endorsement|official|officially|approved by|partner(ed|ship)?|sponsored|"
                            r"backed by|certified)\b", re.I)
        for path in [ROOT / "README.md", ROOT / "README.zh-CN.md", *PAGES]:
            self.assertIsNone(banned.search(path.read_text()), path.name)


class SiteTests(unittest.TestCase):
    def test_every_local_link_and_image_exists(self):
        for path in [ROOT / "README.md", ROOT / "README.zh-CN.md", *PAGES]:
            for ref in local_refs(path.read_text()):
                target = (path.parent / ref).resolve()
                if ref.endswith("/"):
                    target = target / "index.html"
                self.assertTrue(target.exists(), f"{path.relative_to(ROOT)} -> {ref}")

    def test_site_links_from_readme_point_at_real_pages(self):
        for url in set(re.findall(r"https://maxont\.github\.io/aisync/([\w./-]*)", README + README_ZH)):
            page = ROOT / "docs" / url
            if url in ("", "install") or url.endswith("/"):
                page = page / "index.html"
            self.assertTrue(page.exists(), url)

    def test_pages_explain_themselves_and_load_nothing_remote(self):
        for path in PAGES:
            text = path.read_text()
            self.assertRegex(text, r"<!--\s*\nWhat: .+\nWhy: .+\nHow: ", path.name)
            self.assertNotIn("<script", text)
            self.assertFalse(re.findall(r'<(?:img|link)[^>]+(?:src|href)="https?:', text), path.name)

    def test_install_page_hands_off_to_the_ai_safely(self):
        page = (ROOT / "docs/install/index.html").read_text()
        for must in ("For humans", "FOR YOUR AI", "Capability gate", "--dry-run", "shell config",
                     "Be honest about failure", "Report back", "git --version"):
            self.assertIn(must, page)
        self.assertNotIn("python3 -m ai_sync", page)           # not true after a uv, pipx or venv install

    def test_demo_board_page_is_labelled_synthetic(self):
        page = (ROOT / "docs/board/index.html").read_text()
        self.assertIn("Synthetic demo data.", page)
        self.assertIn("overlaps", page)
        self.assertIn("still open", page)
        self.assertIn("unblocked</span> by", page)

    def test_social_card_and_jekyll_off(self):
        self.assertTrue((ROOT / "docs/.nojekyll").exists())
        self.assertIn('og:image" content="https://maxont.github.io/aisync/assets/og.png"',
                      (ROOT / "docs/index.html").read_text())
        self.assertGreater((ROOT / "docs/assets/og.png").stat().st_size, 10_000)


class InstallScriptTests(unittest.TestCase):
    SCRIPT = ROOT / "docs/install.sh"

    def test_pins_the_package_version_and_parses(self):
        text = self.SCRIPT.read_text()
        self.assertIn(f'VERSION="{VERSION}"', text)
        self.assertIn("releases/download/v${VERSION}/aisync-${VERSION}-py3-none-any.whl", text)
        self.assertEqual(subprocess.run(["bash", "-n", str(self.SCRIPT)]).returncode, 0)

    def test_version_flag_installs_that_release(self):
        r = subprocess.run(["bash", str(self.SCRIPT), "--dry-run", "--version", "v9.8.7"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("releases/download/v9.8.7/aisync-9.8.7-py3-none-any.whl", r.stdout)
        release = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn('install.sh | bash -s -- --version "${TAG#v}"', release)

    def test_help_prints_only_the_header(self):
        r = subprocess.run(["bash", str(self.SCRIPT), "--help"], capture_output=True, text=True, timeout=30)
        self.assertTrue(r.stdout.startswith("What: installer for aisync"))
        self.assertNotIn("set -euo", r.stdout)

    def test_never_sudo_never_shell_config(self):
        code = [l for l in self.SCRIPT.read_text().splitlines() if not l.lstrip().startswith("#")]
        self.assertFalse([l for l in code if re.search(r"\bsudo\b", l)])
        self.assertFalse([l for l in code if re.search(r"\.(bashrc|zshrc|profile)\b", l)])

    def test_dry_run_changes_nothing(self):
        r = subprocess.run(["bash", str(self.SCRIPT), "--dry-run", "--from", "/nonexistent/aisync.whl"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry-run finished: nothing was changed.", r.stdout)
        self.assertIn("[dry-run]", r.stdout)


class LineageTests(unittest.TestCase):
    def test_one_headline_credit_with_evidence(self):
        lin = json.loads((ROOT / ".github/refine/lineage.json").read_text())
        self.assertEqual(sum(1 for c in lin["credits"] if c.get("headline")), 1)
        self.assertEqual(next(c for c in lin["credits"] if c.get("headline"))["name"], "AGENTS.md")
        self.assertTrue(all(len(c["evidence"]) >= 30 for c in lin["credits"]))
        self.assertEqual({c["name"] for c in lin["credits"]}, {"AGENTS.md", "Claude Code", "Codex", "Cursor", "git"})
        self.assertIn("not affiliated with the projects above", README)


if __name__ == "__main__":
    unittest.main()

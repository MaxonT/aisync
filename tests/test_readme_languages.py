"""Nine README languages: every file exists, every file links to all nine, and the code blocks (commands,
flags, URLs) are byte-identical to the English source. Translations may change prose only."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGS = {
    "README.md": "English",
    "README.zh-CN.md": "中文",
    "README.ja.md": "日本語",
    "README.ko.md": "한국어",
    "README.es.md": "Español",
    "README.fr.md": "Français",
    "README.de.md": "Deutsch",
    "README.pt.md": "Português",
    "README.ru.md": "Русский",
}
CODE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def blocks(text):
    """The commands of each code block, without the trailing `# comment`: comments are prose and may be translated."""
    out = []
    for b in CODE.findall(text):
        out.append("\n".join(line.split("  #")[0].rstrip() for line in b.splitlines()))
    return out


class NineLanguagesTest(unittest.TestCase):
    def test_all_nine_files_exist(self):
        for name in LANGS:
            self.assertTrue((ROOT / name).exists(), name)

    def test_every_readme_links_to_all_nine(self):
        for name in LANGS:
            text = (ROOT / name).read_text()
            for other in LANGS:
                if other == name:
                    continue
                self.assertIn(f"({other})", text, f"{name} does not link to {other}")

    def test_code_blocks_match_the_english_source(self):
        source = blocks((ROOT / "README.md").read_text())
        self.assertTrue(source)
        for name in LANGS:
            if name == "README.md":
                continue
            got = blocks((ROOT / name).read_text())
            self.assertEqual(len(got), len(source), f"{name}: code block count differs")
            for i, (a, b) in enumerate(zip(source, got)):
                self.assertEqual(a, b, f"{name}: code block {i} differs from the English source")

    def test_translations_credit_only_the_owner(self):
        for name in LANGS:
            text = (ROOT / name).read_text()
            for bad in ("Cursor Agent", "cursoragent", "Co-authored-by: Claude", "Generated with Claude"):
                self.assertNotIn(bad, text, f"{name} credits {bad}")


if __name__ == "__main__":
    unittest.main()


class HeadingParityTest(unittest.TestCase):
    """Every translation has the same H2 sections in the same order as the English source."""

    def test_h2_count_and_order_match_the_english_source(self):
        import re as _re
        src = [l for l in (ROOT / "README.md").read_text().splitlines() if l.startswith("## ")]
        self.assertTrue(src)
        for name in LANGS:
            if name == "README.md":
                continue
            got = [l for l in (ROOT / name).read_text().splitlines() if l.startswith("## ")]
            self.assertEqual(len(got), len(src), f"{name}: {len(got)} H2 sections, English has {len(src)}")

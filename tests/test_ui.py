from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from ai_sync import __version__, cli, ui


class StyleTests(unittest.TestCase):
    def test_logo_rows_are_one_width_and_use_only_full_blocks(self):
        self.assertEqual(len({len(row) for row in ui.LOGO}), 1)
        self.assertEqual(set("".join(ui.LOGO)), {"█", " "})

    def test_no_color_and_pipes_stay_plain(self):
        pipe = io.StringIO()
        self.assertFalse(ui.style_for(pipe, env={}).color)
        self.assertFalse(ui.style_for(pipe, env={"FORCE_COLOR": "1", "NO_COLOR": "1"}).color)
        self.assertTrue(ui.style_for(pipe, env={"FORCE_COLOR": "1"}).color)

    def test_banner_falls_back_to_one_line(self):
        narrow = ui.Style(color=False, unicode=True, width=30)
        ascii_ = ui.Style(color=False, unicode=False, width=120)
        for style in (narrow, ascii_):
            text = ui.banner(style, __version__)
            self.assertEqual(text.count("\n"), 0)
            self.assertIn("aisync v" + __version__, text)

    def test_banner_wide_has_logo_wave_and_tagline(self):
        text = ui.banner(ui.Style(color=True, unicode=True, width=120, truecolor=True), __version__)
        plain = ui.strip_ansi(text).splitlines()
        self.assertEqual(plain[:5], list(ui.LOGO))
        self.assertIn(ui.TAGLINE, plain[6])
        self.assertIn("\x1b[1;38;2;240;101;149m", text)  # starts on the pink stop


class CliFrontScreenTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.dict("os.environ", {"NO_COLOR": "1"}):
            try:
                code = cli.main(list(argv))
            except SystemExit as ex:
                code = ex.code
        return code, out.getvalue()

    def test_bare_aisync_prints_the_front_screen_and_exits_0(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn(ui.TAGLINE, out)
        self.assertIn("aisync demo", out)

    def test_help_lists_every_command_once(self):
        code, out = self.run_cli("-h")
        self.assertEqual(code, 0)
        sub = next(a for a in cli.build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
        for name in sub.choices:
            self.assertEqual(sum(line.split()[:1] == [name] for line in out.splitlines()), 1, name)

    def test_subcommand_help_is_still_argparse(self):
        code, out = self.run_cli("claim", "-h")
        self.assertEqual(code, 0)
        self.assertIn("usage: aisync claim", out)


if __name__ == "__main__":
    unittest.main()

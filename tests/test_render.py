"""Drawing primitives: charts, themes, glyph fallback, cell widths and search snippets."""

from __future__ import annotations

import io
import os
import re
import sqlite3
import sys
import unittest
from contextlib import contextmanager

from support import Screen, StoreTest


class ChartTest(unittest.TestCase):
    """The bar primitive every chart in `cs` is drawn with."""

    def _colour(self):
        """`ui` with colour forced on — it is decided once, at import."""
        from unittest import mock

        from cs import ui

        return mock.patch.object(ui, "_COLOR", True)

    def test_a_value_is_never_rounded_away_to_nothing(self):
        """The old bars used int(), so 1-in-500 drew a blank row.

        A chart that cannot tell "none" from "some" is worse than no chart:
        the whole reason to look at one is to find the rows that are not zero.
        """
        from cs import ui

        self.assertEqual(ui.bar(0, 500, 20), "")
        self.assertNotEqual(ui.bar(1, 500, 20), "")
        self.assertEqual(ui.cells(ui.bar(500, 500, 20)), 20)

    def test_a_bar_is_monotonic_in_its_value(self):
        """More is never shorter — including across the eighth-cell steps."""
        from cs import ui

        widths = [ui.cells(ui.bar(value, 40, 12)) for value in range(41)]
        self.assertEqual(widths, sorted(widths))
        self.assertEqual(widths[0], 0)
        self.assertEqual(widths[-1], 12)

    def test_a_padded_bar_is_exactly_its_column_wide_in_colour(self):
        """A coloured bar is mostly escape characters, so f-string padding
        counts the wrong thing — the bar has to arrive already the right size."""
        import re

        from cs import ui

        with self._colour():
            for value in (0, 1, 7, 19, 20):
                for kind in ({"pad": True}, {"track": True}):
                    with self.subTest(value=value, kind=kind):
                        drawn = ui.bar(value, 20, 16, **kind)
                        plain = re.sub(r"\x1b\[[0-9;]*m", "", drawn)
                        self.assertEqual(ui.cells(plain), 16)

    def test_an_unpadded_bar_carries_no_trailing_space(self):
        from cs import ui

        with self._colour():
            import re
            plain = re.sub(r"\x1b\[[0-9;]*m", "", ui.bar(5, 20, 16))
            self.assertEqual(plain, plain.rstrip())

    def test_the_reader_can_render_every_colour_the_reports_emit(self):
        """A colour with no curses pair renders as plain text in the reader.

        That is the one view where a long report is actually read, so a
        palette entry missing from `sgr_palette` is invisible exactly where
        it matters most. This asserts the two lists cannot drift.
        """
        import re

        from cs import ui

        class FakeCurses:
            COLORS = 256
            COLOR_PAIRS = 256
            A_BOLD, A_DIM, A_REVERSE = 1, 2, 4
            error = RuntimeError

            def __init__(self):
                self.pairs = {}

            def start_color(self):
                pass

            def use_default_colors(self):
                pass

            def init_pair(self, pair, fg, bg):
                self.pairs[pair] = fg

            def color_pair(self, pair):
                return 1 << (pair + 8)

        palette = ui.sgr_palette(FakeCurses())
        for colour in ui.PALETTE_256:
            self.assertIn(f"38;5;{colour}", palette,
                          f"colour {colour} has no curses pair")

        # And nothing emits a colour that was never declared. Read off the
        # source rather than the constants, because with colour off — which
        # is how the tests run — every constant is the empty string and an
        # undeclared colour would sail through.
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(ui))
        used = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "c256"
            and node.args and isinstance(node.args[0], ast.Constant)
        }
        self.assertTrue(used, "no colours found — has c256 been renamed?")
        self.assertEqual(used - set(ui.PALETTE_256), set(),
                         "a colour is emitted but not declared in PALETTE_256")
        # A literal index in an escape, rather than c256's own {number}.
        self.assertFalse(re.search(r"\\033\[38;5;\d", inspect.getsource(ui)),
                         "a 256-colour escape bypasses c256")


class ThemeTest(unittest.TestCase):
    """The palette is tuned for a dark terminal, which is what a terminal is.

    Colour is normally off in these tests — stdout is a pipe — so the theme is
    checked by reloading the module with a tty stand-in in place, which is the
    only way to see the escapes it would actually emit. The reload has to be
    unwound inside the same `with`: `importlib.reload` mutates the module
    object rather than returning a new one, so restoring the real environment
    also resets every attribute a caller was still holding.
    """

    @contextmanager
    def _theme(self, theme: str | None):
        import importlib

        from cs import ui

        class Tty(io.StringIO):
            def isatty(self):
                return True

        previous = {key: os.environ.get(key) for key in ("CS_THEME", "TERM")}
        real = sys.stdout
        try:
            if theme is None:
                os.environ.pop("CS_THEME", None)
            else:
                os.environ["CS_THEME"] = theme
            # ui only emits escapes for a colour-capable tty, and the runner
            # is neither, so both halves have to be stood in for.
            os.environ["TERM"] = "xterm-256color"
            sys.stdout = Tty()
            yield importlib.reload(ui)
        finally:
            sys.stdout = real
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            importlib.reload(ui)

    def test_the_default_palette_is_saturated(self):
        """Pastels have no chroma left on black; the accent was #87afff."""
        with self._theme(None) as ui:
            self.assertEqual(ui.ACCENT, "\033[38;5;39m")   # #00afff
            self.assertEqual(ui.VIOLET, "\033[38;5;177m")  # #d787ff
            self.assertEqual(ui.SLATE, "\033[38;5;239m")   # neutral, recessed
            # The bar ramp is the wordmark's gradient; its two pale steps
            # were the ones that made a half-length bar look unfinished.
            self.assertNotIn(111, ui._BAR_RAMP)
            self.assertNotIn(75, ui._BAR_RAMP)

    def test_light_terminals_keep_the_pastels(self):
        """On white paper the pastels are the legible choice, so they survive."""
        with self._theme("light") as ui:
            self.assertEqual(ui.ACCENT, "\033[38;5;111m")
            self.assertEqual(ui.VIOLET, "\033[38;5;141m")

    def test_every_colour_either_theme_emits_is_declared(self):
        """An undeclared index renders as plain text in the curses reader."""
        for theme in (None, "light"):
            with self.subTest(theme=theme), self._theme(theme) as ui:
                used = {
                    int(re.search(r"(\d+)m$", value).group(1))
                    for name, value in vars(ui).items()
                    if name.isupper() and isinstance(value, str)
                    and value.startswith("\033[38;5;")
                }
                missing = used - set(ui.PALETTE_256)
                self.assertFalse(missing, f"undeclared: {sorted(missing)}")

    def test_the_wordmark_and_the_bars_share_one_gradient(self):
        """They were two lists of the same eight colours, and they drifted."""
        with self._theme(None) as ui:
            self.assertEqual(list(ui._BANNER_RAMP), list(ui._BAR_RAMP))
            self.assertNotIn(111, ui._BANNER_RAMP)

    def test_rules_are_furniture_and_are_drawn_as_furniture(self):
        """Six full-width accent lines made the loudest thing the least useful."""
        with self._theme(None) as ui:
            self.assertEqual(ui.rule(20), f"  {ui.SLATE}{'─' * 20}{ui.RST}")
            titled = ui.rule(30, "Section")
            self.assertTrue(titled.startswith(f"  {ui.ACCENT}──{ui.RST}"))
            self.assertIn(ui.SLATE, titled)   # the trailing dashes recede


class AsciiGlyphTest(StoreTest):
    """`CS_GLYPHS=ascii` has to reach the screen it matters most on.

    It existed for the terminals that draw a hollow box instead of an emoji,
    and it was honoured by the two transcript speaker marks and ignored by
    the seventeen icons on the first screen anyone sees — so the setting you
    reach for *because* your terminal cannot draw emoji left the landing
    screen full of them.
    """

    def _icons(self) -> list[str]:
        """The icons the landing screen actually drew, from a real frame."""
        from cs import cli

        screen = Screen([ord("q")])
        cli._home_tui(screen, {"revealed": True})
        return [value for (_, col), value in screen.frames[-1].items() if col == 3]

    def test_the_menu_is_pure_ascii_when_asked(self):
        import importlib
        import os

        from cs import cli, ui

        os.environ["CS_GLYPHS"] = "ascii"
        try:
            importlib.reload(ui)
            importlib.reload(cli)
            drawn = self._icons()
            self.assertTrue(drawn)
            for icon in drawn:
                self.assertTrue(icon.isascii(), f"{icon!r} is not ASCII")
        finally:
            del os.environ["CS_GLYPHS"]
            importlib.reload(ui)
            importlib.reload(cli)

    def test_the_menu_draws_emoji_by_default(self):
        """The escape hatch is an escape hatch, not the new normal."""
        drawn = self._icons()
        self.assertTrue(drawn)
        self.assertFalse(any(icon.isascii() for icon in drawn))


class CellWidthTest(StoreTest):
    """A column count has to agree with what the terminal actually draws.

    `cells` counted every codepoint, so anything zero-width was charged a
    column it never occupies. That errs in the direction that hurts: text
    measures *wider* than it draws, so a line that fits gets truncated and a
    column that had room gets dropped. It surfaced here because the fix for
    the flat-looking ⚡ is a variation selector, and adding one made a
    two-column icon measure three.
    """

    ZERO_WIDTH = (("\ufe0f", "variation selector 16, asks for the emoji form"),
                  ("\ufe0e", "variation selector 15, asks for the text form"),
                  ("\u200d", "zero-width joiner"),
                  ("\u0301", "combining acute accent"))

    def test_a_zero_width_character_costs_no_column(self):
        from cs import ui

        for ch, what in self.ZERO_WIDTH:
            with self.subTest(what=what):
                self.assertEqual(ui.cells(ch), 0)
                self.assertEqual(ui.cells(f"ab{ch}c"), 3)

    def test_an_accent_does_not_widen_the_word_it_sits_on(self):
        """Composed and decomposed spellings of the same word draw the same,
        so they have to measure the same — repository names arrive both ways
        depending on which filesystem wrote them."""
        from cs import ui

        self.assertEqual(ui.cells("caf\u00e9"), 4)
        self.assertEqual(ui.cells("cafe\u0301"), 4)

    def test_the_widths_it_already_got_right_stay_right(self):
        from cs import ui

        self.assertEqual(ui.cells(""), 0)
        self.assertEqual(ui.cells("plain"), 5)
        self.assertEqual(ui.cells("👤"), 2)
        self.assertEqual(ui.cells("日本"), 4)
        self.assertEqual(ui.cells("✅ done"), 7)

    def test_truncation_uses_the_corrected_count(self):
        """The point of measuring is fitting. An icon carrying a variation
        selector must survive a two-column budget rather than being cut in
        half by the character that makes it render properly."""
        from cs import ui

        self.assertEqual(ui.cells(ui._fit("⚡\ufe0f", 2)), 2)
        self.assertEqual(ui.cells(ui._fit("abc", 2)), 2)


class SearchSnippetTest(unittest.TestCase):
    """The search snippet is a window FTS cut, not a sentence anyone wrote.

    Two things about that window used to defeat masking, and both leaked the
    secret rather than the mask: highlight markers were inserted at token
    boundaries, and a credential is several tokens, so `ghp` `_` `ZZZZ…`
    could be marked or cut apart into something no pattern recognises.
    """

    TOKEN = "ghp_" + "Z" * 36

    def _snippet(self, content: str, term: str) -> str:
        from cs import cli

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE si USING fts5(content)")
        conn.execute("INSERT INTO si VALUES (?)", (content,))
        row = conn.execute(
            "SELECT snippet(si, 0, '', '', '…', 12) FROM si WHERE si MATCH ?",
            (term,),
        ).fetchone()
        conn.close()
        return cli._snippet(" ".join(row[0].split())) if row else ""

    def test_a_credential_survives_no_window_the_search_can_cut(self):
        leaked = []
        for lead in range(0, 14):
            before = " ".join(f"L{i}" for i in range(lead))
            after = " ".join(f"w{i}" for i in range(40))
            content = f"{before} {self.TOKEN} {after}"
            terms = [f"L{i}" for i in range(lead)]
            terms += [f"w{i}" for i in range(40)] + ["ghp"]
            for term in terms:
                if "Z" * 10 in self._snippet(content, term):
                    leaked.append((lead, term))
        self.assertEqual(leaked[:5], [], "a windowed credential printed in clear")

    def test_a_private_key_body_without_its_header_is_suppressed(self):
        from cs import cli, redact

        body = (
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB"
            "AAAAMwAAAAtzc2gtZWQyNTUxOQAAACBAbCdEfGhIjKlMnOpQrStUvW"
        )
        shown = self._snippet(f"before {body} after", "before")
        self.assertNotIn(body, shown)
        self.assertIn("private-key-fragment", shown)

        wrapped = (
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd "
            "EFGhIjKlMnOpQrStUvWxYz0123456789AbCdEfgh"
        )
        self.assertEqual(cli._snippet(wrapped), "[redacted:private-key-fragment]")
        digest = "a3" * 32
        self.assertEqual(redact.snippet(digest), digest)

    def test_the_match_is_still_highlighted(self):
        from unittest import mock

        from cs import cli, ui

        with mock.patch.object(ui, "AMBER", "<hit>"), \
                mock.patch.object(ui, "BOLD", ""), \
                mock.patch.object(ui, "RST", "</hit>"), \
                mock.patch.object(ui, "DIM", ""):
            marked = cli._highlight("deploy the widget", "widget")
        self.assertIn("<hit>widget</hit>", marked)

    def test_a_file_hit_keeps_an_elided_filename(self):
        from cs import cli

        self.assertEqual(cli._hit_text("edit", "…/deep/notes.md"), "…/deep/notes.md")

"""The transcript: how a conversation is set on the page.

`cs read` — and the `t` key on any listing, which reaches the same renderer —
is the one view whose content is somebody else's prose rather than this
tool's numbers. It therefore has almost no numbers to check and almost
nothing but layout, so the layout is what these assert: how much furniture a
turn costs, who each line is attributed to, and that the two surfaces it is
read on (a pager and the curses reader) can both draw everything it emits.

The shape here was arrived at by taking three full-width rules per turn down
to one. Nothing below encodes a taste; each test names the thing that was
wrong before it.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path

from support import Screen, StoreTest, _Tty

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

REPLY = """Here is what I changed.

## The parser

I moved the tokenizer out because the old one was doing two jobs.

- `parse()` now returns a tuple
- `lex()` is private

```python
def parse(text: str) -> tuple[str, int]:
    return text.partition(":")[0], 0
```

> Note: the old behaviour returned None, which callers ignored.

That is everything.
"""


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


@contextlib.contextmanager
def _colour_ui():
    """`ui` re-imported with colour on, then put back as it was.

    Colour is decided once, at import, from `sys.stdout.isatty()` — so the
    only honest way to test the coloured output is to import the module
    again with a stdout that claims to be a terminal. Everything derived
    from it (`ACCENT`, `MUTED`, the whole palette) is a module constant, so
    patching the flag alone would leave every colour still empty.
    """
    from cs import cli, ui

    term = os.environ.get("TERM")
    os.environ["TERM"] = "xterm-256color"
    try:
        with redirect_stdout(_Tty()):
            importlib.reload(ui)
        yield ui
    finally:
        if term is None:
            os.environ.pop("TERM", None)
        else:
            os.environ["TERM"] = term
        importlib.reload(ui)
        # cli holds the module, not its names, so nothing to rebind — but say
        # so, because a reader who assumes otherwise will add a rebind here.
        assert cli.ui is ui


class TranscriptTest(StoreTest):
    """A session with a reply that uses every shape a reply actually uses."""

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(Path(os.environ["COPILOT_HOME"]) / "session-store.db")
        conn.execute(
            "INSERT INTO turns VALUES (90,'sess-alpha',2,?,?,"
            "'2026-08-18T09:14:00.000Z')",
            ("Refactor the parser into its own module, and do not touch the CLI.",
             REPLY),
        )
        conn.execute(
            "INSERT INTO turns VALUES (91,'sess-alpha',3,'and now?',NULL,"
            "'2026-08-18T09:41:00.000Z')"
        )
        conn.commit()
        conn.close()

    def _lines(self, *args: str, columns: str = "92") -> list[str]:
        previous = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = columns
        try:
            code, out = self._run("read", "sess-alpha", *args)
        finally:
            if previous is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = previous
        self.assertEqual(code, 0)
        return out.splitlines()

    def _block(self, lines: list[str], speaker: str) -> list[str]:
        """The body lines under one speaker label, up to the next blank."""
        start = next(i for i, line in enumerate(lines) if speaker in line)
        body = []
        for line in lines[start + 1:]:
            if not line.strip():
                break
            body.append(line)
        return body

    # ── How much furniture a turn costs ──────────────────────────────

    def test_a_turn_opens_on_one_rule_and_not_three(self):
        """Turn, You and Copilot each drew a full-width rule, so a two-line
        exchange arrived under three lines of identical furniture and the
        eye had nothing to rank."""
        lines = self._lines("--turn", "0")
        drawn = [line for line in lines if "─" * 8 in _plain(line)]
        # One to open the turn, one to close the page before the footer.
        self.assertEqual(len(drawn), 3, drawn)   # header, turn, footer
        self.assertTrue(any("Turn 0" in line for line in drawn))
        for line in drawn:
            self.assertNotIn("You", _plain(line))
            self.assertNotIn("Copilot", _plain(line))

    def test_the_turn_rule_carries_when_and_how_big(self):
        """The size used to sit on its own line under the rule: a lone
        fragment of grey between a label and the thing it labels."""
        lines = self._lines("--turn", "2")
        rule = next(line for line in lines if "Turn 2" in line)
        self.assertIn("09:14", _plain(rule))
        self.assertIn("chars", _plain(rule))
        # And nothing is left behind on a line of its own.
        after = lines[lines.index(rule) + 1]
        self.assertEqual(after.strip(), "")

    def test_the_rule_still_names_the_ask(self):
        """Scrolling a long transcript should say what you are looking at,
        not just how far in you are."""
        lines = self._lines("--turn", "0")
        self.assertTrue(any("Turn 0 · make a portal" in line for line in lines))

    # ── Who said what ────────────────────────────────────────────────

    def test_every_line_of_a_reply_is_attributed_to_the_speaker(self):
        """The label says it once at the top; a fifty-turn session is
        scrolled, so the block has to keep saying it all the way down."""
        from cs import ui

        lines = self._lines("--turn", "2")
        body = self._block(lines, "Copilot")
        self.assertGreater(len(body), 5)
        for line in body:
            self.assertTrue(_plain(line).startswith(f"  {ui.SPINE}"), line)

    def test_the_rail_survives_a_blank_line_inside_a_reply(self):
        """A reply with paragraphs would otherwise break into what looks
        like several replies."""
        from cs import ui

        lines = self._lines("--turn", "2")
        start = next(i for i, line in enumerate(lines) if "Copilot" in line)
        railed = [line for line in lines[start + 1:start + 20]]
        self.assertIn(f"  {ui.SPINE}", [_plain(line).rstrip() for line in railed])

    def test_a_block_never_ends_in_an_empty_rail(self):
        """Replies almost always end with a newline, and a rail beside
        nothing reads as a block with more in it than it has."""
        from cs import ui

        lines = self._lines("--turn", "2")
        body = self._block(lines, "Copilot")
        self.assertNotEqual(_plain(body[-1]).rstrip(), f"  {ui.SPINE}")

    def test_the_speaker_name_starts_in_the_same_column_in_either_glyph_mode(self):
        """The emoji marks are two cells and their plain forms are one, so a
        fixed gap put the name in a different column depending on
        CS_GLYPHS — visible the moment a rail runs underneath it."""
        from cs import ui

        emoji = _plain(ui.speaker("👤", "You"))
        plain = _plain(ui.speaker(">", "You"))
        # Columns, not characters: the emoji is one character and two cells,
        # which is the whole reason the two used to disagree.
        self.assertEqual(ui.cells(emoji[:emoji.index("You")]),
                         ui.cells(plain[:plain.index("You")]))

    # ── The page holds its width ─────────────────────────────────────

    def test_the_turn_rule_ends_at_the_same_column_at_every_width(self):
        """The note rides the right-hand end, so a long title has to shorten
        rather than push the note off the edge."""
        from cs import ui

        for columns in ("46", "64", "80", "92", "120"):
            with self.subTest(columns=columns):
                lines = self._lines("--turn", "2", columns=columns)
                rule = next(line for line in lines if "Turn 2" in line)
                inner = min(int(columns), 100) - 4
                self.assertEqual(ui.cells(_plain(rule)), inner + len("  "))

    def test_no_line_of_a_transcript_overruns_the_window(self):
        """Code is allowed to overflow — its alignment is the information —
        but prose, rails and rules are not."""
        from cs import ui

        for columns in ("46", "72", "96"):
            with self.subTest(columns=columns):
                for line in self._lines(columns=columns):
                    if "def parse" in line or "return text" in line:
                        continue
                    self.assertLessEqual(ui.cells(_plain(line)), int(columns), line)

    # ── What the shapes in a reply turn into ─────────────────────────

    def test_a_heading_in_a_reply_outranks_the_prose_around_it(self):
        """Bold alone does not: a reply is already full of bold spans, so
        the one structure a long answer has read as another sentence."""
        with _colour_ui() as ui:
            rendered = ui.markdown("## The parser\n\nplain words here", 60)
            accent = ui.ACCENT
            heading = next(line for line in rendered if "The parser" in line)
            body = next(line for line in rendered if "plain words" in line)
            self.assertTrue(accent, "colour was not actually on")
            self.assertIn(accent, heading)
            self.assertNotIn(accent, body)

    def test_a_quotation_is_marked_rather_than_left_with_its_marker(self):
        """'> Note:' passed through as prose and read as a typo."""
        from cs import ui

        lines = self._lines("--turn", "2")
        quoted = next(line for line in lines if "old behaviour" in line)
        plain = _plain(quoted)
        self.assertNotIn(">", plain)
        # Inside the speaker's rail, and carrying one of its own.
        self.assertEqual(plain.count(ui.SPINE), 2, plain)

    def test_a_heading_and_its_prose_begin_in_the_same_column(self):
        """The heading marker used to take the two columns to the left of
        the text, which the speaker's rail also wants."""
        from cs import ui

        def column(line: str, word: str) -> int:
            plain = _plain(line)
            return ui.cells(plain[:plain.index(word)])

        lines = self._lines("--turn", "2")
        heading = next(line for line in lines if "The parser" in line)
        prose = next(line for line in lines if "tokenizer" in line)
        self.assertEqual(column(heading, "The parser"), column(prose, "I moved"))

    def test_an_absent_reply_is_furniture_and_not_content(self):
        """It is this view describing the record, not the record itself."""
        with _colour_ui() as ui:
            from cs import cli

            body = cli._turn_body(None, "(no reply recorded)", ui.VIOLET, 60)
        self.assertEqual(len(body), 1)
        self.assertIn(ui.MUTED, body[0])
        self.assertIn("(no reply recorded)", _plain(body[0]))

    # ── Both surfaces can draw it ────────────────────────────────────

    def test_the_transcript_uses_only_colours_the_reader_can_allocate(self):
        """The menu reads reports inside curses, which has to allocate a
        colour pair per index up front. A colour this view emits but
        `PALETTE_256` does not declare renders as plain text there — the
        same bug the reports had, and invisible from the shell."""
        with _colour_ui() as ui:
            from cs import cli, db

            conn = db.connect()
            try:
                detail = db.session_detail(conn, "sess-alpha")
                turns = db.session_transcript(conn, "sess-alpha")
            finally:
                conn.close()
            rendered = cli._render_transcript("sess-alpha", detail, turns, None)
            declared = set(ui.PALETTE_256)
        used = {int(n) for n in re.findall(r"\x1b\[38;5;(\d+)m", rendered)}
        self.assertTrue(used, "colour was not actually on")
        self.assertEqual(used - declared, set())

    def test_a_credential_in_a_reply_is_masked_inside_the_rail(self):
        """The rail is added after rendering, so it is the last chance to
        get the order wrong."""
        secret = "ghp_" + "C" * 36
        conn = sqlite3.connect(Path(os.environ["COPILOT_HOME"]) / "session-store.db")
        conn.execute(
            "INSERT INTO turns VALUES (92,'sess-alpha',4,'deploy',?,"
            "'2026-08-18T10:00:00.000Z')",
            (f"used {secret} to authenticate",),
        )
        conn.commit()
        conn.close()
        out = "\n".join(self._lines("--turn", "4"))
        self.assertNotIn(secret, out)
        self.assertIn("authenticate", out)


class RulePrimitiveTest(StoreTest):
    """`ui.rule` grew a right-hand note; these hold its arithmetic."""

    def test_an_untitled_rule_is_unchanged(self):
        from cs import ui

        self.assertEqual(_plain(ui.rule(40)), "  " + "─" * 40)

    def test_a_note_does_not_widen_the_rule(self):
        from cs import ui

        for width in (30, 46, 72, 88):
            with self.subTest(width=width):
                bare = ui.cells(_plain(ui.rule(width, "Turn 3")))
                noted = ui.cells(_plain(ui.rule(width, "Turn 3", note="09:41")))
                self.assertEqual(bare, noted)

    def test_a_long_title_shortens_and_the_note_survives(self):
        """The note is the fixed part: it is what the rule was widened for."""
        from cs import ui

        drawn = _plain(ui.rule(40, "T" * 200, note="09:41 · 2.3k chars"))
        self.assertIn("09:41 · 2.3k chars", drawn)
        self.assertEqual(ui.cells(drawn), 42)

    def test_a_note_too_wide_for_the_rule_does_not_produce_a_negative_run(self):
        """A window narrower than the note itself is a rounding accident
        away from a traceback."""
        from cs import ui

        for width in (1, 4, 8, 12):
            with self.subTest(width=width):
                drawn = _plain(ui.rule(width, "Turn 3", note="09:41 · 2.3k chars"))
                self.assertNotIn("─" * 200, drawn)
                self.assertGreater(len(drawn), 0)


class SpinePrimitiveTest(StoreTest):
    """`ui.spine` attributes an already-rendered block to a speaker."""

    def test_the_rail_replaces_the_indent_rather_than_adding_to_it(self):
        """Body text has to stay in the column every other view puts it in,
        or a transcript and a report stop lining up when read in turn."""
        from cs import ui

        railed = ui.spine(["    hello"], indent="    ")
        self.assertEqual(_plain(railed[0]), f"  {ui.SPINE} hello")

    def test_a_blank_line_keeps_the_rail_and_no_trailing_space(self):
        from cs import ui

        railed = ui.spine(["", "    x"], indent="    ")
        self.assertEqual(_plain(railed[0]), f"  {ui.SPINE}")
        self.assertEqual(_plain(railed[0]), _plain(railed[0]).rstrip())

    def test_a_line_that_is_not_indented_still_gets_a_rail(self):
        """A caller that hands over its own lines must not silently lose
        the attribution the rail exists to give."""
        from cs import ui

        railed = ui.spine(["hello"], indent="    ")
        self.assertTrue(_plain(railed[0]).startswith(f"  {ui.SPINE} "))
        self.assertIn("hello", _plain(railed[0]))

    def test_the_rail_has_a_plain_form(self):
        """CS_GLYPHS=ascii exists for terminals that draw a hollow box, and
        a rail is exactly the kind of character they get wrong."""
        from cs import ui

        self.assertEqual(len(ui.SPINE), 1)
        self.assertEqual(ui.cells(ui.SPINE), 1)


class TranscriptExportTest(StoreTest):
    """The markdown export is the transcript's other rendering."""

    def test_the_export_still_marks_both_speakers(self):
        _, out = self._run("export", "sess-alpha")
        self.assertIn("### 👤 You", out)
        self.assertIn("### 🤖 Copilot", out)

    def test_the_export_does_not_carry_terminal_furniture(self):
        """Markdown is read by something other than a terminal; a rail
        pasted into a document is noise."""
        from cs import ui

        _, out = self._run("export", "sess-alpha")
        self.assertNotIn(ui.SPINE, out)
        self.assertNotIn("\x1b[", out)


class TranscriptFromTheListingTest(StoreTest):
    """`t` on a listing is how most people reach a transcript."""

    def test_t_opens_the_transcript_for_the_row_under_the_cursor(self):
        """The key is advertised in the hint line, so it has to keep
        resolving to the reader and to the right session."""
        import curses

        from cs.cli import _listing_tui

        rows = [
            ("id-new", "2026-08-01T12:00", "Newest", "r/a", "/tmp", 1, 5_000_000),
            ("id-mid", "2026-08-01T11:00", "Middle", "r/b", "/tmp", 2, 5_000_000),
        ]
        self.assertEqual(_listing_tui(Screen([ord("t")]), rows, "Sessions"),
                         ("read", "id-new"))
        self.assertEqual(
            _listing_tui(Screen([curses.KEY_DOWN, ord("T")]), rows, "Sessions"),
            ("read", "id-mid"),
        )

    def test_the_key_and_the_hint_that_advertises_it_agree(self):
        """A hint naming a key nothing binds is worse than no hint."""
        from cs.cli import _hint_line

        self.assertIn("transcript", _hint_line(120, mouse=False))

"""Hostile stored text: terminal control sequences must never reach the screen."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from support import StoreTest


class TerminalInjectionTest(StoreTest):
    """Stored text is drawn on a terminal, and a terminal obeys what it reads.

    Repository names, branches, file paths, commit refs and model names are
    recorded from whatever workspace the agent ran in — nobody types them, so
    a hostile checkout chooses them. An escape sequence surviving into a
    report could set the window title, write the reader's clipboard, or erase
    the `[redacted:…]` marker printed a moment earlier.

    This walks one sequence through every such field and runs the whole
    command surface over it. It is deliberately a sweep and not a list of
    known sites: the point is that a view added later is covered the day it
    is written, without anyone remembering to come back here.
    """

    MARK = "PWNED"
    SEQUENCE = f"\x1b]0;{MARK}\x07"
    NEWLINE_MARK = "FORGED-LINE"

    COMMANDS = (
        ("show", "1"), ("brief", "1"), ("read", "1"), ("recent", "3650"),
        ("cost", "3650"), ("repos",), ("stats", "3650"), ("timeline", "3650"),
        ("agents", "3650"), ("audit",), ("coach",), ("rhythm",), ("context",),
        ("hooks",), ("search", "x"), ("all",), ("yolo",), ("skills",),
    )

    def setUp(self):
        super().setUp()
        store = Path(os.environ["COPILOT_HOME"]) / "session-store.db"
        conn = sqlite3.connect(store)
        session = conn.execute("SELECT id FROM sessions LIMIT 1").fetchone()[0]
        poisoned = self.SEQUENCE + "x\n" + self.NEWLINE_MARK
        for table, column in (
            ("sessions", "repository"), ("sessions", "branch"),
            ("sessions", "cwd"), ("sessions", "summary"),
            ("assistant_usage_events", "model"),
            ("session_files", "file_path"), ("session_refs", "ref_value"),
        ):
            key = "id" if table == "sessions" else "session_id"
            try:
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {key} = ?",
                    (poisoned, session),
                )
            except sqlite3.OperationalError:
                pass  # an older store simply has one fewer field to poison
        conn.commit()
        conn.close()

    def test_no_command_lets_an_escape_sequence_reach_the_terminal(self):
        escaped = []
        for command in self.COMMANDS:
            _code, out = self._run(*command)
            forged_row = out.startswith(self.NEWLINE_MARK) or f"\n{self.NEWLINE_MARK}" in out
            if self.MARK in out or "\x1b]" in out or forged_row:
                escaped.append(command[0])
        self.assertEqual(escaped, [], "stored text forged terminal output")

    def test_single_line_reports_do_not_admit_forged_rows(self):
        from cs import cli

        self.assertNotIn("\n", cli._project_tag("org/acme\nFAKE", ""))
        row = cli._spend_row("12", "acme\nFAKE", "", "2 calls")
        self.assertNotIn("\n", row)

"""Governance: unattended runs, handoff chains and the credential audit."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from support import CALM, CHILD, LEAK, PARENT, UNATTENDED, StoreTest, _add_governance_rows


class GovernanceTest(StoreTest):
    """Autonomy, handoffs and credential exposure — the three inferred views."""

    def setUp(self):
        super().setUp()
        _add_governance_rows(Path(self._tmp.name))

    # ── Autonomy ─────────────────────────────────────────────────────
    def test_yolo_flags_the_session_where_approvals_were_turned_off(self):
        code, out = self._run("yolo")
        self.assertEqual(code, 0)
        self.assertIn("Try it unsupervised", out)
        self.assertIn("you turned approvals off in the session", out)

    def test_yolo_does_not_flag_a_session_that_only_discusses_it(self):
        _, out = self._run("yolo")
        self.assertNotIn("A near-identical id", out)

    def test_yolo_infers_an_unattended_run_from_steps_per_prompt(self):
        _, out = self._run("yolo")
        self.assertIn("One prompt, long run", out)
        self.assertIn("unattended", out)

    def test_yolo_hides_supervised_sessions_until_asked(self):
        _, out = self._run("yolo")
        self.assertNotIn("A supervised session", out)
        _, everything = self._run("yolo", "--all")
        self.assertIn("A supervised session", everything)

    def test_yolo_says_what_it_can_and_cannot_know(self):
        _, out = self._run("yolo")
        self.assertIn("store records no approval mode", out)

    def test_yolo_rejects_unknown_options(self):
        code, _ = self._run("yolo", "--everything")
        self.assertEqual(code, 1)

    # ── Handoffs ─────────────────────────────────────────────────────
    def test_handoff_lists_both_ends_of_the_handoff(self):
        code, out = self._run("handoff")
        self.assertEqual(code, 0)
        self.assertIn("First half of the work", out)
        self.assertIn("Second half of the work", out)
        self.assertIn("emitted", out)
        self.assertIn("received", out)

    def test_handoff_chain_links_the_sessions_oldest_first(self):
        code, out = self._run("handoff", PARENT)
        self.assertEqual(code, 0)
        self.assertIn("2 sessions", out)
        self.assertLess(out.index("First half"), out.index("Second half"))
        self.assertIn("via HANDOFF.md", out)

    def test_handoff_chain_of_one_says_so_rather_than_drawing_a_tree(self):
        _, out = self._run("handoff", LEAK)
        self.assertIn("stands alone", out)

    def test_a_session_with_no_handoff_is_not_listed(self):
        _, out = self._run("handoff")
        self.assertNotIn("A supervised session", out)

    def test_the_handoff_view_labels_its_two_different_dates(self):
        """The chain lists sessions by when each *started*; the table below
        shows last activity. Identically formatted and unlabelled, one session
        appeared under two different dates."""
        _, out = self._run("handoff")
        self.assertIn("by when each started", out)
        self.assertIn("last active", out)

    # ── Credential exposure ──────────────────────────────────────────
    def test_audit_finds_a_password_pasted_into_a_prompt(self):
        code, out = self._run("audit")
        self.assertEqual(code, 0)
        self.assertIn("Wire up the database", out)
        self.assertIn("DB_PASSWORD", out)
        self.assertIn("pasted", out)

    def test_audit_never_prints_the_secret_itself(self):
        _, out = self._run("audit")
        self.assertNotIn("hunter2xyz", out)

    def test_audit_can_be_scoped_to_one_session(self):
        _, out = self._run("audit", CALM)
        self.assertIn("Nothing credential-shaped found", out)

    def test_audit_rejects_a_session_that_does_not_exist(self):
        code, out = self._run("audit", "does-not-exist")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_no_report_runs_off_the_window(self):
        """Every view, at every width anyone actually uses.

        The governance test below asserts the shape of four tables. This one
        asserts the cheaper, broader thing about all of them: nothing drawn
        is wider than the terminal it is drawn on. Fixed widths are how that
        stops being true — a hand-built table, a 30-column bar, a footnote
        with its line breaks typed in — and each of those shipped at least
        once before this existed.
        """
        import shutil
        from unittest import mock

        import cs.cli as cli

        views = {
            "stats": lambda: cli._render_stats(3650),
            "timeline": lambda: cli._render_timeline(3650),
            "cost": lambda: cli._render_cost(3650),
            "agents": lambda: cli._render_agents(3650),
            "coach": lambda: cli._render_coach(3650),
            "rhythm": lambda: cli._render_rhythm(3650),
            "context": cli._render_context,
            "hooks": cli._render_hooks,
            "mcp": cli._render_mcp,
            "skills": lambda: cli._render_assets("skills", 25),
            "profiles": lambda: cli._render_assets("agents", 25),
            "repos": cli._render_repos,
            "yolo": lambda: cli._render_yolo(True),
            "handoffs": cli._render_handoffs,
            "audit": lambda: cli._render_audit(None),
        }
        for name, render in views.items():
            for columns in (52, 60, 72, 80, 100, 140):
                size = os.terminal_size((columns, 40))
                with self.subTest(view=name, columns=columns), \
                        mock.patch.object(shutil, "get_terminal_size",
                                          return_value=size):
                    text = re.sub(r"\x1b\[[0-9;]*m", "", cli._capture(render))
                    for line in text.split("\n"):
                        self.assertLessEqual(
                            cli.ui.cells(line), columns,
                            f"{name} runs off {columns} columns: {line!r}")

    def test_every_governance_table_has_the_same_structure_at_any_width(self):
        """Delegation, autonomy, handoffs and security are one table shape.

        Each was laid out by hand with its own guessed widths, so rows ran off
        a small window and dividers were measured off a different string from
        the rows they divided. They now share _fit_columns/_cell, and this
        asserts the shape rather than any one report's columns.
        """
        import shutil
        from unittest import mock

        import cs.cli as cli

        views = {
            "delegation": lambda: cli._render_agents(3650),
            "autonomy": lambda: cli._render_yolo(True),
            "handoffs": cli._render_handoffs,
            "security": lambda: cli._render_audit(None),
        }
        for name, render in views.items():
            for columns in (40, 46, 60, 80, 100, 140):
                size = os.terminal_size((columns, 40))
                with mock.patch.object(shutil, "get_terminal_size", return_value=size):
                    text = re.sub(r"\x1b\[[0-9;]*m", "", cli._capture(render))
                lines = [line for line in text.split("\n") if line.strip()]
                for line in lines:
                    self.assertLessEqual(
                        cli.ui.cells(line), columns,
                        f"{name} overflows {columns} columns: {line!r}")
                dividers = [line for line in lines if set(line.strip()) == {"─"}]
                self.assertTrue(dividers, f"{name} has no ruled table at {columns}")
                # Rows are the block directly under a divider, up to the blank
                # line that ends the table — hence the unfiltered text here.
                block = text.split("\n")
                for divider in dividers:
                    for row in block[block.index(divider) + 1:]:
                        if not row.strip():
                            break
                        self.assertLessEqual(
                            cli.ui.cells(row.rstrip()), cli.ui.cells(divider),
                            f"{name} row outruns its divider at {columns}: {row!r}")

    def test_governance_cells_measure_terminal_width(self):
        import cs.cli as cli

        self.assertEqual(cli.ui.cells(cli._cell("✅" * 10, 10)), 10)
        self.assertEqual(cli.ui.cells(cli._cell("漢字" * 5, 10)), 10)

    def test_the_audit_shows_the_masked_line_under_each_row(self):
        """The view raised "what is it?" and the answer was three commands away.

        The line comes out of the masking pass, so an unmasked line is dropped
        rather than printed — including when masking is switched off entirely.
        """
        _, out = self._run("audit")
        self.assertIn("[redacted", out, "no masked line to check")
        self.assertNotIn("hunter2xyz", out)
        # The turn column names the turn the line came from, so the command in
        # the footnote opens exactly what is on screen. Two different turn
        # numbers on adjacent lines is the defect this replaced.
        import re as _re

        row = next(line for line in out.split("\n") if "66666666" in line)
        match = _re.search(r"66666666\s+\d+\s+(\d+)", row)
        self.assertIsNotNone(match, f"no turn on the row: {row!r}")
        turn = match.group(1)
        self.assertIn(f"inspect cs read 66666666 --turn {turn}", out)

        import os as _os
        from unittest import mock

        from cs import db, signals
        with mock.patch.dict(_os.environ, {"CS_REDACT": "0"}):
            conn = db.connect()
            rows = signals.exposures(conn)
            conn.close()
        self.assertTrue(rows, "nothing found to check")
        for entry in rows:
            self.assertEqual(entry["line"], "",
                             "an unmasked line escaped when masking was off")

    def test_the_masked_line_keeps_the_mask_in_view(self):
        """Truncating from the left cut off the very thing it was shown for."""
        import cs.cli as cli

        line = "a" * 70 + "TOKEN=[redacted] trailing"
        shown = cli._around(line, "[redacted", 40)
        self.assertIn("[redacted", shown)
        self.assertLessEqual(len(shown), 40)
        self.assertTrue(shown.startswith("…"))
        short = cli._around("TOKEN=[redacted]", "[redacted", 40)
        self.assertEqual(short, "TOKEN=[redacted]")

    def test_the_audit_summary_counts_values_not_sessions(self):
        """The block was headed 'values' and counted sessions, understating it:
        8 sessions held 15 values, 29 held 81."""
        _, out = self._run("audit")
        self.assertIn("1 finding across 1 session", out)
        self.assertIn("1 value pasted by you", out)

    def test_the_audit_attributes_each_side_separately(self):
        from cs import db, signals

        conn = sqlite3.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute(
            "UPDATE turns SET assistant_response = ? WHERE session_id = ?",
            ("connected with appsecret=ReplySecret999", LEAK),
        )
        conn.commit()
        conn.close()

        conn = db.connect()
        rows = signals.exposures(conn, LEAK)
        conn.close()
        self.assertEqual(
            {(row["side"], row["count"]) for row in rows},
            {("you", 1), ("agent", 1)},
        )
        _, out = self._run("audit", LEAK)
        self.assertIn("1 value pasted by you", out)
        self.assertIn("Assistant output · review context", out)

    def test_the_audit_opens_as_an_action_screen(self):
        import shutil
        from unittest import mock

        import cs.cli as cli

        _, out = self._run("audit")
        self.assertIn("Security posture", out)
        self.assertIn("ACTION REQUIRED", out)
        self.assertIn("Immediate action · pasted by you", out)
        self.assertLess(out.index("ACTION REQUIRED"), out.index("Immediate action"))
        size = os.terminal_size((46, 40))
        with mock.patch.object(shutil, "get_terminal_size", return_value=size):
            narrow = cli._capture(lambda: cli._render_audit(None))
        self.assertRegex(narrow, r"risk\s+session\s+summary")

    def test_the_audit_uses_a_wide_terminal_for_the_session_summary(self):
        import shutil
        from unittest import mock

        import cs.cli as cli

        summary = "Investigate credential exposure without truncating the final words"
        conn = sqlite3.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute("UPDATE sessions SET summary = ? WHERE id = ?", (summary, LEAK))
        conn.commit()
        conn.close()
        size = os.terminal_size((159, 40))
        with mock.patch.object(shutil, "get_terminal_size", return_value=size):
            out = cli._capture(lambda: cli._render_audit(None))
        row = next(line for line in out.splitlines() if LEAK[:8] in line)
        self.assertIn(summary, row)
        divider = next(line for line in out.splitlines()
                       if line.strip() and set(line.strip()) == {"─"})
        self.assertGreater(len(divider), 120)
        self.assertNotIn("\n      summary  ", out)

    def test_the_finding_column_keeps_whole_names(self):
        """`SPassword, SpMS_DBPas…` names nothing you could search for.

        Whole names and a count of the rest say the same thing in the same
        room, and the count is the part that was missing entirely.
        """
        import cs.cli as cli

        names = ["DB_PASSWORD", "api_key", "token"]
        self.assertEqual(cli._names(names, 40), "DB_PASSWORD, api_key, token")
        short = cli._names(names, 20)
        self.assertNotIn("…", short)
        self.assertTrue(short.endswith("+2"), short)
        self.assertLessEqual(cli.ui.cells(short), 20)
        # One name that cannot fit at all is still cut: something beats nothing.
        self.assertEqual(cli._names(["ATLASSIAN_API_TOKEN"], 8), "ATLASSI…")
        self.assertEqual(cli._names([], 20), "")

    def test_the_audit_lines_up_the_evidence_and_the_command(self):
        """The row's second line is two columns, not a trailing sentence.

        `evidence … · inspect cs read …` put the same boilerplate at a
        different column on every row, so the table lost its right edge and
        the commands could not be scanned down.
        """
        import shutil
        from unittest import mock

        import cs.cli as cli

        other = "77777777-7777-4777-8777-777777777777"
        conn = sqlite3.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,'local','main',?,?,?)",
            (other, "/tmp/o", "acme/portal", "Paste a token by mistake",
             "2026-07-07T09:00", "2026-07-07T09:10"),
        )
        conn.execute(
            "INSERT INTO turns (session_id, turn_index, user_message,"
            " assistant_response) VALUES (?,?,?,?)",
            (other, 12, "here is my ATLASSIAN_API_TOKEN=abcd1234efgh5678", "ok"),  # gitleaks:allow
        )
        conn.commit()
        conn.close()

        size = os.terminal_size((159, 40))
        with mock.patch.object(shutil, "get_terminal_size", return_value=size):
            out = cli._capture(lambda: cli._render_audit(None))
        import re as _re

        lines = [_re.sub(r"\x1b\[[0-9;]*m", "", line) for line in out.splitlines()]
        divider = next(line for line in lines
                       if line.strip() and set(line.strip()) == {"─"})
        commands = [line for line in lines if "inspect cs read" in line]
        self.assertGreater(len(commands), 1, "need two rows to compare")
        starts = {line.index("inspect cs read") for line in commands}
        self.assertEqual(len(starts), 1, f"commands start in {len(starts)} places")
        for line in commands:
            self.assertTrue(line.startswith("      evidence  "), line)
            self.assertLessEqual(len(line), len(divider))

    def test_the_audit_puts_each_count_beside_what_it_means(self):
        """Three columns of label over three columns of meaning made you count
        across a 150-column window to pair them up. A narrow window has no
        room for one line, so there each tier gets its own — never a grid."""
        import shutil
        from unittest import mock

        import cs.cli as cli

        size = os.terminal_size((159, 40))
        with mock.patch.object(shutil, "get_terminal_size", return_value=size):
            wide = cli._capture(lambda: cli._render_audit(None))
        self.assertRegex(wide, r"\d+ CRITICAL confirmed key format\b")
        self.assertRegex(wide, r"\d+ HIGH token or URL login\b")
        size = os.terminal_size((80, 40))
        with mock.patch.object(shutil, "get_terminal_size", return_value=size):
            narrow = cli._capture(lambda: cli._render_audit(None))
        self.assertRegex(narrow, r"CRITICAL\s+confirmed key format\n")
        self.assertRegex(narrow, r"HIGH\s+token or URL login\n")

    def test_the_scanner_knows_the_credential_formats_in_common_use(self):
        """A shape it cannot name is a shape it cannot report.

        Each of these was missed: Stripe uses an underscore where OpenAI uses
        a hyphen, Azure writes `AccountKey` where the assignment rule wanted
        `AccessKey`, and a Slack webhook keeps its secret in the URL path.
        """
        from cs import redact

        corpus = {
            "gitlab-token": "glpat-" "ABCDEFGHIJ1234567890",  # gitleaks:allow
            "slack-webhook":
                "https://hooks.slack.com/services/T00000000/B00000000/"
                "XXXXXXXXXXXXXXXXXXXXXXXX",
            "google-oauth-secret": "GOCSPX-" "1234567890abcdefghijklmn",  # gitleaks:allow
            "stripe-key": "sk_live_"  # gitleaks:allow - split: scanners read it as live
            "51H8abcdefghijklmnopqrstuvwxyz0123",
            "npm-token": "npm_abcdefghijklmnopqrstuvwxyz0123456789",
            "pypi-token":
                "pypi-" "AgEIcHlwaS5vcmcCJDU0YWJjZGVmLTEyMzQtNTY3OC05YWJj",  # gitleaks:allow
            "huggingface-token": "hf_" "ABCdefGHIjklMNOpqrSTUvwxYZ0123456789",
            "databricks-token":
                "dapi" "1234567890abcdef1234567890abcdef",  # gitleaks:allow
            "sendgrid-key":
                "SG." "abcdefghijklmnopqrstuv."
                "abcdefghijklmnopqrstuvwxyz0123456789abcdefghi",
            "basic-auth": "Authorization: Basic YWRtaW46c3VwZXJzZWNyZXQxMjM=",
            "pgp-private-key":
                "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOY\n"
                "-----END PGP PRIVATE KEY BLOCK-----",
            "azure-storage-key":
                "AccountKey=" "abcdefgh1234567890ABCDEFGH1234567890abcdefgh12==",  # gitleaks:allow
            # The nine below had a rule in `redact._RULES` and no sample here,
            # so nothing ever ran them. A pattern that stops matching is
            # silent by construction: the audit simply reports fewer findings,
            # which reads as good news.
            "github-pat":
                "github_pat_" "11ABCDEFG0abcdefghijklmnopqrstuvwxyz012345",  # gitleaks:allow
            "aws-key-id": "AKIA" "IOSFODNN7EXAMPLE",  # gitleaks:allow
            "slack-token": "xoxb-" "123456789012-1234567890123-abcdEFGH",  # gitleaks:allow
            "google-api-key":
                "AIza" "SyA0123456789abcdefghijklmnopqrstuvw",  # gitleaks:allow
            "api-key": "sk-ant-" "api03abcdefghijklmnopqrstuvwxyz0123",  # gitleaks:allow
            "azure-sas": "sig=" "abcdefghijklmnopqrstuvwxyz0123456789%2F",  # gitleaks:allow
            "jwt":
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."  # gitleaks:allow
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
            "bearer-token":
                "Authorization: Bearer " "abcdefghijklmnopqrstuvwxyz0123",  # gitleaks:allow
            "url-credentials":
                "postgres://app:" "hunter2secret" "@db.internal:5432/prod",  # gitleaks:allow
            # Both of these are exercised elsewhere in this file, on a stored
            # session rather than on a string. They are here as well because
            # the completeness guard below is only worth having if it covers
            # every rule — an exception list is a place for the next one to hide.
            "github-token": "ghp_" "B1cdefghijklmnopqrstuvwxyz0123456789",  # gitleaks:allow
            "private-key":
                "-----BEGIN RSA PRIVATE KEY-----\n"  # gitleaks:allow
                "MIIEowIBAAKCAQEAmZ1nOtBcdefghijkl\n"
                "-----END RSA PRIVATE KEY-----",
        }
        for kind, sample in corpus.items():
            with self.subTest(kind=kind):
                found = redact.findings(sample)
                self.assertIn(kind, [k for k, _ in found], f"missed: {sample[:40]}")
                self.assertIn(redact.severity(kind), redact.RANK)
                # And the value never survives the masker. Asserted whole:
                # a rule that keeps a documented prefix still has to have
                # replaced everything after it.
                masked = redact.redact(sample)
                self.assertNotIn(sample, masked)
                for secret in ("ABCDEFGHIJ1234567890", "XXXXXXXXXXXXXXXXXXXXXXXX",
                               "51H8abcdefghijklmnopqrstuvwxyz0123",
                               "hunter2secret", "IOSFODNN7EXAMPLE",
                               "YWRtaW46c3VwZXJzZWNyZXQxMjM"):
                    if secret in sample:
                        self.assertNotIn(secret, masked)
        # The guard that keeps this list honest as rules are added.
        self.assertEqual(
            {kind for kind, _ in redact._RULES} - set(corpus), set(),
            "a credential rule with no sample is a rule no test has run",
        )
        self.assertEqual(set(corpus) - {kind for kind, _ in redact._RULES}, set())
        short = "Authorization: Basic dXNlcjpwYXNz"
        self.assertIn("basic-auth", [kind for kind, _ in redact.findings(short)])
        self.assertNotIn("dXNlcjpwYXNz", redact.redact(short))
        self.assertFalse(redact.findings("Basic dGVzdA=="))

    def test_an_already_masked_value_is_not_masked_again(self):
        """The specific label was being overwritten by the generic one, so
        `AccountKey=[redacted:azure-storage-key]` came out saying nothing."""
        from cs import redact

        masked = redact.redact(
            "AccountKey=abcdefgh1234567890ABCDEFGH1234567890abcdefgh12==;Foo=1"  # gitleaks:allow
        )
        self.assertIn("[redacted:azure-storage-key]", masked)

    def test_stored_text_cannot_drive_the_terminal(self):
        """A transcript is untrusted input, and the pager runs `less -R`, so
        an escape sequence in a stored turn reached the terminal intact — it
        could rewrite the clipboard, retitle the window or clear the screen.
        Stripping happens before the `CS_REDACT` check because a user turning
        masking off is asking to see secrets, not to be attacked."""
        from cs import redact

        hostile = {
            "osc-52-clipboard": "\x1b]52;c;cGF5bG9hZA==\x07",
            "osc-0-title": "\x1b]0;pwned\x07",
            "csi-erase": "\x1b[2J",
            "csi-colour": "\x1b[31m",
            "c1-csi": "\x9b2J",
            "dcs": "\x1bP0;1|x\x1b\\",
            "charset-select": "\x1b(0",
            "carriage-return": "safe\rHACKED",
        }
        for kind, payload in hostile.items():
            with self.subTest(kind=kind):
                out = redact.redact(f"before {payload} after")
                self.assertNotIn("\x1b", out)
                self.assertNotIn("\x9b", out)
                self.assertNotIn("\r", out)
                self.assertNotIn("2J", out)
                # Real content either side of it survives.
                self.assertIn("before", out)
                self.assertIn("after", out)
        # An OSC that never terminates consumes the rest of the line. Losing
        # that tail is the safe reading: the alternative is guessing where the
        # attacker meant the sequence to stop.
        unterminated = redact.redact("before \x1b]52;c;abc after")
        self.assertNotIn("\x1b", unterminated)
        self.assertEqual(unterminated.strip(), "before")
        # Layout characters are content, not control.
        self.assertEqual(redact.redact("a\nb\tc"), "a\nb\tc")
        from unittest import mock
        with mock.patch.dict(os.environ, {"CS_REDACT": "0"}):
            self.assertNotIn("\x1b", redact.redact("\x1b]0;x\x07hi"))

    def test_meaningful_joiners_survive_terminal_sanitising(self):
        from cs import redact

        family = "👨\u200d👩\u200d👧"
        persian = "می\u200cرود"
        self.assertEqual(redact.plain(family), family)
        self.assertEqual(redact.plain(persian), persian)
        self.assertEqual(redact.plain("evil\u202egnp.exe"), "evilgnp.exe")

    def test_a_secret_spanning_lines_is_masked_when_the_output_is_bulleted(self):
        """`cs brief` splits before it masks, and every multi-line rule — a PEM
        block above all — needs both its ends in the same string to fire."""
        from cs import cli

        pem = ("deploy with this\n-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEAx\n-----END RSA PRIVATE KEY-----")
        self.assertEqual(cli._bullets(pem, 5), ["deploy with this", "[redacted:private-key]"])
        self.assertNotIn("MIIEowIBAAKCAQEAx", "".join(cli._bullets(pem, 5)))

    def test_a_secret_is_masked_before_the_width_decides_what_fits(self):
        """Truncating first and masking after prints the opening characters of
        the secret — the part an attacker most wants — and calls it safe."""
        from cs import cli, redact

        token = "ghp_" + "A" * 36  # gitleaks:allow
        masked = redact.redact(f"token {token} ok")
        self.assertNotIn("ghp_A", cli.ui.trunc(masked, 20))
        # No caller may put the truncation first.
        source = Path(cli.__file__).read_text()
        self.assertNotIn("redact.redact(ui.trunc", source)

    def test_a_value_whose_closing_quote_was_cut_off_is_still_masked(self):
        """Stored text arrives truncated, so the closing quote is often gone.
        Requiring the pair meant the match failed and the value printed."""
        from cs import redact

        self.assertEqual(redact.redact('password: "s3cr3t-value-here'), 'password: "[redacted]"')
        self.assertEqual(redact.redact('password: "s3cr3t-value-here"'), 'password: "[redacted]"')
        self.assertEqual(redact.redact("max_tokens: 4096"), "max_tokens: 4096")

    def test_the_audit_reads_checkpoints_as_well_as_turns(self):
        """A checkpoint is a separate record, written by the agent, and it
        outlives the turns — scanning turns alone left it unread."""
        import sqlite3 as sq

        from cs import db, signals

        conn = sq.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute(
            "UPDATE checkpoints SET work_done = ? WHERE checkpoint_number = 2",
            ("- wired it up with SNOWFLAKE_PASSWORD=fromcheckpoint99",),
        )
        conn.commit()
        conn.close()

        conn = db.connect()
        rows = signals.exposures(conn, "sess-alpha")
        conn.close()
        saved = [row for row in rows if row["side"] == "checkpoint"]
        self.assertTrue(saved, "the checkpoint was never scanned")
        self.assertIn("SNOWFLAKE_PASSWORD", saved[0]["hints"])
        self.assertFalse(saved[0]["pasted"], "a checkpoint is not something you typed")

        _, out = self._run("audit")
        self.assertIn("Saved checkpoints", out)
        self.assertNotIn("fromcheckpoint99", out)
        # A checkpoint has no turn to open, so the row offers the view that
        # actually reads one back rather than '--turn 0'.
        self.assertIn("inspect cs show sess-alp", out)

        # Older checkpoints are not scanned because `cs brief` cannot open
        # them; every audit command must lead to the record that matched.
        conn = sq.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute(
            "UPDATE checkpoints SET work_done = ? WHERE checkpoint_number = 1",
            ("OLD_PASSWORD=stalecheckpoint99",),
        )
        conn.commit()
        conn.close()
        conn = db.connect()
        rows = signals.exposures(conn, "sess-alpha")
        conn.close()
        self.assertNotIn(
            "OLD_PASSWORD",
            [hint for row in rows for hint in row["hints"]],
        )
        conn = sq.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute(
            "UPDATE checkpoints SET overview = ? WHERE checkpoint_number = 2",
            ("HIDDEN_PASSWORD=notshownbybrief",),
        )
        conn.commit()
        conn.close()
        conn = db.connect()
        rows = signals.exposures(conn, "sess-alpha")
        conn.close()
        self.assertNotIn(
            "HIDDEN_PASSWORD",
            [hint for row in rows for hint in row["hints"]],
        )

    def test_checkpoint_only_audit_counts_saved_values(self):
        import sqlite3 as sq

        conn = sq.connect(Path(self._tmp.name) / "session-store.db")
        conn.execute("UPDATE turns SET user_message = '', assistant_response = ''")
        conn.execute(
            "UPDATE checkpoints SET work_done = ? WHERE checkpoint_number = 2",
            ("SNOWFLAKE_PASSWORD=fromcheckpoint99",),
        )
        conn.commit()
        conn.close()
        _, out = self._run("audit", "sess-alpha")
        self.assertIn("1 value in saved checkpoints", out)
        self.assertNotIn("0 values", out)

    def test_the_audit_reports_sensitive_file_paths_that_were_touched(self):
        """The store proves a sensitive path was touched, not that it was read."""
        import sqlite3 as sq

        from cs import db, signals

        conn = sq.connect(Path(self._tmp.name) / "session-store.db")
        conn.executemany(
            "INSERT INTO session_files (session_id, file_path, tool_name)"
            " VALUES (?,?,?)",
            [("sess-alpha", "/tmp/a/portal/.env", "edit"),
             ("sess-alpha", "/tmp/a/deploy/id_rsa", "edit"),
             ("sess-alpha", "/tmp/a/portal/README.md", "edit")],
        )
        conn.commit()
        conn.close()

        conn = db.connect()
        touched = signals.sensitive_files(conn, "sess-alpha")
        conn.close()
        self.assertEqual(len(touched), 1)
        self.assertEqual(touched[0]["count"], 2, "README is not a credential file")
        self.assertEqual(set(touched[0]["kinds"]), {"env file", "ssh private key"})

        _, out = self._run("audit")
        self.assertIn("Credential files touched", out)
        self.assertIn("env file", out)
        self.assertIn("portal/.env", out)
        self.assertIn("deploy/id_rsa", out)
        # And it reaches the per-session risk block too.
        self.assertIn("credential file", self._run("show", "sess-alpha")[1])
        self.assertIn("touched", self._run("show", "sess-alpha")[1])

    def test_findings_report_a_name_or_a_public_prefix_but_no_value(self):
        from cs import redact

        found = redact.findings("token=ghp_" + "a" * 30 + " and password=letmein99")
        self.assertEqual(
            sorted(kind for kind, _ in found), ["credential", "github-token"]
        )
        hints = {hint for _, hint in found}
        self.assertIn("ghp_…", hints)
        self.assertIn("password", hints)
        self.assertNotIn("letmein99", " ".join(hints))

    # ── Per-session block ────────────────────────────────────────────
    def test_show_carries_the_risk_block_when_there_is_something_to_say(self):
        _, out = self._run("show", LEAK)
        self.assertIn("Risk & continuity", out)
        self.assertIn("secrets", out)

    def test_show_reports_the_handoff_role(self):
        """A finding outranks the page it is on, so it leads both forms.

        Risk and continuity used to be filed with the inventory, on the
        theory that they were readings rather than judgements. That was a
        classification argument winning over a practical one: an unattended
        run or a credential in a transcript is the single most important
        thing on the page, and burying it behind a flag meant the short
        form — the one people actually type — was the one that hid it.
        """
        _, out = self._run("show", CHILD)
        self.assertIn("Risk & continuity", out)
        self.assertIn("received", out)
        self.assertIn("Risk & continuity", self._run("brief", CHILD)[1])

    def test_a_clean_session_gets_no_risk_block_at_all(self):
        _, out = self._run("show", CALM)
        self.assertNotIn("Risk & continuity", out)

    # ── Short ids ────────────────────────────────────────────────────
    def test_an_unambiguous_id_prefix_resolves(self):
        code, out = self._run("show", UNATTENDED[:8])
        self.assertEqual(code, 0)
        self.assertIn("One prompt, long run", out)

    def test_an_ambiguous_prefix_asks_for_more_characters(self):
        code, _ = self._run("show", "111111")
        self.assertEqual(code, 1)

    def test_a_row_number_still_wins_over_a_prefix(self):
        self._run("recent", "3650")
        code, out = self._run("show", "1")
        self.assertEqual(code, 0)

    def test_a_credential_name_has_to_start_a_word(self):
        """The audit is only worth reading if its rows are real.

        Every name here was found by hand in the real store: the left column
        is what a credential assignment looks like in the wild, the right is
        English prose that used to be reported as a leak because "bypass"
        ends in "pass".
        """
        from cs import redact
        for line in ("DB_PASSWORD=hunter2xyz", "spring.datasource.password: s3cr3t",
                     "client-secret: abcdefgh", '{"apiKey": "abcd1234"}',
                     "SpMS_DBPassword=abcd1234", "jdbcPassword=abcd1234",
                     "PASSWD=abcd1234", "X-Api-Key: abcdefgh",
                     "dbpassword=abcd1234", "appsecret=abcd1234",
                     "accesstoken=abcd1234"):
            with self.subTest(line=line):
                self.assertTrue(redact.findings(line), f"missed: {line}")
        for line in ('- Wording pass: "has to run on capacity"',
                     "- Temporary bypass: move it on", "passing: the tests are green",
                     "compass: pointing north", "surpass: expectations here"):
            with self.subTest(line=line):
                self.assertFalse(redact.findings(line), f"false positive: {line}")

    def test_the_audit_ranks_a_certain_leak_above_a_guess(self):
        """A private key and a password-ish name are not the same finding."""
        from cs import cli, redact
        self.assertEqual(redact.severity("private-key"), "critical")
        self.assertEqual(redact.severity("credential"), "medium")
        code, out = self._run("audit")
        self.assertEqual(code, 0)
        self.assertIn("REVIEW", out)
        self.assertIn("Sorted by risk ↓", out)
        # Ranked, not listed. The old summary was one catch-all kind ×92,
        # which said nothing about what to look at first.
        key, descending = cli._REPORT_COLUMNS["audit"]["risk"]
        self.assertTrue(descending)
        worst = {"rank": redact.RANK.index("critical"), "count": 1}
        noise = {"rank": redact.RANK.index("medium"), "count": 99}
        self.assertGreater(key(worst), key(noise))

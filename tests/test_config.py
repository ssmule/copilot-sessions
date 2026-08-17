"""Configuration read off disk rather than out of the store: hooks, MCP
servers, instruction files and capability detection.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from support import StoreTest, _build_store


class HookTest(StoreTest):
    """`cs hooks` reads configuration on disk, so every test writes some."""

    def _write(self, name: str, payload: dict) -> Path:
        folder = Path(os.environ["COPILOT_HOME"]) / "hooks"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(payload))
        return path

    def _script(self, name: str) -> Path:
        path = Path(self._tmp.name) / name
        path.write_text("#!/bin/sh\n")
        return path

    def test_no_hooks_says_where_it_looked(self):
        """An empty answer is only useful with the search path beside it."""
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("No hook is configured", out)
        self.assertIn("hooks", out)
        self.assertIn(os.environ["COPILOT_HOME"], out)

    def test_both_declaration_shapes_are_read(self):
        """A flat list, and commands grouped under a matcher — both are real."""
        self._write("flat.json", {"hooks": {
            "sessionStart": [{"type": "command", "command": "echo started"}]}})
        self._write("grouped.json", {"hooks": {"preToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo guarding"}]}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("echo started", out)
        self.assertIn("echo guarding", out)
        self.assertIn("Bash", out)

    def test_events_are_listed_in_lifecycle_order(self):
        """Declared last, run first: the report follows the session, not the file."""
        self._write("h.json", {"hooks": {
            "agentStop": [{"type": "command", "command": "echo done"}],
            "sessionStart": [{"type": "command", "command": "echo begin"}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertLess(out.index("echo begin"), out.index("echo done"))

    def test_one_event_written_two_ways_is_one_event(self):
        """`PreToolUse` and `preToolUse` are the same point in the lifecycle."""
        self._write("a.json", {"hooks": {
            "PreToolUse": [{"type": "command", "command": "echo upper"}]}})
        self._write("b.json", {"hooks": {
            "preToolUse": [{"type": "command", "command": "echo lower"}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("When they run · 1", out)

    def test_a_missing_script_is_called_out(self):
        """A hook pointing at a deleted script fails at run time, silently."""
        self._write("h.json", {"hooks": {"sessionStart": [
            {"type": "command", "command": "/nowhere/at/all/guard.sh --strict"}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("Scripts that are gone", out)
        self.assertIn("/nowhere/at/all/guard.sh", out)

    def test_the_script_is_judged_not_the_interpreter(self):
        """`/usr/bin/env python3 <script>` — the interpreter is never missing."""
        self._write("h.json", {"hooks": {"sessionStart": [
            {"command": "/usr/bin/env python3 /nowhere/guard.py"}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("/nowhere/guard.py", out)

    def test_a_script_that_exists_is_not_called_out(self):
        script = self._script("present.sh")
        self._write("h.json", {"hooks": {"sessionStart": [
            {"type": "command", "command": f"{script} --once"}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertNotIn("Scripts that are gone", out)

    def test_a_command_with_no_path_is_not_judged(self):
        """`npx thing` may or may not resolve; guessing would be worse."""
        self._write("h.json", {"hooks": {"sessionStart": [
            {"type": "command", "command": "npx cc-safety-net hook"}]}})
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertNotIn("Scripts that are gone", out)

    def test_a_broken_hook_file_is_reported_not_skipped(self):
        """Invalid JSON means the hooks in it never run — silence is wrong."""
        folder = Path(os.environ["COPILOT_HOME"]) / "hooks"
        folder.mkdir(exist_ok=True)
        (folder / "broken.json").write_text('{"hooks": {,}')
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("Not loaded", out)
        self.assertIn("broken.json", out)

    def test_a_parked_hook_directory_is_reported(self):
        """'hooks.off' is how people disable the lot; it explains an empty report."""
        (Path(os.environ["COPILOT_HOME"]) / "hooks.off").mkdir()
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertIn("Switched off", out)
        self.assertIn("hooks.off", out)

    def test_unrelated_backups_are_not_called_hooks(self):
        """settings.json.bak is not a parked hook file, and saying so is noise."""
        (Path(os.environ["COPILOT_HOME"]) / "settings.json.bak").write_text("{}")
        code, out = self._run("hooks")
        self.assertEqual(code, 0)
        self.assertNotIn("Switched off", out)

    def test_a_secret_in_a_hook_command_is_masked(self):
        """Hook commands are shell lines, and shell lines carry exported tokens."""
        secret = "ghp_" + "A" * 36
        self._write("h.json", {"hooks": {"sessionStart": [
            {"type": "command", "command": f"deploy --token={secret}"}]}})
        for args in (("hooks",), ("hooks", "sessionStart")):
            with self.subTest(args=args):
                code, out = self._run(*args)
                self.assertEqual(code, 0)
                self.assertNotIn(secret, out)

    def test_hook_drill_down_strips_terminal_sequences_and_newlines(self):
        poison = "\x1b]0;PWNED\x07"
        self._write("h.json", {"hooks": {"sessionStart": [{
            "type": "command",
            "matcher": f"Bash\nforged{poison}",
            "command": f"/missing/run.sh\nforged{poison}",
            "timeoutSec": f"30\nforged{poison}",
        }]}})
        code, out = self._run("hooks", "sessionStart")
        self.assertEqual(code, 0)
        self.assertNotIn("PWNED", out)
        self.assertNotIn("\x1b]", out)
        self.assertNotIn("\nforged", out)

    def test_drill_down_shows_the_command_in_full(self):
        """The table abbreviates paths; the drill-down is where they are whole."""
        self._write("h.json", {"hooks": {"sessionStart": [
            {"type": "command",
             "command": "/very/long/path/that/keeps/going/on/and/on/run.sh"}]}})
        code, out = self._run("hooks", "sessionStart")
        self.assertEqual(code, 0)
        self.assertIn("/very/long/path/that/keeps/going/on/and/on/run.sh", out)

    def test_drill_down_on_an_unknown_event_lists_the_real_ones(self):
        self._write("h.json", {"hooks": {"sessionStart": [
            {"type": "command", "command": "echo hi"}]}})
        code, out = self._run("hooks", "nonsense")
        self.assertEqual(code, 0)
        self.assertIn("No hook runs on 'nonsense'", out)
        self.assertIn("sessionStart", out)

    def test_an_unknown_sort_column_lists_the_real_ones(self):
        code, out = self._run("hooks", "--sort", "nope")
        self.assertEqual(code, 1)

    def test_sorting_by_source_groups_the_files(self):
        self._write("aaa.json", {"hooks": {
            "agentStop": [{"type": "command", "command": "echo from-aaa"}]}})
        self._write("zzz.json", {"hooks": {
            "sessionStart": [{"type": "command", "command": "echo from-zzz"}]}})
        code, out = self._run("hooks", "--sort", "source")
        self.assertEqual(code, 0)
        self.assertLess(out.index("echo from-aaa"), out.index("echo from-zzz"))


class McpTest(StoreTest):
    """`cs mcp` reads configuration on disk, so every test writes some."""

    def _write(self, servers: dict, name: str = "mcp-config.json") -> Path:
        path = Path(os.environ["COPILOT_HOME"]) / name
        path.write_text(json.dumps({"mcpServers": servers}))
        return path

    def test_no_servers_says_where_it_looked(self):
        """An empty answer is only useful with the search path beside it."""
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("No MCP server is configured", out)
        self.assertIn(os.environ["COPILOT_HOME"], out)

    def test_local_and_remote_are_told_apart(self):
        """Whether the conversation leaves the machine is the whole point."""
        self._write({
            "snyk": {"type": "local", "command": "snyk", "args": ["mcp"]},
            "atlassian": {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp"},
        })
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("1 local · 1 remote", out)
        self.assertIn("snyk mcp", out)
        # The table abbreviates a long endpoint; the drill-down is where it
        # is whole, the same trade `cs hooks` makes with a command.
        code, out = self._run("mcp", "atlassian")
        self.assertEqual(code, 0)
        self.assertIn("https://mcp.atlassian.com/v1/mcp", out)

    def test_the_transport_is_inferred_when_it_is_not_stated(self):
        """VS Code writes no 'type'; the shape still says which it is."""
        self._write({"a": {"command": "run-me"}, "b": {"url": "https://x/mcp"}})
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("1 local · 1 remote", out)

    def test_the_vscode_key_is_read_too(self):
        """`servers` and `mcpServers` are one file format written two ways."""
        path = Path(os.environ["COPILOT_HOME"]) / "mcp-config.json"
        path.write_text(json.dumps({"servers": {"figma": {"url": "https://f/mcp"}}}))
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("figma", out)

    def test_a_wildcard_tool_list_is_called_out(self):
        """'*' means whatever the server adds next is enabled, undecided."""
        self._write({"wide": {"url": "https://x/mcp", "tools": ["*"]},
                     "narrow": {"url": "https://y/mcp", "tools": ["search"]}})
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("1 of 2 expose everything", out)

    def test_a_token_in_the_url_never_reaches_the_screen(self):
        """An MCP endpoint is routinely handed its credential as a query."""
        secret = "ghp_" + "A" * 36
        self._write({"leaky": {"url": f"https://mcp.example.com/v1?token={secret}"}})
        for args in (("mcp",), ("mcp", "leaky")):
            with self.subTest(args=args):
                code, out = self._run(*args)
                self.assertEqual(code, 0)
                self.assertNotIn(secret, out)
                self.assertNotIn("token=", out)
        # The endpoint still identifies itself — the query is what goes.
        self.assertIn("https://mcp.example.com/v1", self._run("mcp", "leaky")[1])

    def test_a_literal_credential_in_the_config_is_named_not_printed(self):
        """The key is enough to go and fix it; the value is the thing to lose."""
        secret = "ghp_" + "B" * 36
        self._write({"gh": {"command": "server", "env": {"GITHUB_TOKEN": secret}},
                     "ok": {"command": "server", "env": {"TOKEN": "${FROM_ENV}"}}})
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("Credentials written into the config", out)
        self.assertIn("env.GITHUB_TOKEN", out)
        self.assertNotIn(secret, out)
        self.assertNotIn("env.TOKEN", out)

    def test_a_missing_command_is_reported(self):
        """Copilot will still try to start it, and the spawn will fail."""
        self._write({"ghost": {"command": "/nowhere/at/all/server"}})
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("Commands that are gone", out)
        self.assertIn("ghost", out)

    def test_a_broken_config_is_reported_rather_than_ignored(self):
        """A trailing comma starts no server at all — silence is the wrong report."""
        (Path(os.environ["COPILOT_HOME"]) / "mcp-config.json").write_text(
            '{"mcpServers": {,}')
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("Not loaded", out)
        self.assertIn("mcp-config.json", out)

    def test_a_parked_config_is_reported(self):
        """A .bak is how people switch a server off; it explains an empty report."""
        (Path(os.environ["COPILOT_HOME"]) / "mcp-config.json.bak").write_text("{}")
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("Switched off", out)
        self.assertIn("mcp-config.json.bak", out)

    def test_config_text_cannot_forge_a_row_or_drive_the_terminal(self):
        """The file ships in whatever repository you cd'd into."""
        poison = "\x1b]0;PWNED\x07"
        self._write({f"evil\nforged{poison}": {
            "type": "local", "command": f"run\nforged{poison}"}})
        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertNotIn("PWNED", out)
        self.assertNotIn("\x1b]", out)
        self.assertNotIn("\nforged", out)

    def test_usage_is_counted_from_qualified_mentions_only(self):
        """A bare 'notion' is a word; mcp__notion__search is a call."""
        import sqlite3 as sq

        self._write({"notion": {"url": "https://n/mcp"},
                     "unused": {"url": "https://u/mcp"}})
        conn = sq.connect(Path(os.environ["COPILOT_HOME"]) / "session-store.db")
        conn.execute(
            "INSERT INTO turns (session_id, turn_index, user_message, "
            "assistant_response, timestamp) VALUES ('sess-alpha',50,?,?,'t')",
            ("check unused for me", "calling mcp__notion__search now"),
        )
        conn.commit()
        conn.close()

        code, out = self._run("mcp")
        self.assertEqual(code, 0)
        self.assertIn("Never referenced", out)
        self.assertIn("unused", out)
        code, out = self._run("mcp", "notion")
        self.assertEqual(code, 0)
        self.assertIn("1 sessions", out)

    def test_drill_down_on_an_unknown_server_lists_the_real_ones(self):
        self._write({"snyk": {"command": "snyk"}})
        code, out = self._run("mcp", "nonsense")
        self.assertEqual(code, 0)
        self.assertIn("No MCP server named 'nonsense'", out)
        self.assertIn("snyk", out)

    def test_an_unknown_sort_column_lists_the_real_ones(self):
        code, out = self._run("mcp", "--sort", "nope")
        self.assertEqual(code, 1)

    def test_the_workspace_wins_over_the_personal_config(self):
        """One name declared twice is one server, resolved the way Copilot does."""
        import cs.mcp as mcp_module

        self._write({"shared": {"command": "personal-one"}})
        workspace = Path(self._tmp.name) / "repo"
        (workspace / ".copilot").mkdir(parents=True)
        (workspace / ".copilot" / "mcp-config.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "workspace-one"}}}))
        here = os.getcwd()
        os.chdir(workspace)
        try:
            servers, _ = mcp_module.load()
        finally:
            os.chdir(here)
        self.assertEqual([s["name"] for s in servers], ["shared"])
        self.assertEqual(servers[0]["scope"], "personal")


class ContextTest(StoreTest):
    """`cs context` reads disk, so these build a small project on disk."""

    def setUp(self):
        super().setUp()
        self.project = Path(self._tmp.name) / "project"
        (self.project / ".github").mkdir(parents=True)

    def _audit(self):
        from cs import context

        found = context.audit(self.project)
        return found, {gap[1]: gap for gap in context.gaps(found)}

    def test_a_project_with_no_instructions_is_the_headline_gap(self):
        _found, gaps = self._audit()
        wanted = "This project tells the agent nothing about itself"
        self.assertIn(wanted, gaps)
        self.assertEqual(gaps[wanted][0], "high")

    def test_an_instruction_file_clears_that_gap(self):
        (self.project / ".github" / "copilot-instructions.md").write_text(
            "# Conventions\nRun the tests with pytest.\n"
        )
        found, gaps = self._audit()
        self.assertNotIn("This project tells the agent nothing about itself", gaps)
        self.assertEqual([i.kind for i in found["items"]], ["instructions"])

    def test_an_oversized_instruction_file_is_reported_with_the_limit(self):
        from cs import context

        (self.project / "AGENTS.md").write_text(
            "# Rules\n" + ("x" * (context.INSTRUCTION_LIMIT + 100))
        )
        _found, gaps = self._audit()
        oversize = [key for key in gaps if "characters" in key]
        self.assertEqual(len(oversize), 1)
        self.assertIn("truncates", gaps[oversize[0]][2])

    def test_a_long_unsectioned_file_is_reported(self):
        (self.project / "AGENTS.md").write_text("just prose\n" * 80)
        _found, gaps = self._audit()
        self.assertTrue([key for key in gaps if "no headings" in key])

    def test_a_documented_skill_counts_once_not_per_page(self):
        skills = self.project / ".github" / "skills" / "deploy"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("# Deploy\n")
        (skills / "REFERENCE.md").write_text("# Long reference\n")
        found, _gaps = self._audit()
        self.assertEqual([i.kind for i in found["items"] if i.scope == "project"],
                         ["skills"])

    def test_the_report_runs_and_names_both_scopes(self):
        code, out = self._run("context")
        self.assertEqual(code, 0)
        self.assertIn("project", out)
        self.assertIn("personal", out)

    def test_context_takes_no_arguments(self):
        code, _out = self._run("context", "30")
        self.assertEqual(code, 1)

    def test_a_directory_it_may_not_read_is_skipped_not_fatal(self):
        """An audit of what is on disk must survive what it cannot open."""
        from cs import context

        shut = self.project / ".github" / "agents"
        shut.mkdir()
        (shut / "one.md").write_text("# An agent\n")
        shut.chmod(0o000)
        try:
            found = context.audit(self.project)
        finally:
            shut.chmod(0o755)
        self.assertEqual([i.kind for i in found["items"]], [])


class InstructionsTest(StoreTest):
    """`cs instructions` reads disk too, so each test builds a project."""

    def setUp(self):
        super().setUp()
        self.project = Path(self._tmp.name) / "project"
        (self.project / ".github").mkdir(parents=True)
        self._was = os.getcwd()
        os.chdir(self.project)

    def tearDown(self):
        os.chdir(self._was)
        super().tearDown()

    def test_an_empty_report_names_where_it_looked(self):
        """The one screen where the search path is the whole answer."""
        code, out = self._run("instructions")
        self.assertEqual(code, 0)
        self.assertIn("None found", out)
        self.assertIn("AGENTS.md", out)
        self.assertIn(os.environ["COPILOT_HOME"], out)

    def test_a_file_is_listed_with_its_scope_and_size(self):
        (self.project / "AGENTS.md").write_text("# Rules\nRun the tests.\n")
        code, out = self._run("instructions")
        self.assertEqual(code, 0)
        self.assertIn("AGENTS.md", out)
        self.assertIn("project", out)
        self.assertIn("personal", out)

    def test_an_oversized_file_says_how_much_is_not_read(self):
        """The number that makes the report worth opening."""
        from cs import context

        over = 250
        (self.project / "AGENTS.md").write_text(
            "# Rules\n" + "x" * (context.INSTRUCTION_LIMIT + over - len("# Rules\n"))
        )
        code, out = self._run("instructions")
        self.assertEqual(code, 0)
        self.assertIn(f"last {over:,} are past the limit", out)

    def test_a_long_unsectioned_file_is_called_out(self):
        (self.project / "AGENTS.md").write_text("just prose\n" * 80)
        code, out = self._run("instructions")
        self.assertEqual(code, 0)
        self.assertIn("no headings", out)

    def test_the_search_path_matches_what_is_actually_scanned(self):
        """An empty report that names a path the scan never walks is a lie."""
        from cs import context

        (self.project / ".github" / "copilot-instructions.md").write_text("# Hi\n")
        found = context.audit(self.project)
        listed = set(context.instruction_paths(self.project))
        for item in found["items"]:
            if item.kind == "instructions" and item.scope == "project":
                self.assertIn(item.path, listed)

    def test_instructions_takes_no_arguments(self):
        code, _out = self._run("instructions", "30")
        self.assertEqual(code, 1)


class CapabilityTest(unittest.TestCase):
    """cs runs against whatever schema the installed Copilot happens to write.

    Every optional table and column is therefore an answer cs may not be able
    to give — never a reason to fail. These build stores that are missing
    things on purpose and check that the commands still run.
    """

    ALL = ("recent", "all", "repos", "stats", "timeline", "cost", "agents",
           "yolo", "handoff", "audit", "skills", "profiles", "hooks",
           "coach", "rhythm", "context")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.environ["COPILOT_HOME"] = str(self.base)
        os.environ["TERM"] = "dumb"

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("COPILOT_HOME", None)

    def _store(self, script: str, rows: bool = True) -> None:
        conn = sqlite3.connect(self.base / "session-store.db")
        conn.executescript(script)
        if rows:
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at) VALUES "
                "('11111111-2222-3333-4444-555555555555',"
                "datetime('now'), datetime('now'))"
            )
            conn.execute(
                "INSERT INTO turns (session_id, turn_index, user_message,"
                " assistant_response) VALUES "
                "('11111111-2222-3333-4444-555555555555', 0, 'hi', 'there')"
            )
        conn.commit()
        conn.close()

    def _run(self, *args: str) -> tuple[int, str, str]:
        from cs.cli import main

        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(args))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    BARE = """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT,
                               updated_at TEXT);
        CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT,
                            turn_index INTEGER, user_message TEXT,
                            assistant_response TEXT);
    """

    def test_a_store_with_only_the_essentials_answers_every_command(self):
        """No repository, no cost, no files, no checkpoints — and no traceback."""
        self._store(self.BARE)
        for command in self.ALL:
            with self.subTest(command=command):
                code, _out, err = self._run(command)
                self.assertNotIn("Traceback", err)
                self.assertIn(code, (0, 1))

    def test_a_store_with_only_the_essentials_still_lists_its_sessions(self):
        """A missing column costs one fact, not the whole listing."""
        self._store(self.BARE)
        code, out, _err = self._run("all")
        self.assertEqual(code, 0)
        self.assertIn("1 total", out)
        # No summary column in this store, so the row says so rather than
        # the listing dropping the session it could not fully describe.
        self.assertIn("(untitled)", out)

    def test_a_file_that_is_not_a_session_store_says_so(self):
        """Pointing COPILOT_HOME at the wrong directory is the common mistake."""
        conn = sqlite3.connect(self.base / "session-store.db")
        conn.executescript("CREATE TABLE unrelated (x TEXT);")
        conn.commit()
        conn.close()
        code, _out, err = self._run("recent")
        self.assertEqual(code, 1)
        self.assertIn("not a Copilot session store", err)
        self.assertIn("the sessions table", err)
        self.assertNotIn("Traceback", err)

    def test_a_store_missing_an_essential_column_says_which(self):
        self._store(
            """
            CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT);
            CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT,
                                turn_index INTEGER, user_message TEXT,
                                assistant_response TEXT);
            """,
            rows=False,
        )
        code, _out, err = self._run("recent")
        self.assertEqual(code, 1)
        self.assertIn("sessions.updated_at", err)

    def test_capabilities_reports_what_a_full_store_can_do(self):
        from cs import db

        _build_store(self.base)
        conn = db.connect()
        try:
            able = db.capabilities(conn)
            self.assertEqual(db.missing_essentials(conn), [])
            self.assertTrue(all(able.values()), able)
            self.assertEqual(set(able), set(db.OPTIONAL))
        finally:
            conn.close()

    def test_capabilities_reports_what_a_bare_store_cannot(self):
        from cs import db

        self._store(self.BARE)
        conn = db.connect()
        try:
            able = db.capabilities(conn)
            self.assertFalse(any(able.values()), able)
        finally:
            conn.close()

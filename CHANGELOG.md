# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The transcript (`cs read`, and `t` from any listing) is set differently.
  Each turn drew three full-width rules — its own, and one per speaker — all
  the same weight, plus a lone line of grey underneath carrying the size. A
  turn now opens on one rule that carries the ask at one end and the time and
  size at the other, and each speaker's words run beside a coloured rail so
  who-said-what survives scrolling. Section headings inside a reply are set in
  the accent rather than in bold, which a reply full of `**bold**` already
  uses, and a blockquote is marked rather than left with its `>`.
- `ui.rule()` takes a `note` that rides its right-hand end, and `ui.spine()`
  is new — both are shared primitives, so any view can be set this way.
- The turn index has gone from `cs show`. It was a third rendering of the same
  list: the page already prints the first and last request, `--asks` prints
  every one of them numbered, and `cs read` is the conversation itself. On a
  hundred-turn session the summary closed with a hundred lines of truncated
  prompt. `--asks` now carries the `--turn N` command the index used to.
- `cs show` and `cs brief` no longer read 2,000 characters of every prompt in
  the session in order to count them.

### Fixed

- A `session-store.db` that is not a database — a truncated download, a file
  restored from the wrong backup, a store mid-write — produced a raw
  `sqlite3.DatabaseError` traceback instead of the sentence every other bad
  `COPILOT_HOME` gets.
- The `credentials masked · CS_REDACT=0 …` line in every session footer was
  the one fixed-width string on an otherwise width-aware page, and ran off any
  window under about fifty columns. It shortens now.
- The speaker label put the name in a different column depending on
  `CS_GLYPHS`, because the emoji marks are two cells wide and their plain
  forms are one.
- Installing on Python 3.9 no longer gets as far as running. `pip` honours
  `requires-python`, but `install.sh` and `bin/cs` bypass pip, and every module
  imports `annotations` from `__future__` — so an old interpreter used to start
  cleanly and fail later inside a view. `cs/__init__.py`, which every entry
  point imports, now refuses up front and names Homebrew as the way out.
- `install.sh` checks the Python version before creating the symlink rather
  than leaving a link that cannot work.
- `install.sh` warns when another `cs` earlier on `PATH` will keep answering,
  which previously made a successful install look like it had done nothing.

### Changed

- Package metadata gained the supported Python versions, operating systems and
  the issue, source and changelog links.

## [1.0.0] — 2026-08-17

First public release. `cs` reads the local GitHub Copilot CLI session store,
read-only, and reports on it in the terminal. Python 3.10+, standard library
only.

### Added

**Listing and history**

- `cs` / `cs home` — landing screen, every view one keypress away.
- `cs recent`, `cs all`, `cs repos` — sessions by recency, including quiet and
  automated ones, or grouped by repository.
- `cs timeline` — working days, with sessions, turns and spend per day.

**Cost and efficiency**

- `cs cost` — AI spend broken down by model, repository and day.
- `cs efficiency` — whether it had to cost that: cache hit rate, rate
  multiplier, first-token latency and reasoning share.
- `cs stats` — the output ledger: commits, PRs, files, cost and delegation.

**What ran, and on whose behalf**

- `cs agents` — delegation split across you, the main agent and sub-agents.
- `cs skills`, `cs profiles` — what is configured on disk versus what sessions
  actually referenced and loaded.
- `cs instructions` — the instruction files every session in a repository
  starts with, in both scopes, naming the ones past Copilot's
  4,000-character truncation limit and counting the shortfall.
- `cs mcp` — MCP servers wired up, local and remote, and what they may call.
- `cs hooks` — commands Copilot runs on the session lifecycle, flagging any
  that point at a script that no longer exists.

**Governance**

- `cs yolo` — which sessions ran unattended, and the evidence for saying so.
- `cs handoff` — sessions that wrote or picked up a handoff, and the chain a
  session belongs to.
- `cs audit` — credential-shaped text found in sessions, reported by name and
  prefix only, never by value.

**Finding and resuming**

- `cs search` — full-text across summaries, repositories, both sides of every
  turn and session checkpoints, with `AND` / `OR` / `NEAR` and phrases.
- `cs show` — one session whole: what is open, what was asked and done, then
  spend, files, skills, agents, risk and turns. `--short` (aliased as
  `cs brief`) stops after the story; `--asks` lists every request in order.
- `cs read` — the conversation itself, both sides, in full. `--turn N` prints
  one turn. `cs transcript` is an alias.
- `cs files` — sessions that touched a path, with globs and partials.
- `cs resume` — changes to the session directory and runs `copilot --resume`.

**Getting it out**

- `--json` and `--csv` on the main views, `cs export` for one session as
  Markdown, and `cs completion` for bash, zsh and fish. Credentials are masked
  on the way out exactly as they are on screen — a file is more exposed than a
  screen, not less.
- `cs <view> --why` — how to read any view, section by section.

### Security

- The store is opened `mode=ro` through a SQLite URI. There is no write path.
- No network access and no runtime dependencies.
- Session content is treated as untrusted input: credential-shaped text is
  masked at the render edge in `cs/redact.py`, and terminal control sequences
  and row-breaking characters are stripped before anything is drawn.

[1.0.0]: https://github.com/ssmule/copilot-sessions/releases/tag/v1.0.0

# Agent Instructions

`cs` reads the local Copilot CLI session store and reports on it. Python 3.10+,
standard library only, and the store is opened read-only.

> **This repository is published as open source.** Everything committed here is
> public and permanent: treat every file as something a stranger will read,
> and every commit as something that cannot be unpublished. In practice that
> means no personal paths, machine names, tokens, employer detail or real
> session content in tracked files; no private working notes outside
> `docs/HANDOVER.md`; and no tool, hook or config that only works on the
> maintainer's machine — a fresh clone must be able to run everything the
> repository tells it to run.

Read [CONTRIBUTING.md](CONTRIBUTING.md) first — it is short, and it is the
actual contract. This file only adds the things that bite agents specifically.

## Before you commit, run both

CI runs exactly these two commands, in this order:

```bash
ruff check cs tests
python -m unittest discover -s tests
```

Run both locally too. Two traps live here:

- **The tests are `unittest`, not `pytest`.** `pytest` may appear to work and
  will collect the wrong things.
- **`ruff` is not a dependency and may not be installed.** Install the pinned
  version somewhere outside the repo — never add it to `pyproject.toml`:

  ```bash
  python3 -m venv /tmp/lintenv && /tmp/lintenv/bin/pip install 'ruff==0.15.22'
  /tmp/lintenv/bin/ruff check cs tests
  ```

  This is not a footnote. Lint once ran ahead of the tests as a plain step, so
  four trivial lint errors skipped the test suite entirely and nobody noticed
  for ten commits. The workflow now runs the tests with `if: always()` so
  neither step can hide the other, but the cheapest fix is still to lint before
  you push.

## Task tracking

The issue tracker is **GitHub Issues**. Use whatever scratch list your harness
gives you for in-flight work; nothing in this repo needs to know about it.

If you find local tooling state on disk (`.beads/`, `.agents/skills/beads/`),
it is a maintainer's personal workflow, deliberately untracked. Do not commit
it and do not require it.

## Rules that will get a change rejected

These are enforced by review, and mostly by tests:

- **Never open the store for writing.** `mode=ro` is the only path in. There is
  no write feature waiting to be added.
- **Never print stored text directly.** Session content is untrusted input. It
  goes through `redact.py` and the sanitising path in `ui.py` first — masking
  applies to files and pipes exactly as it does to the screen.
- **No runtime dependencies.** Standard library only. It is what makes `cs`
  installable on a locked-down machine.
- **Inferences carry their evidence.** If a view claims a session ran
  unattended, it must also show what made it think so.

## Things that have actually gone wrong here

- **The store is live.** It changes while you query it. Two numbers captured a
  minute apart are not comparable, and a "discrepancy" is usually just time
  passing.
- **Not every glyph renders.** Icons and box characters silently fall back to
  blank in some fonts and terminals. Check a change in a real terminal rather
  than trusting the source; the same applies to the deck in `docs/deck/`, where
  a block character used as a cursor rendered as nothing at all.
- **Widths are load-bearing.** A new table has to hold its shape at 40, 60, 80,
  100 and 140 columns. There is a shared width test that will check this for
  you — use it.
- **Preview views without a pager**, or the command will hang waiting for you:

  ```bash
  CS_PAGER=cat COLUMNS=92 python3 -m cs <command>
  ```

## Git

Do not commit or push unless you were asked to. When you finish, report what
changed, what you ran to verify it, and the commands you would run next.

## Non-interactive shell commands

`cp`, `mv` and `rm` are aliased to `-i` on some systems, which will hang an
agent forever on a y/n prompt. Always force:

```bash
cp -f source dest
mv -f source dest
rm -f file
rm -rf directory
```

Likewise: `ssh`/`scp` with `-o BatchMode=yes`, `apt-get` with `-y`, and
`brew` with `HOMEBREW_NO_AUTO_UPDATE=1`.

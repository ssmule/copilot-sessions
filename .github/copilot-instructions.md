# GitHub Copilot Instructions

`cs` reads the local Copilot CLI session store and reports on it. Python 3.10+,
standard library only, store opened read-only.

**[AGENTS.md](../AGENTS.md) is the full brief. [CONTRIBUTING.md](../CONTRIBUTING.md)
is the contract.** Read those; this file is only a pointer, kept short so the
two never drift apart.

The short version:

- **This repo is public.** Nothing machine-specific, personal or secret goes in
  a tracked file, and a fresh clone must be able to run whatever the repo tells
  it to run.
- Run `ruff check cs tests` **and** `python -m unittest discover -s tests`
  before you commit. The tests are `unittest`, not `pytest`.
- The store is read-only, and stored text is untrusted — it goes through
  `redact.py` before it is ever printed.
- No runtime dependencies. Standard library only.
- Issues live in GitHub Issues.
- Do not commit or push unless you were asked to.

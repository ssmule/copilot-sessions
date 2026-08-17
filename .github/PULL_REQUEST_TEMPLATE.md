<!--
Thanks for the PR. The checklist is short on purpose — CONTRIBUTING.md has the
reasoning behind each line if you want it.
-->

## What this changes, and why

<!--
The *why*, not the *what* — the diff already says what. If it fixes an issue,
link it: "Fixes #123".
-->

## Before / after

<!--
For anything that changes a view, paste the terminal output both ways. Text,
not just a screenshot: it is searchable and it diffs. Screenshots are welcome
in addition for UI work.

Credentials are masked on the way out, so pasted output is safe to include.
-->

```text
before:


after:

```

## Checks

- [ ] `ruff check cs tests` passes
- [ ] `python -m unittest discover -s tests` passes (it is `unittest`, not `pytest`)
- [ ] New behaviour with a branch, loop, parser or credential path has a test
- [ ] Any new table or report holds its shape at 40, 60, 80, 100 and 140 columns
- [ ] No new runtime dependency — standard library only
- [ ] Nothing opens the session store for writing
- [ ] Stored text is printed through the masking path, never directly

<!--
A new credential rule in redact.py needs two cases: one synthetic string that
must mask, and one near-miss that must not. Mark the lines `# gitleaks:allow`.

Security problems do not belong in a PR description — see SECURITY.md.
-->

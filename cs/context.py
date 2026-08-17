"""Context audit — what this repository gives the agent before you type.

Every other view in cs reads the record of work already done. This one reads
the setup that work will start from: the instruction files, prompts, skills,
agent profiles and hooks that are loaded before your first word.

It is the cheapest thing to get right and the easiest to leave rotting. An
instruction file grows past the point where it is read in full; a skills
directory has one file in it from six months ago; a repository has nothing at
all and every session starts by explaining the same conventions again.

Two scopes are checked: **personal** (`$COPILOT_HOME`, which follows you
between repositories) and **project** (the working directory, which is the
one your colleagues also get). Nothing is written or fixed — this reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import hooks

# Copilot's code-review path silently truncates an instruction file past this,
# and a model that reads two-thirds of your conventions is worse than one that
# reads a short file completely.
INSTRUCTION_LIMIT = 4_000

# Where each kind of context lives. Copilot's own paths and the AGENTS.md
# convention both get a look because a real repository usually carries more
# than one, and a file the agent reads is a file this should report.
# (kind, path relative to the scope root, what counts as a file, recurse).
# A skill is one directory or one file, never every markdown page inside it —
# counting the pages would report a well-documented skill as forty skills.
_PROJECT = (
    ("instructions", ".github/copilot-instructions.md", None, False),
    ("instructions", "AGENTS.md", None, False),
    ("instructions", ".github/instructions", r"\.instructions\.md$", False),
    ("prompts", ".github/prompts", r"\.prompt\.md$", True),
    ("prompts", "prompts", r"\.prompt\.md$", False),
    ("skills", ".github/skills", r"(?:^|/)SKILL\.md$", True),
    ("skills", ".copilot/skills", r"\.md$", False),
    ("agents", ".github/agents", r"\.md$", False),
    ("agents", ".copilot/agents", r"\.md$", False),
)

_PERSONAL = (
    ("instructions", "copilot-instructions.md", None, False),
    ("instructions", "AGENTS.md", None, False),
    ("skills", "skills", r"\.md$|^[^.]+$", False),
    ("agents", "agents", r"\.md$", False),
)

_HEADING = re.compile(r"^#{1,6}\s", re.M)


@dataclass
class Item:
    """One context file, and what is true about it."""

    kind: str
    scope: str
    path: Path
    label: str
    chars: int
    lines: int
    headings: int

    @property
    def oversized(self) -> bool:
        return self.kind == "instructions" and self.chars > INSTRUCTION_LIMIT

    @property
    def unsectioned(self) -> bool:
        """A long file with no headings is one the model reads as a wall."""
        return self.kind == "instructions" and self.lines > 60 and not self.headings


def _measure(path: Path, kind: str, scope: str, root: Path) -> Item | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        label = str(path.relative_to(root))
    except ValueError:
        label = path.name
    return Item(kind, scope, path, label, len(text),
                text.count("\n") + 1, len(_HEADING.findall(text)))


def _is_file(path: Path) -> bool:
    """`Path.is_file()`, but False rather than a traceback.

    `is_file()` only swallows "not there" errors; a directory the user cannot
    read raises `PermissionError` instead. One such directory anywhere under
    the scope roots would take the whole audit down, and a context report is
    the last thing that should insist on reading everything to say anything.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def _entries(target: Path, recurse: bool) -> list[Path]:
    """What is in a directory, or nothing if it cannot be listed."""
    try:
        return sorted(target.rglob("*") if recurse else target.iterdir())
    except OSError:
        return []


def _collect(root: Path, scope: str, patterns) -> list[Item]:
    found: list[Item] = []
    for kind, relative, glob, recurse in patterns:
        target = root / relative
        if glob is None:
            if _is_file(target) and (item := _measure(target, kind, scope, root)):
                found.append(item)
            continue
        if not target.is_dir():
            continue
        matcher = re.compile(glob, re.I)
        entries = _entries(target, recurse)
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and not recurse:
                # A skill kept as a directory: measure the file that defines
                # it, so the count is skills rather than pages.
                entry = next(
                    (entry / name for name in ("SKILL.md", "README.md")
                     if _is_file(entry / name)), entry
                )
                if entry.is_dir():
                    continue
            elif not _is_file(entry) or not matcher.search(entry.as_posix()):
                continue
            if item := _measure(entry, kind, scope, root):
                found.append(item)
    return found


def audit(project: Path | None = None) -> dict:
    """Everything on disk that will be loaded before your first prompt."""
    root = (project or Path.cwd()).resolve()
    personal_root = hooks.home()
    items = (
        _collect(root, "project", _PROJECT)
        + _collect(personal_root, "personal", _PERSONAL)
    )
    # One file can be reached by two patterns (a repo whose cwd *is* the
    # Copilot home, say). Keyed by resolved path so it is counted once.
    unique = {item.path.resolve(): item for item in items}
    configured, problems = hooks.load()
    return {
        "root": root,
        "personal_root": personal_root,
        "items": sorted(unique.values(), key=lambda i: (i.scope, i.kind, i.label)),
        "hooks": configured,
        "hook_problems": problems,
    }


def instruction_paths(project: Path | None = None) -> list[Path]:
    """Every place an instruction file can live, project first then personal.

    Read out of the same tables the audit walks, so an empty report cannot
    name a search path the scan does not actually use.
    """
    root = (project or Path.cwd()).resolve()
    return [
        *(root / relative for kind, relative, _glob, _r in _PROJECT
          if kind == "instructions"),
        *(hooks.home() / relative for kind, relative, _glob, _r in _PERSONAL
          if kind == "instructions"),
    ]


def gaps(found: dict) -> list[tuple[str, str, str]]:
    """(severity, what, what to do) — only things that are actually wrong.

    Deliberately short. A checklist long enough to ignore is a checklist that
    gets ignored, so this reports the gaps that change what the agent sees on
    the very next session and nothing else.
    """
    items: list[Item] = found["items"]
    by = lambda kind, scope: [  # noqa: E731 - a filter, not a function
        i for i in items if i.kind == kind and i.scope == scope
    ]
    out: list[tuple[str, str, str]] = []

    if not by("instructions", "project"):
        out.append((
            "high",
            "This project tells the agent nothing about itself",
            "Add .github/copilot-instructions.md (or AGENTS.md) with the "
            "conventions you would otherwise repeat: how to run the tests, "
            "what not to touch, how commits are worded.",
        ))
    for item in items:
        if item.oversized:
            out.append((
                "medium",
                f"{item.scope} {item.label} is {item.chars:,} characters",
                f"Copilot truncates an instruction file past "
                f"{INSTRUCTION_LIMIT:,} characters, so the end of this one is "
                f"not being read. Move the scoped rules into "
                f".github/instructions/*.instructions.md, which load only "
                f"when they match.",
            ))
        if item.unsectioned:
            out.append((
                "low",
                f"{item.scope} {item.label} is {item.lines} lines with no "
                f"headings",
                "Add ## sections. A model skims structure the same way you "
                "do, and an unsectioned wall gets read as one topic.",
            ))
    if not by("skills", "project") and not by("skills", "personal"):
        out.append((
            "medium",
            "No skills anywhere",
            "A prompt you have typed three times is a skill. 'cs coach' finds "
            "the repeats; $COPILOT_HOME/skills is where they go.",
        ))
    if not found["hooks"]:
        out.append((
            "low",
            "No hooks configured",
            "Hooks run commands on the session lifecycle — a formatter after "
            "an edit, a guard before a shell command. 'cs hooks' shows where "
            "they are declared.",
        ))
    if found["hook_problems"]:
        out.append((
            "high",
            f"{len(found['hook_problems'])} hook files do not parse",
            "Invalid JSON means those hooks never run, silently. 'cs hooks' "
            "names the files.",
        ))
    return out

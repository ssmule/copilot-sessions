"""Hooks — the commands Copilot is configured to run around a session.

Skills and agent profiles are files a session may reference. Hooks are the
opposite: nothing in a session references them, because they are not asked
for. They fire on the lifecycle — before a tool call, after a prompt, when
the agent stops — and the session store records none of it.

So this module reads configuration, never history, and the report built on it
says so. What it can tell you is worth having anyway: what would run, in what
order, from which file, and whether the script a hook names is still on disk.

Nothing here executes a hook or writes to a hook file.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from . import redact

# Copilot's lifecycle, in the order the events actually occur. Sorting by this
# rather than alphabetically is the difference between a list of hooks and a
# picture of a session: `sessionStart` belongs at the top because it runs
# first, not because 'a' comes before 's'.
EVENTS: tuple[str, ...] = (
    "sessionStart",
    "userPromptSubmitted",
    "permissionRequest",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "notification",
    "errorOccurred",
    "preCompact",
    "agentStop",
    "sessionEnd",
)

_CANONICAL = {name.lower(): name for name in EVENTS}

# Names a hook file gets when someone parks it rather than deletes it. Worth
# reporting: "no hooks configured" and "your hooks are switched off" look
# identical from the inside, and only one of them is a surprise.
_PARKED = (".off", ".disabled", ".bak", ".sample", ".example")


def home() -> Path:
    """Copilot's own directory — the same one the session store lives in."""
    override = os.environ.get("COPILOT_HOME")
    return Path(override) if override else Path.home() / ".copilot"


def search_paths() -> list[tuple[Path, str]]:
    """Every place a hook can be declared: (path, scope).

    Personal first, then the workspace, which is the order Copilot itself
    reads them in — and the order a reader needs to answer "where did THAT
    come from".
    """
    workspace = Path.cwd() / ".copilot"
    return [
        (home() / "hooks", "personal"),
        (home() / "settings.json", "personal"),
        (workspace / "hooks", "workspace"),
        (workspace / "settings.json", "workspace"),
    ]


def _canonical(event: str) -> str:
    """`PreToolUse` and `preToolUse` are one event written two ways."""
    return _CANONICAL.get(event.lower(), event)


def order(event: str) -> tuple[int, str]:
    """Sort key putting known events in lifecycle order, unknown ones last."""
    canonical = _canonical(event)
    if canonical in EVENTS:
        return EVENTS.index(canonical), ""
    return len(EVENTS), canonical.lower()


def _target(command: str) -> tuple[Path | None, bool]:
    """The script a command runs, and whether it has gone missing.

    Only an explicit path is judged. `npx cc-safety-net` may or may not
    resolve depending on a registry and a cache, and guessing about that
    would be a worse answer than declining to give one.

    The *last* path wins, not the first: hooks are written
    `/usr/bin/env python3 /path/to/guard.py`, and the interpreter is always
    there while the script is the thing that goes missing.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    found: Path | None = None
    for token in tokens:
        cleaned = token.lstrip("(<>\"'")
        if not cleaned.startswith(("/", "./", "~/")):
            continue
        if cleaned.startswith("/dev/"):
            continue    # a redirect, not a program
        found = Path(cleaned).expanduser()
    return (found, not found.exists()) if found else (None, False)


def short(command: str, keep: int = 30) -> str:
    """A command with its long absolute paths abbreviated to the useful end.

    Hook commands are mostly interpreter-plus-script, and both are absolute:
    truncating from the right left every row reading `/Users/me/.pye…`, which
    is the one part of a hook that is the same as every other hook's. The
    identifying part is the file name, so that is what survives.
    """
    out = []
    for token in command.split(" "):
        path = token
        if path.startswith(str(Path.home())):
            path = "~" + path[len(str(Path.home())):]
        if len(path) > keep and "/" in path:
            head, _, tail = path.rpartition("/")
            parent = head.rpartition("/")[2]
            path = f"…/{parent}/{tail}" if parent else f"…/{tail}"
        out.append(path)
    return " ".join(out)


def _entries(event: str, declared: object, source: Path, scope: str) -> list[dict]:
    """Flatten one event's declaration, in either shape Copilot accepts.

    A flat list of commands, or commands grouped under a `matcher` that says
    which tools they apply to. Both appear in the wild, sometimes in the same
    directory, so both are read into one shape here.
    """
    out: list[dict] = []
    if not isinstance(declared, list):
        return out
    for item in declared:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("hooks"), list):
            matcher = str(item.get("matcher") or "")
            for inner in item["hooks"]:
                if isinstance(inner, dict):
                    out.extend(_one(event, inner, matcher, source, scope))
            continue
        out.extend(_one(event, item, str(item.get("matcher") or ""), source, scope))
    return out


def _one(event: str, item: dict, matcher: str, source: Path, scope: str) -> list[dict]:
    command = redact.one_line(str(item.get("command") or "").strip())
    if not command:
        return []
    target, missing = _target(command)
    # These strings come out of a file that ships inside whatever repository
    # the user has cd'd into, so they are written by whoever wrote that repo.
    # `cs hooks` is the command run to decide whether to trust it, and an
    # escape sequence here could erase the very line being judged — so the
    # text is stripped of anything a terminal acts on as it is read, not
    # left to each place that prints it.
    return [{
        "event": redact.one_line(_canonical(event)),
        "matcher": redact.one_line(matcher),
        "kind": redact.one_line(str(item.get("type") or "command")),
        "command": command,
        "timeout": redact.one_line(str(item["timeoutSec"]))
        if item.get("timeoutSec") is not None else None,
        "source": source,
        "scope": scope,
        "target": redact.one_line(str(target)) if target is not None else None,
        "missing": missing,
    }]


def _read(path: Path) -> tuple[dict, str]:
    """A hook file's event map, or the reason it could not be read.

    Both `{"hooks": {...}}` and a bare `{event: [...]}` are accepted, and a
    settings.json that simply declares no hooks is not a problem — it is the
    normal case.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"could not be read ({exc.__class__.__name__})"
    except json.JSONDecodeError as exc:
        return {}, f"is not valid JSON (line {exc.lineno})"
    if not isinstance(loaded, dict):
        return {}, "does not hold a JSON object"
    declared = loaded.get("hooks", loaded)
    if not isinstance(declared, dict):
        return {}, "has a 'hooks' key that is not an object"
    return declared, ""


def _files() -> list[tuple[Path, str]]:
    """Every readable hook file, in the order Copilot would load them."""
    found: list[tuple[Path, str]] = []
    for location, scope in search_paths():
        if location.is_dir():
            found.extend(
                (entry, scope)
                for entry in sorted(location.iterdir())
                if entry.is_file() and entry.suffix == ".json"
                and not entry.name.startswith(".")
            )
        elif location.is_file():
            found.append((location, scope))
    return found


def load() -> tuple[list[dict], list[tuple[Path, str]]]:
    """Every configured hook, plus the files that could not be read.

    Unreadable files are returned rather than skipped: a hook file with a
    trailing comma runs nothing at all, and silence is exactly the wrong
    report for that.
    """
    configured: list[dict] = []
    problems: list[tuple[Path, str]] = []
    for path, scope in _files():
        declared, problem = _read(path)
        if problem:
            # A settings.json is a hook file only incidentally; complaining
            # that one is malformed is the job of whatever else reads it.
            if path.name != "settings.json":
                problems.append((path, problem))
            continue
        for event, value in declared.items():
            configured.extend(_entries(event, value, path, scope))
    configured.sort(key=lambda hook: (order(hook["event"]), hook["command"]))
    return configured, problems


def parked() -> list[Path]:
    """Hook files and directories that were switched off rather than removed."""
    out: set[Path] = set()
    for base in (home(), Path.cwd() / ".copilot"):
        # Beside the hooks directory: anything hook-named that isn't it —
        # `hooks.off`, the usual way to switch the lot off for an afternoon.
        if base.is_dir():
            out.update(
                entry for entry in base.iterdir()
                if entry.name.startswith("hooks") and entry.name != "hooks"
            )
        # Inside it, name the file whatever you like: the suffix is what
        # decides whether Copilot will load it, and these ones it won't.
        if (folder := base / "hooks").is_dir():
            out.update(
                entry for entry in folder.iterdir()
                if entry.is_file() and entry.name.endswith(_PARKED)
            )
    return sorted(out)

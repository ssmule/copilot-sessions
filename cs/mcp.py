"""MCP servers — the tools Copilot can reach for that are not its own.

Skills and agent profiles are files the model may read. Hooks are commands
that fire on the lifecycle. MCP servers are the third thing a session starts
with and the only one that reaches outside the machine: a process spawned on
your box, or an HTTP endpoint somewhere else, either of which can be handed
the contents of the conversation.

Like hooks, this reads configuration and never history. The Copilot CLI store
records no MCP invocation event, so what a report built on this can honestly
say is *what is wired up* — the server, its transport, where it was declared,
which tools it is allowed to expose, and whether a credential was pasted into
the file rather than referenced from the environment. Usage, where it is
claimed at all, is inferred from session text and labelled as a signal.

Nothing here starts a server, and no credential value is ever read out of a
config file into a string this module returns.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import redact
from .hooks import home

# Both spellings in the wild. Copilot writes `mcpServers`; VS Code writes
# `servers`. A file that uses one is read the same as a file that uses the
# other, because a reader asking "what can this session call" does not care
# which editor wrote the JSON.
_KEYS = ("mcpServers", "servers")

# The same "parked rather than deleted" suffixes hooks looks for. A backed-up
# mcp-config.json is the usual way someone switches a server off for an
# afternoon, and "no servers" and "your servers are in a .bak" are different
# answers to the same question.
_PARKED = (".bak", ".off", ".disabled", ".sample", ".example", ".local")

# A value that came from the environment rather than from the file. Anything
# else sitting in a token or header field is a literal, and a literal is a
# credential someone committed.
_FROM_ENV = ("${", "$(", "%")


def search_paths() -> list[tuple[Path, str]]:
    """Every place an MCP server can be declared: (path, scope).

    Personal first, then the workspace, the order Copilot reads them in — and
    the order a reader needs to answer "where did THAT come from".
    """
    workspace = Path.cwd()
    return [
        (home() / "mcp-config.json", "personal"),
        (workspace / ".copilot" / "mcp-config.json", "workspace"),
        (workspace / ".mcp.json", "workspace"),
        (workspace / ".vscode" / "mcp.json", "workspace"),
    ]


def _read(path: Path) -> tuple[dict, str]:
    """A file's server map, or the reason it could not be read.

    An empty map is not a problem — a config file that declares no server is
    the normal case for a repository that has not needed one.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"could not be read ({exc.__class__.__name__})"
    except json.JSONDecodeError as exc:
        return {}, f"is not valid JSON (line {exc.lineno})"
    if not isinstance(loaded, dict):
        return {}, "does not hold a JSON object"
    for key in _KEYS:
        declared = loaded.get(key)
        if isinstance(declared, dict):
            return declared, ""
        if declared is not None:
            return {}, f"has a '{key}' key that is not an object"
    return {}, ""


def _transport(entry: dict) -> str:
    """local, http or sse — worked out from the shape when it is not stated.

    The distinction is the whole risk story: a local server is a process this
    machine runs, a remote one is a conversation leaving it.
    """
    stated = str(entry.get("type") or "").strip().lower()
    if stated in ("local", "stdio"):
        return "local"
    if stated in ("http", "sse"):
        return stated
    return "local" if entry.get("command") else "http" if entry.get("url") else "?"


def _endpoint(entry: dict, transport: str) -> str:
    """What this server actually is, in one line and with no secret in it.

    A URL's query string and userinfo are dropped rather than truncated: an
    MCP endpoint is routinely handed its token that way, and a report that
    prints one to help you audit it has created the problem it was checking
    for.
    """
    if transport == "local":
        command = str(entry.get("command") or "")
        args = entry.get("args")
        if isinstance(args, list):
            command = " ".join([command, *(str(a) for a in args)]).strip()
        return redact.one_line(command)
    url = str(entry.get("url") or "")
    for cut in ("?", "#"):
        url = url.split(cut, 1)[0]
    scheme, sep, rest = url.partition("://")
    if sep and "@" in rest.split("/", 1)[0]:
        rest = rest.split("@", 1)[1]
        url = f"{scheme}://{rest}"
    return redact.one_line(url)


def _missing(entry: dict, transport: str) -> bool:
    """Whether a local server names a program that is not there.

    Only judged for local servers, and only for something resolvable. A
    remote URL might be down this second and up the next, and guessing about
    that would be a worse answer than declining to give one.
    """
    if transport != "local":
        return False
    command = str(entry.get("command") or "").strip()
    if not command:
        return True
    if command.startswith(("/", "./", "~/")):
        return not Path(command).expanduser().exists()
    return shutil.which(command) is None


def _tools(entry: dict) -> tuple[list[str], bool]:
    """(named tools, whether it is allowed everything the server offers).

    `["*"]` is the default a wizard writes and the one worth noticing: it
    means whatever the server adds next is enabled too, without anyone
    deciding.
    """
    declared = entry.get("tools")
    if declared is None:
        return [], True
    if not isinstance(declared, list):
        return [], True
    names = [redact.one_line(str(name)) for name in declared if str(name).strip()]
    if "*" in names:
        return [n for n in names if n != "*"], True
    return names, False


def _secrets(entry: dict) -> list[str]:
    """Names of config keys holding a literal credential — never the value.

    A token written into `env` or `headers` as `${GITHUB_TOKEN}` is fine; the
    same field with the token itself in it is a credential sitting in a file
    that gets committed, and naming the key is enough to go and fix it.
    """
    found: list[str] = []
    for field in ("env", "headers"):
        holder = entry.get(field)
        if not isinstance(holder, dict):
            continue
        for key, value in holder.items():
            text = str(value or "")
            if len(text) < 8 or text.startswith(_FROM_ENV):
                continue
            found.append(redact.one_line(f"{field}.{key}"))
    return found


def _server(name: str, entry: dict, source: Path, scope: str) -> dict:
    transport = _transport(entry)
    named, everything = _tools(entry)
    # These strings come out of a file that ships inside whatever repository
    # the user has cd'd into, so they are written by whoever wrote that repo.
    # `cs mcp` is the command run to decide whether to trust it, and an escape
    # sequence here could rewrite the very line being judged — so the text is
    # stripped of anything a terminal acts on as it is read, rather than left
    # to each place that prints it.
    return {
        "name": redact.one_line(name),
        "transport": transport,
        "endpoint": _endpoint(entry, transport),
        "tools": named,
        "all_tools": everything,
        "off": not everything and not named,
        "missing": _missing(entry, transport),
        "secrets": _secrets(entry),
        "source": source,
        "scope": scope,
    }


def _files() -> list[tuple[Path, str]]:
    """Every readable config file, in the order Copilot would load them."""
    return [(path, scope) for path, scope in search_paths() if path.is_file()]


def load() -> tuple[list[dict], list[tuple[Path, str]]]:
    """Every configured MCP server, plus the files that could not be read.

    Unreadable files are returned rather than skipped: a config with a
    trailing comma starts no server at all, and silence is exactly the wrong
    report for that.

    A name declared twice is the workspace overriding your personal config,
    which is one server, not two — so the first file to declare it wins, in
    the same order Copilot resolves them.
    """
    servers: dict[str, dict] = {}
    problems: list[tuple[Path, str]] = []
    for path, scope in _files():
        declared, problem = _read(path)
        if problem:
            problems.append((path, problem))
            continue
        for name, entry in declared.items():
            if isinstance(entry, dict) and name not in servers:
                servers[name] = _server(name, entry, path, scope)
    return sorted(servers.values(), key=lambda s: s["name"].lower()), problems


def parked() -> list[Path]:
    """Config files that were switched off rather than removed."""
    out: set[Path] = set()
    for path, _scope in search_paths():
        folder = path.parent
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if not entry.is_file() or entry == path:
                continue
            if entry.name.startswith(path.name) and entry.name != path.name:
                out.add(entry)
            elif entry.name.startswith("mcp") and entry.name.endswith(_PARKED):
                out.add(entry)
    return sorted(out)

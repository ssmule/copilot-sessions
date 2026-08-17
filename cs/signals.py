"""Three things the store never records, read back out of what it does.

Copilot's session store has no column for approval mode, no parent-session
link, and no notion of a credential. All three matter once you are answering
for the work: which sessions ran unattended, which work was handed from one
session to the next, and which conversations have a password sitting in them.

Each is inferred here, and every verdict carries the evidence it was drawn
from — a judgement you can check is worth more than a number you have to
trust. Nothing in this module writes to the store.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict

from . import db, redact

# ── Autonomy ─────────────────────────────────────────────────────────
# `assistant_usage_events.initiator` says who asked for each model call:
# 'user' for a prompt you typed, 'agent'/'sub-agent' for steps the agent took
# on its own. Steps per prompt is therefore a direct measure of how far the
# session ran between check-ins — which is what YOLO mode actually changes.

UNATTENDED_RATIO = 20.0   # agent steps per prompt; the 98th percentile
UNATTENDED_STEPS = 40     # and enough of them to be a run, not a long answer

# Flags that turn every approval off, and the words that do the same when
# typed. Only your own messages count as evidence: the store is full of the
# agent *explaining* `--allow-all-tools`, which is not the same as using it.
_FLAGS = ("--allow-all-tools", "--allow-all-paths", "--allow-all-permissions")
# "yolo" on its own line is the toggle; "yolo means…" is a question about it.
_TYPED = re.compile(
    r"^\s*/?yolo\s*$|^\s*auto[ -]approve\s*$"
    r"|\b(?:turn on|enable|switch to|run in|use)\b[^.\n]{0,20}\byolo\b",
    re.I | re.M,
)


def _header(conn: sqlite3.Connection) -> str:
    """The six session facts every report here opens with, aliased `s`.

    Written through :func:`db.optional` rather than as a literal column list:
    Copilot has added columns over its releases, and a report that cannot say
    which repository a session belonged to should still say everything else.
    """
    return f"""s.id, substr(MAX(s.created_at, s.updated_at), 1, 16),
                  COALESCE({db.optional(conn, 'sessions', 'summary', 's', "''")}, ''),
                  COALESCE({db.optional(conn, 'sessions', 'repository', 's', "''")}, ''),
                  COALESCE({db.optional(conn, 'sessions', 'cwd', 's', "''")}, ''),
                  (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id)"""


def _evidence(prompt: str) -> str:
    """What this prompt shows about approvals, or '' if it shows nothing."""
    lowered = prompt.lower()
    flag = next((f for f in _FLAGS if f in lowered), None)
    if flag:
        return f"you passed {flag}"
    if _TYPED.search(prompt):
        return "you turned approvals off in the session"
    return ""


def _explicit(conn: sqlite3.Connection) -> dict[str, str]:
    """Session id → the evidence, for sessions that show approvals were off."""
    rows = conn.execute(
        """SELECT session_id, user_message FROM turns
           WHERE lower(user_message) LIKE '%allow-all-%'
              OR lower(user_message) LIKE '%yolo%'
              OR lower(user_message) LIKE '%auto-approve%'
              OR lower(user_message) LIKE '%auto approve%'"""
    ).fetchall()

    evidence: dict[str, str] = {}
    for session_id, prompt in rows:
        if session_id not in evidence and (why := _evidence(prompt or "")):
            evidence[session_id] = why
    return evidence


def _steps(conn: sqlite3.Connection) -> dict[str, int]:
    """Agent-initiated model calls per session — the steps you did not ask for."""

    if not db.has_delegation(conn):
        return {}
    return {
        session_id: count
        for session_id, count in conn.execute(
            """SELECT session_id, COUNT(*) FROM assistant_usage_events
               WHERE initiator IN ('agent', 'sub-agent') GROUP BY session_id"""
        )
    }


def _verdict(steps: int, turns: int, evidence: str) -> tuple[str, str]:
    """One of yes / high / no, with the reason it was reached."""
    if evidence:
        return "yes", evidence
    ratio = steps / max(turns, 1)
    if ratio >= UNATTENDED_RATIO and steps >= UNATTENDED_STEPS:
        return "high", f"{ratio:.0f} agent steps per prompt, unattended"
    if turns == 0:
        return "no", "no prompts recorded"
    return "no", f"{ratio:.1f} agent steps per prompt"


def autonomy(conn: sqlite3.Connection) -> list[dict]:
    """Every session that has prompts, most autonomous first.

    Sessions with no turns are dropped: a ratio needs something to divide by,
    and an empty session had no chance to run away with anything.
    """
    evidence = _explicit(conn)
    steps = _steps(conn)
    rows = conn.execute(
        f"""SELECT {_header(conn)}
           FROM sessions s"""
    ).fetchall()

    out = []
    for session_id, active, summary, repo, cwd, turns in rows:
        if not turns:
            continue
        agent_steps = steps.get(session_id, 0)
        verdict, why = _verdict(agent_steps, turns, evidence.get(session_id, ""))
        out.append({
            "id": session_id, "active": active, "summary": summary,
            "repo": repo, "cwd": cwd, "turns": turns, "steps": agent_steps,
            "ratio": agent_steps / turns, "verdict": verdict, "why": why,
        })
    order = {"yes": 0, "high": 1, "no": 2}
    out.sort(key=lambda r: (order[r["verdict"]], -r["ratio"]))
    return out


def session_autonomy(conn: sqlite3.Connection, session_id: str) -> dict:
    """The same verdict for one session, without scoring the whole store."""
    turns = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    steps = _steps_for(conn, session_id)
    evidence = _explicit_for(conn, session_id)
    verdict, why = _verdict(steps, turns, evidence)
    return {
        "verdict": verdict, "why": why, "steps": steps, "turns": turns,
        "ratio": steps / max(turns, 1),
    }


def _steps_for(conn: sqlite3.Connection, session_id: str) -> int:

    if not db.has_delegation(conn):
        return 0
    return conn.execute(
        """SELECT COUNT(*) FROM assistant_usage_events
           WHERE session_id = ? AND initiator IN ('agent', 'sub-agent')""",
        (session_id,),
    ).fetchone()[0]


def _explicit_for(conn: sqlite3.Connection, session_id: str) -> str:
    for (prompt,) in conn.execute(
        "SELECT user_message FROM turns WHERE session_id = ? ORDER BY turn_index",
        (session_id,),
    ):
        if why := _evidence(prompt or ""):
            return why
    return ""


# ── Handoffs ─────────────────────────────────────────────────────────
# A handoff is a document one session writes so the next can pick the work
# up. Nothing links the two sessions in the store, but the document does: it
# is opened by both, and `session_files` records every file a session touched.

_DOC = re.compile(r"hand[-_ ]?off[^/]*\.(?:md|markdown|txt)$", re.I)
_ASK_WRITE = re.compile(
    r"\b(?:creat\w*|writ\w*|generat\w*|provid\w*|produc\w*|prepar\w*|updat\w*)\b"
    r"[^.\n]{0,40}\bhand[- ]?off",
    re.I,
)
_ASK_READ = re.compile(
    r"\b(?:read|take|use|open|follow|review|continue)\b[^.\n]{0,40}\bhand[- ]?off"
    r"|\bhand[- ]?off\b[^.\n]{0,40}\b(?:from|of)\s+(?:the\s+)?previous\b"
    r"|\bprevious\s+session\b[^.\n]{0,30}\bhand[- ]?off",
    re.I,
)


def _docs_by_session(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Handoff documents each session opened or wrote."""

    if not db.has_files(conn):
        return {}
    out: dict[str, list[str]] = defaultdict(list)
    for session_id, path in conn.execute(
        "SELECT session_id, file_path FROM session_files WHERE file_path LIKE '%hand%'"
    ):
        if _DOC.search(path or ""):
            out[session_id].append(path)
    return dict(out)


def _asked(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """What each session's prompts asked for: writing a handoff, reading one."""
    out: dict[str, set[str]] = defaultdict(set)
    for session_id, prompt in conn.execute(
        "SELECT session_id, user_message FROM turns "
        "WHERE lower(user_message) LIKE '%hand%off%'"
    ):
        text = prompt or ""
        if _ASK_WRITE.search(text):
            out[session_id].add("wrote")
        if _ASK_READ.search(text):
            out[session_id].add("read")
    return dict(out)


def _role(docs: list[str], asked: set[str]) -> str:
    """emitted / received / both / none, from the document and what was asked."""
    if "wrote" in asked and "read" in asked:
        return "both"
    if "wrote" in asked:
        return "emitted"
    if "read" in asked:
        return "received"
    return "touched" if docs else "none"


def handoffs(conn: sqlite3.Connection) -> list[dict]:
    """Every session involved in a handoff, newest first."""
    docs = _docs_by_session(conn)
    asked = _asked(conn)
    ids = set(docs) | set(asked)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT {_header(conn)}
            FROM sessions s WHERE s.id IN ({placeholders})
            ORDER BY MAX(s.created_at, s.updated_at) DESC""",
        tuple(ids),
    ).fetchall()
    return [
        {
            "id": session_id, "active": active, "summary": summary,
            "repo": repo, "cwd": cwd, "turns": turns,
            "docs": sorted(docs.get(session_id, [])),
            "role": _role(docs.get(session_id, []), asked.get(session_id, set())),
        }
        for session_id, active, summary, repo, cwd, turns in rows
    ]


def session_handoff(conn: sqlite3.Connection, session_id: str) -> dict:
    """Whether one session handed work on, took it up, or neither."""
    docs = sorted(_docs_by_session(conn).get(session_id, []))
    asked = _asked(conn).get(session_id, set())
    return {"role": _role(docs, asked), "docs": docs}


_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)


def edges(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(parent, child, why) links between sessions, always older → newer.

    Two kinds of link, both evidence rather than inference: sessions that
    opened the same handoff document, and sessions that name another session's
    id — which is what `cs resume` prints and people paste into the next one.

    Building this reads every turn, so callers drawing more than one chain
    should build it once and hand it to `chain`.
    """
    started = {
        session_id: created
        for session_id, created in conn.execute("SELECT id, created_at FROM sessions")
    }
    edges: list[tuple[str, str, str]] = []

    shared: dict[str, list[str]] = defaultdict(list)
    for session_id, paths in _docs_by_session(conn).items():
        for path in paths:
            shared[path].append(session_id)
    for path, sessions in shared.items():
        if len(sessions) < 2:
            continue
        chain = sorted(sessions, key=lambda s: started.get(s) or "")
        name = path.rsplit("/", 1)[-1]
        for parent, child in zip(chain, chain[1:], strict=False):
            edges.append((parent, child, f"via {name}"))

    for session_id, prompt, reply in conn.execute(
        "SELECT session_id, user_message, assistant_response FROM turns"
    ):
        for match in _UUID.findall(f"{prompt or ''}\n{reply or ''}"):
            other = match.lower()
            if other == session_id or other not in started:
                continue
            older, newer = (
                (other, session_id)
                if (started.get(other) or "") <= (started.get(session_id) or "")
                else (session_id, other)
            )
            edges.append((older, newer, "session id referenced"))

    seen: set[tuple[str, str]] = set()
    unique = []
    for parent, child, why in edges:
        if (parent, child) in seen:
            continue
        seen.add((parent, child))
        unique.append((parent, child, why))
    return unique


def chain(
    conn: sqlite3.Connection, session_id: str,
    links: list[tuple[str, str, str]] | None = None,
) -> dict:
    """The handoff tree `session_id` belongs to: its whole connected group.

    Returns roots and a parent → children map, so the caller can draw it.
    Sessions with no links come back as a single node — a chain of one is
    still the honest answer.
    """
    links = edges(conn) if links is None else links
    neighbours: dict[str, set[str]] = defaultdict(set)
    for parent, child, _ in links:
        neighbours[parent].add(child)
        neighbours[child].add(parent)

    group, queue = {session_id}, [session_id]
    while queue:
        for neighbour in neighbours[queue.pop()]:
            if neighbour not in group:
                group.add(neighbour)
                queue.append(neighbour)

    children: dict[str, list[tuple[str, str]]] = defaultdict(list)
    has_parent: set[str] = set()
    for parent, child, why in links:
        if parent in group and child in group:
            children[parent].append((child, why))
            has_parent.add(child)

    detail = {}
    placeholders = ",".join("?" * len(group))
    for row in conn.execute(
        f"""SELECT s.id, substr(s.created_at, 1, 16),
                   COALESCE({db.optional(conn, 'sessions', 'summary', 's', "''")}, ''),
                   COALESCE({db.optional(conn, 'sessions', 'repository', 's', "''")}, ''),
                   COALESCE({db.optional(conn, 'sessions', 'cwd', 's', "''")}, ''),
                   (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id)
            FROM sessions s WHERE s.id IN ({placeholders})""",
        tuple(group),
    ):
        detail[row[0]] = {
            "id": row[0], "started": row[1], "summary": row[2],
            "repo": row[3], "cwd": row[4], "turns": row[5],
        }
    roots = sorted(
        (s for s in group if s not in has_parent),
        key=lambda s: detail.get(s, {}).get("started") or "",
    )
    return {"roots": roots, "children": dict(children), "detail": detail,
            "size": len(group)}


# ── Credential exposure ──────────────────────────────────────────────
# `cs` masks secrets on the way to the screen. Masking alone leaves you
# unaware, so the same rules are run over the store to answer the question
# masking cannot: which conversations have a credential in them at all.

# Files whose whole purpose is to hold a credential. `session_files` proves
# that a path was touched, not that its contents were read, so this is a path
# warning rather than a credential-exposure claim.
_SENSITIVE_FILES: list[tuple[str, re.Pattern[str]]] = [
    ("env file", re.compile(r"(?:^|/)\.env(?:\.[^/]*)?$")),
    ("ssh private key", re.compile(r"(?:^|/)id_(?:rsa|dsa|ecdsa|ed25519)$")),
    ("aws credentials", re.compile(r"/\.aws/(?:credentials|config)$")),
    ("kubeconfig", re.compile(r"(?:/\.kube/config$|kubeconfig[^/]*$)")),
    ("registry login", re.compile(r"(?:^|/)\.(?:npmrc|pypirc|netrc|docker/config\.json)$")),
    ("certificate or keystore", re.compile(r"(?i)\.(?:pem|key|p12|pfx|jks|keystore)$")),
    ("terraform state", re.compile(r"\.tfstate(?:\.backup)?$")),
    ("secrets file", re.compile(r"(?i)(?:^|/)[^/]*secrets?[^/]*\.(?:ya?ml|json|toml|txt)$")),
]


def sensitive_files(
    conn: sqlite3.Connection, session_id: str | None = None
) -> list[dict]:
    """Sessions that touched a file whose job is to hold a credential.

    The text scan can only find a secret that was written down in the
    conversation. `session_files` records paths created or edited; it does not
    prove that the file contents were read, so the report says only "touched".
    """
    if not db.has_files(conn):
        return []
    tool = db.optional(conn, "session_files", "tool_name")
    sql = f"SELECT session_id, file_path, {tool} FROM session_files"
    params: tuple = ()
    if session_id:
        sql += " WHERE session_id = ?"
        params = (session_id,)

    per: dict[str, dict] = {}
    for sid, path, tool in conn.execute(sql, params):
        kind = next((name for name, pattern in _SENSITIVE_FILES
                     if pattern.search(path or "")), None)
        if not kind:
            continue
        entry = per.setdefault(
            sid, {"id": sid, "count": 0, "kinds": Counter(), "paths": [],
                  "wrote": False}
        )
        entry["count"] += 1
        entry["kinds"][kind] += 1
        entry["wrote"] = entry["wrote"] or tool == "create"
        if path not in entry["paths"]:
            entry["paths"].append(path)
    if not per:
        return []

    placeholders = ",".join("?" * len(per))
    meta = {
        row[0]: row[1:]
        for row in conn.execute(
            f"""SELECT id, substr(MAX(created_at, updated_at), 1, 16),
                       COALESCE({db.optional(conn, 'sessions', 'summary', '', "''")}, ''),
                       COALESCE({db.optional(conn, 'sessions', 'cwd', '', "''")}, '')
                FROM sessions WHERE id IN ({placeholders})""",
            tuple(per),
        )
    }
    for entry in per.values():
        active, summary, cwd = meta.get(entry["id"], ("", "", ""))
        entry.update(active=active, summary=summary, cwd=cwd)
    return sorted(per.values(), key=lambda e: (-e["count"], e["active"]), reverse=False)


def _masked_line(text: str) -> str:
    """The masked line a finding sits on, or "" when there isn't a safe one.

    The line is taken from `redact.redact`'s output, never the raw text, so
    the only way a value could reach the caller is if the masker had already
    let it through — and a line with no mask on it is dropped, which is also
    what happens when masking is switched off entirely.
    """
    for line in redact.redact(text).splitlines():
        if "[redacted" in line:
            return " ".join(line.split())
    return ""


def _checkpoint_text(
    conn: sqlite3.Connection, session_id: str | None
) -> list[tuple[str, str]]:
    """(session id, prose) for the latest checkpoint of each session.

    Checkpoints are written by the agent, out of the same conversation, and
    say things like "set DB_PASSWORD in the config". They are a different
    table from `turns`, so scanning turns alone left them unread. `cs show`
    opens only the latest checkpoint, so the audit scans exactly that record.
    """
    if not db._has_table(conn, "checkpoints"):
        return []
    columns = [row[1] for row in conn.execute("PRAGMA table_info(checkpoints)")]
    visible = [name for name in ("next_steps", "work_done") if name in columns]
    if not visible or "session_id" not in columns:
        return []
    sql = f"SELECT session_id, {', '.join(visible)} FROM checkpoints"
    if "checkpoint_number" in columns:
        sql += """ c WHERE checkpoint_number = (
                    SELECT MAX(newer.checkpoint_number)
                    FROM checkpoints newer
                    WHERE newer.session_id = c.session_id
                  )"""
    params: tuple = ()
    if session_id:
        sql += " AND c.session_id = ?" if "checkpoint_number" in columns else (
            " WHERE session_id = ?"
        )
        params = (session_id,)
    def bullets(text: str) -> list[str]:
        picked = []
        in_code = False
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not line:
                continue
            line = re.sub(r"\*\*|__|^#{1,6}\s*", "", line)
            line = re.sub(r"^([-*•]|\d+[.)])\s+", "", line)
            picked.append(re.sub(r"\s+", " ", line))
            if len(picked) >= 5:
                break
        return picked

    return [
        (row[0], "\n".join(
            line for value in row[1:] if value for line in bullets(str(value))
        ))
        for row in conn.execute(sql, params)
    ]


def exposures(conn: sqlite3.Connection, session_id: str | None = None) -> list[dict]:
    """Sessions holding credential-shaped text, worst first.

    A finding names the kind and where it is. It never carries the value:
    an audit that prints the secret has simply leaked it somewhere new.
    """
    sql = """SELECT t.session_id, t.turn_index, t.user_message, t.assistant_response
             FROM turns t"""
    params: tuple = ()
    if session_id:
        sql += " WHERE t.session_id = ?"
        params = (session_id,)

    # One row per session and side. A prompt finding is proven to have been
    # pasted by the user; a reply finding is only proven to be assistant
    # output. Combining them made every finding in a mixed session look pasted.
    per: dict[tuple[str, str], dict] = {}

    def record(sid: str, side: str, source: str, where: int, text: str) -> None:
        for kind, hint in redact.findings(text or ""):
            entry = per.setdefault(
                (sid, side), {"id": sid, "count": 0, "kinds": Counter(),
                              "hints": [], "pasted": side == "you",
                              "turn": where, "line": "", "side": side,
                              "source": source}
            )
            entry["count"] += 1
            entry["kinds"][kind] += 1
            if hint and hint not in entry["hints"]:
                entry["hints"].append(hint)
            if not entry["line"]:
                line = _masked_line(text or "")
                if line:
                    entry.update(line=line, turn=where)

    for sid, turn_index, prompt, reply in conn.execute(sql + " ORDER BY t.turn_index", params):
        for side, text in (("you", prompt), ("agent", reply)):
            record(sid, side, "turn", turn_index, text)

    # Its own side, so a checkpoint finding is never reported as a turn you
    # could open — there is no turn number to open it at.
    for sid, prose in _checkpoint_text(conn, session_id):
        record(sid, "checkpoint", "checkpoint", 0, prose)

    if not per:
        return []
    session_ids = sorted({entry["id"] for entry in per.values()})
    placeholders = ",".join("?" * len(session_ids))
    meta = {
        row[0]: row[1:]
        for row in conn.execute(
            f"""SELECT id, substr(MAX(created_at, updated_at), 1, 16),
                       COALESCE({db.optional(conn, 'sessions', 'summary', '', "''")}, ''),
                       COALESCE({db.optional(conn, 'sessions', 'repository', '', "''")}, ''),
                       COALESCE({db.optional(conn, 'sessions', 'cwd', '', "''")}, '')
                FROM sessions WHERE id IN ({placeholders})""",
            tuple(session_ids),
        )
    }
    for entry in per.values():
        sid = entry["id"]
        active, summary, repo, cwd = meta.get(sid, ("", "", "", ""))
        entry.update(active=active, summary=summary, repo=repo, cwd=cwd)
        # A session is as serious as the most certain thing in it, and is
        # ranked on that rather than on how many findings it has: one leaked
        # private key outranks thirty password-shaped assignments.
        entry["severity"] = next(
            (name for name in redact.RANK
             if any(redact.severity(kind) == name for kind in entry["kinds"])),
            "medium",
        )
        entry["rank"] = redact.RANK.index(entry["severity"])
    return sorted(per.values(),
                  key=lambda e: (e["rank"], -e["count"], e["id"], e["side"]))

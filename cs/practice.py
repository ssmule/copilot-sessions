"""Practice review — what the record says about *how* the work is being done.

`db.py` says what happened. `signals.py` says what the store implies about
approvals, handoffs and credentials. This module asks a different question
again: given a month of sessions, which habits are costing you results?

Every rule here works the same way. It reads one snapshot of the window,
counts the cases it recognises, and fires only when there are enough of them
to mean something — a store with nine sessions in it should not be lectured.
Each finding carries the sample it was drawn from and up to three real
examples, because a habit you cannot see an example of is not a finding, it
is an accusation.

The store columns this leans on — `duration_ms`, `finish_reason`,
`cache_read_tokens`, `reasoning_effort`, `turns.timestamp` — are recorded by
Copilot and read nowhere else in cs. Each is optional, and a rule whose column
is absent simply does not fire.

Nothing here writes to the store, and nothing here judges the code.
"""

from __future__ import annotations

import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import db, redact

# What a rule costs the group it belongs to when it fires. Written down here
# rather than tuned per rule: a score you cannot recompute in your head is a
# score you have to take on faith.
COST = {"high": 25, "medium": 15, "low": 8}

GROUPS = ("prompt quality", "session hygiene", "model & spend", "review habits")


@dataclass
class Finding:
    """One habit the record shows, with the evidence it was drawn from."""

    rule: str
    name: str
    group: str
    severity: str
    count: int
    total: int
    headline: str
    fix: str
    evidence: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        return self.count / self.total if self.total else 0.0


@dataclass
class Turn:
    session: str
    index: int
    prompt: str
    prompt_len: int
    reply_len: int
    when: datetime | None


@dataclass
class Work:
    """Everything one turn asked the models to do."""

    calls: int = 0
    steps: int = 0              # calls the agent made on its own
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    reasoning: int = 0
    slowest_ms: int = 0
    models: set[str] = field(default_factory=set)
    efforts: Counter = field(default_factory=Counter)
    endings: Counter = field(default_factory=Counter)


# Blocks the harness injects into the user side of a turn: reminders, skill
# preambles, the echo of a slash command, tool results. They are stored as
# `user_message` because that is the channel they arrive on, but nobody typed
# them — and a review that counts them is reviewing the harness, not the work.
_INJECTED = re.compile(
    r"<(system[-_]reminder|skill-context|local-command-[a-z]+|command-name|"
    r"command-message|command-args|function_results|function_calls|"
    r"user-prompt-submit-hook)\b.*?(?:</\1>|$)",
    re.S | re.I,
)


def clean(prompt: str) -> str:
    """A prompt reduced to what the person actually typed."""
    return _INJECTED.sub(" ", prompt).strip()


def _local(stamp: str) -> datetime | None:
    """Copilot writes UTC with a Z; habits happen in your own evening."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


@dataclass
class Snapshot:
    """One window of the store, read once and handed to every rule."""

    days: int
    sessions: dict[str, dict]
    turns: list[Turn]
    work: dict[tuple[str, int], Work]
    files: dict[tuple[str, int], list[str]]

    def turns_by_session(self) -> dict[str, list[Turn]]:
        out: dict[str, list[Turn]] = defaultdict(list)
        for turn in self.turns:
            out[turn.session].append(turn)
        for group in out.values():
            group.sort(key=lambda t: t.index)
        return dict(out)

    def calls(self) -> int:
        return sum(work.calls for work in self.work.values())

    def clock_turns(self) -> list[Turn]:
        """Timestamped turns that fall inside the window themselves.

        The window picks *sessions* active in the last N days, which is what
        every other view in cs means by it — but a session touched yesterday
        may have opened in March, and counting its March turns as this
        month's evenings would put work in a week it did not happen in.
        Anything asking what time of day it was has to cut by the turn.
        """
        timed = [t for t in self.turns if t.when]
        if self.days <= 0 or not timed:
            return timed
        newest = max(t.when for t in timed)
        return [t for t in timed if (newest - t.when).days < self.days]


def _window(conn: sqlite3.Connection, days: int) -> tuple[str, tuple]:
    """The same windowing convention the rest of cs uses: 0 means all of time."""
    if days <= 0:
        return "", ()
    return "WHERE MAX(created_at, updated_at) >= datetime('now', ?)", (f"-{days} days",)


def snapshot(conn: sqlite3.Connection, days: int) -> Snapshot:
    """Read the window once. Rules never touch the database themselves."""
    where, args = _window(conn, days)
    # Scheduled runs are somebody's cron job wearing a session's clothes.
    # Counting them as practice would report the pipeline's habits as yours —
    # the same reason every listing honours this file.
    hidden = tuple(db.ignored_prefixes())
    sessions = {
        row[0]: {
            "id": row[0],
            "repo": row[1] or "",
            "cwd": row[2] or "",
            "summary": row[3] or "",
            "started": _local(row[4]),
        }
        for row in conn.execute(
            f"""SELECT id,
                       {db.optional(conn, 'sessions', 'repository', '', "''")},
                       {db.optional(conn, 'sessions', 'cwd', '', "''")},
                       {db.optional(conn, 'sessions', 'summary', '', "''")},
                       created_at
                FROM sessions {where}""",
            args,
        )
        if not (hidden and (row[3] or "").startswith(hidden))
    }
    if not sessions:
        return Snapshot(days, {}, [], {}, {})

    stamp = db.optional(conn, "turns", "timestamp", "", "''")
    turns = []
    for session_id, index, prompt, reply_len, when in conn.execute(
        f"""SELECT session_id, turn_index,
                   substr(COALESCE(user_message, ''), 1, 2000),
                   length(COALESCE(assistant_response, '')),
                   COALESCE({stamp}, '')
            FROM turns ORDER BY session_id, turn_index"""
    ):
        if session_id not in sessions:
            continue
        typed = clean(prompt)
        turns.append(
            Turn(session_id, index, typed, len(typed), reply_len, _local(when))
        )

    # A session with no turns is not a habit, it is a launch. The CLI writes
    # the row the moment it starts, so a window holds hundreds that recorded
    # nothing; counting them would put a denominator on the screen that none
    # of the findings beneath it were measured against.
    spoke = {turn.session for turn in turns}
    sessions = {sid: row for sid, row in sessions.items() if sid in spoke}
    if not sessions:
        return Snapshot(days, {}, [], {}, {})

    work: dict[tuple[str, int], Work] = defaultdict(Work)
    if db.has_usage(conn):
        work = _work(conn, sessions)

    files: dict[tuple[str, int], list[str]] = defaultdict(list)
    if db.has_files(conn) and db._has_columns(conn, "session_files", "turn_index"):
        for session_id, index, path in conn.execute(
            "SELECT session_id, COALESCE(turn_index, 0), file_path FROM session_files"
        ):
            if session_id in sessions:
                files[(session_id, index)].append(path)

    return Snapshot(days, sessions, turns, dict(work), dict(files))


def _work(conn: sqlite3.Connection, sessions: dict) -> dict[tuple[str, int], Work]:
    """Per-turn model usage, with every optional column filled in or skipped."""
    columns = {
        name: db.optional(conn, "assistant_usage_events", name, "", "NULL")
        for name in ("input_tokens", "output_tokens", "cache_read_tokens",
                     "reasoning_tokens", "reasoning_effort", "duration_ms",
                     "finish_reason", "initiator", "turn_index", "model")
    }
    out: dict[tuple[str, int], Work] = defaultdict(Work)
    for row in conn.execute(
        f"""SELECT session_id, COALESCE({columns['turn_index']}, 0),
                   COALESCE({columns['model']}, ''),
                   COALESCE({columns['input_tokens']}, 0),
                   COALESCE({columns['output_tokens']}, 0),
                   COALESCE({columns['cache_read_tokens']}, 0),
                   COALESCE({columns['reasoning_tokens']}, 0),
                   COALESCE({columns['reasoning_effort']}, ''),
                   COALESCE({columns['duration_ms']}, 0),
                   COALESCE({columns['finish_reason']}, ''),
                   COALESCE({columns['initiator']}, '')
            FROM assistant_usage_events"""
    ):
        (session_id, index, model, sent, produced, cached,
         reasoning, effort, duration, ending, initiator) = row
        if session_id not in sessions:
            continue
        item = out[(session_id, index)]
        item.calls += 1
        item.steps += 1 if initiator in ("agent", "sub-agent") else 0
        item.input_tokens += sent
        item.output_tokens += produced
        item.cache_read += cached
        item.reasoning += reasoning
        item.slowest_ms = max(item.slowest_ms, duration)
        if model:
            item.models.add(model)
        if effort:
            item.efforts[effort] += 1
        if ending:
            item.endings[ending] += 1
    return out


# ── What the prompts look like ───────────────────────────────────────

THIN_PROMPT = 30          # characters; below this there is no brief in it
_CONSTRAINT = re.compile(
    r"\b(?:must|should|do not|don't|never|only|avoid|ensure|require[sd]?|"
    r"instead of|without|keep|prefer|at most|at least|no more than)\b", re.I
)
_STRUCTURE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s)", re.M)
_LOOKUP = re.compile(
    r"^\s*(?:what|where|which|who|when)\b.{0,80}\?\s*$"
    r"|^\s*(?:how do i|how to|is there|can i)\b.{0,80}\?\s*$", re.I | re.S
)
_HEAT = re.compile(r"!{3,}|\?{3,}|\bwtf\b|\bffs\b|\bcome on\b|\bstill broken\b"
                   r"|\bthis is (?:broken|wrong|useless)\b|\bwhy won'?t\b", re.I)
_FLUFF = re.compile(
    r"\b(?:please|kindly|basically|actually|just|simply|really|very|"
    r"i think|maybe|perhaps|sort of|kind of|you know)\b", re.I
)

# Coarse work types, only distinct enough to notice a session doing five
# unrelated things. Deliberately not a taxonomy.
_WORK_TYPES = (
    ("fix", re.compile(r"\b(?:fix|bug|error|crash|broken|fail(?:ing|ed)?|debug)\b", re.I)),
    ("build", re.compile(r"\b(?:add|build|implement|create|write|introduce)\b", re.I)),
    ("refactor", re.compile(r"\b(?:refactor|rename|clean ?up|simplify|extract|move)\b", re.I)),
    ("test", re.compile(r"\b(?:test|spec|coverage|assert)\b", re.I)),
    ("docs", re.compile(r"\b(?:document|readme|docs?|changelog|comment)\b", re.I)),
    ("review", re.compile(r"\b(?:review|explain|why|how does|walk me through|audit)\b", re.I)),
    ("ops", re.compile(r"\b(?:deploy|release|ci|pipeline|docker|install|config)\b", re.I)),
)


def _work_type(prompt: str) -> str:
    for name, pattern in _WORK_TYPES:
        if pattern.search(prompt):
            return name
    return "other"


def _normalise(prompt: str) -> str:
    """A prompt reduced to what makes it the same request as another one."""
    lowered = re.sub(r"[^a-z0-9 ]+", " ", prompt.lower())
    return " ".join(lowered.split())[:120]


def _example(turn: Turn, note: str = "") -> str:
    """One evidence line, masked here because it is cut here.

    The caller masks again at the render edge, which is where the rule
    belongs — but masking has to happen before the 70-character cut, not
    after. A credential the cut lands inside is no longer shaped like one,
    so every pattern misses it and the surviving prefix prints in clear.
    """
    head = " ".join(redact.redact(turn.prompt).split())[:70] or "(no prompt)"
    tail = f" · {note}" if note else ""
    return f"{turn.session[:8]} #{turn.index}  {head}{tail}"


def _summary(snap: Snapshot, session: str, width: int) -> str:
    """A session's summary, masked before it is cut — see `_example`."""
    text = redact.redact(snap.sessions[session]["summary"] or "")
    return " ".join(text.split())[:width]


# ── The rules ────────────────────────────────────────────────────────
# Each takes the snapshot and returns a Finding or None. A rule that cannot
# reach its minimum sample returns None rather than a reassuring zero: an
# absence of evidence is not a clean bill of health, and saying so would be
# the one dishonest thing this module could do.

def _thin_prompts(snap: Snapshot) -> Finding | None:
    asked = [t for t in snap.turns if t.prompt_len > 0]
    if len(asked) < 20:
        return None
    thin = [t for t in asked if t.prompt_len < THIN_PROMPT]
    if len(thin) / len(asked) <= 0.30:
        return None
    return Finding(
        "thin-prompts", "Thin prompts", "prompt quality", "medium",
        len(thin), len(asked),
        f"{len(thin)} of {len(asked)} prompts are under {THIN_PROMPT} characters",
        "Say what you want changed, what must not change, and how you will "
        "know it worked. Three sentences beats three retries.",
        [_example(t, f"{t.prompt_len} chars") for t in thin[:3]],
    )


def _repeated_prompts(snap: Snapshot) -> Finding | None:
    seen: dict[str, list[Turn]] = defaultdict(list)
    for turn in snap.turns:
        if turn.prompt_len >= 15:
            seen[_normalise(turn.prompt)].append(turn)
    repeats = {key: group for key, group in seen.items() if len(group) >= 3}
    if not repeats:
        return None
    asked = sum(1 for t in snap.turns if t.prompt_len >= 15)
    count = sum(len(group) for group in repeats.values())
    ranked = sorted(repeats.values(), key=len, reverse=True)
    return Finding(
        "repeated-prompts", "The same ask, again", "prompt quality", "medium",
        count, asked,
        f"{count} prompts are near-duplicates of {len(repeats)} distinct asks",
        "A prompt you type three times is a skill. Put it in "
        "$COPILOT_HOME/skills and call it by name — 'cs skills' lists what "
        "you already have.",
        [_example(group[0], f"asked {len(group)}×") for group in ranked[:3]],
    )


def _frustration(snap: Snapshot) -> Finding | None:
    hot = [t for t in snap.turns if t.prompt and _HEAT.search(t.prompt)]
    caps = [
        t for t in snap.turns
        if t.prompt_len > 15 and sum(c.isupper() for c in t.prompt)
        > 0.6 * max(1, sum(c.isalpha() for c in t.prompt))
    ]
    flagged = {(t.session, t.index): t for t in hot + caps}
    if len(flagged) < 3:
        return None
    return Finding(
        "frustration", "Prompts written in anger", "prompt quality", "medium",
        len(flagged), len(snap.turns),
        f"{len(flagged)} prompts carry frustration markers — caps, !!!, "
        f"'still broken'",
        "The model does not read tone, so the heat is pure loss. When a "
        "thread turns, start a fresh session and restate the problem from "
        "the top: it is usually the context that is wrong, not the model.",
        [_example(t) for t in list(flagged.values())[:3]],
    )


def _no_constraints(snap: Snapshot) -> Finding | None:
    substantial = [t for t in snap.turns if t.prompt_len >= THIN_PROMPT]
    if len(substantial) < 25:
        return None
    loose = [t for t in substantial if not _CONSTRAINT.search(t.prompt)]
    if len(loose) / len(substantial) <= 0.75:
        return None
    return Finding(
        "no-constraints", "Prompts with no boundaries", "prompt quality", "low",
        len(loose), len(substantial),
        f"{len(loose)} of {len(substantial)} substantial prompts name no "
        f"constraint",
        "One 'don't touch X' or 'only in Y' saves a revert. Constraints are "
        "cheaper to type than to undo.",
        [_example(t) for t in loose[:3]],
    )


def _unstructured_openings(snap: Snapshot) -> Finding | None:
    """How a long session opens decides most of what follows."""
    by_session = snap.turns_by_session()
    long_runs = {
        sid: group for sid, group in by_session.items() if len(group) >= 8
    }
    if len(long_runs) < 5:
        return None
    vague = [
        group[0] for group in long_runs.values()
        if group and group[0].prompt_len < 200
        and not _STRUCTURE.search(group[0].prompt)
    ]
    if len(vague) / len(long_runs) <= 0.6:
        return None
    return Finding(
        "unstructured-openings", "Long sessions that open vague",
        "prompt quality", "medium",
        len(vague), len(long_runs),
        f"{len(vague)} of {len(long_runs)} sessions that ran 8+ turns opened "
        f"with an unstructured prompt",
        "A session that will run all afternoon deserves a brief: a numbered "
        "list of what must be true at the end. The turns you save are the "
        "ones spent re-explaining.",
        [_example(t) for t in vague[:3]],
    )


# ── Session hygiene ──────────────────────────────────────────────────

MEGA_TURNS = 50


def _mega_sessions(snap: Snapshot) -> Finding | None:
    by_session = snap.turns_by_session()
    if len(by_session) < 10:
        return None
    huge = {sid: group for sid, group in by_session.items()
            if len(group) >= MEGA_TURNS}
    if not huge:
        return None
    ranked = sorted(huge.items(), key=lambda kv: len(kv[1]), reverse=True)
    return Finding(
        "mega-sessions", "Sessions that never ended", "session hygiene", "high",
        len(huge), len(by_session),
        f"{len(huge)} sessions ran past {MEGA_TURNS} turns",
        "Past a few dozen turns the early context is compacted away and the "
        "model is working from a summary of a summary. Close the session, "
        "write a handoff, open a fresh one — 'cs handoff' shows who does.",
        [f"{sid[:8]}  {len(group)} turns · {_summary(snap, sid, 44)}"
         for sid, group in ranked[:3]],
    )


def _one_shot_sessions(snap: Snapshot) -> Finding | None:
    by_session = snap.turns_by_session()
    if len(by_session) < 20:
        return None
    single = [sid for sid, group in by_session.items() if len(group) == 1]
    if len(single) / len(by_session) <= 0.35:
        return None
    return Finding(
        "one-shot-sessions", "Asked once, walked away", "session hygiene", "low",
        len(single), len(by_session),
        f"{len(single)} of {len(by_session)} sessions ended after one turn",
        "The second turn is where the answer gets useful — it is the one "
        "that corrects the first. Ending there leaves the refinement on the "
        "table.",
        [f"{sid[:8]}  {_summary(snap, sid, 52)}"
         for sid in single[:3]],
    )


def _late_night(snap: Snapshot) -> Finding | None:
    timed = snap.clock_turns()
    if len(timed) < 40:
        return None
    late = [t for t in timed if t.when.hour >= 22 or t.when.hour < 5]
    if len(late) / len(timed) <= 0.15:
        return None
    return Finding(
        "late-night", "Work after ten", "session hygiene", "low",
        len(late), len(timed),
        f"{len(late)} of {len(timed)} turns landed between 22:00 and 05:00",
        "Not a productivity note — a review one. Code written at 2am is "
        "reviewed by nobody, including you. 'cs rhythm' shows the shape of "
        "the week.",
        [_example(t, t.when.strftime("%a %H:%M")) for t in late[:3]],
    )


def _weekend(snap: Snapshot) -> Finding | None:
    timed = snap.clock_turns()
    if len(timed) < 40:
        return None
    weekend = [t for t in timed if t.when.weekday() >= 5]
    if len(weekend) / len(timed) <= 0.25:
        return None
    return Finding(
        "weekend", "Weekends are working days", "session hygiene", "low",
        len(weekend), len(timed),
        f"{len(weekend)} of {len(timed)} turns happened on a Saturday or Sunday",
        "Sustained weekend work is the clearest leading indicator of a "
        "delivery date that was never real. Worth raising before it is "
        "worth fixing.",
        [_example(t, t.when.strftime("%a %d %b")) for t in weekend[:3]],
    )


def _broken_flow(snap: Snapshot) -> Finding | None:
    """Long gaps mid-session: the work is being interrupted, not done."""
    fragmented = []
    considered = 0
    for sid, group in snap.turns_by_session().items():
        stamps = [t.when for t in group if t.when]
        if len(stamps) < 5:
            continue
        considered += 1
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(stamps, stamps[1:], strict=False)]
        median = statistics.median(gaps)
        if median > 30:
            fragmented.append((sid, median, len(group)))
    if considered < 8 or len(fragmented) / considered <= 0.3:
        return None
    fragmented.sort(key=lambda row: row[1], reverse=True)
    return Finding(
        "broken-flow", "Sessions held open across the day",
        "session hygiene", "medium",
        len(fragmented), considered,
        f"{len(fragmented)} of {considered} sessions had a median gap over "
        f"30 minutes between turns",
        "A session left open across meetings comes back to a model that has "
        "been holding stale context for hours, and to a you who has "
        "forgotten what it was told. Close it and reopen with a brief.",
        [f"{sid[:8]}  median gap {minutes:.0f} min across {turns} turns"
         for sid, minutes, turns in fragmented[:3]],
    )


def _session_drift(snap: Snapshot) -> Finding | None:
    by_session = snap.turns_by_session()
    considered = {sid: group for sid, group in by_session.items()
                  if len(group) >= 5}
    if len(considered) < 8:
        return None
    drifting = []
    for sid, group in considered.items():
        kinds = {_work_type(t.prompt) for t in group if t.prompt_len >= 15}
        kinds.discard("other")
        if len(kinds) >= 4:
            drifting.append((sid, sorted(kinds)))
    if len(drifting) / len(considered) <= 0.3:
        return None
    return Finding(
        "session-drift", "One session, four different jobs",
        "session hygiene", "medium",
        len(drifting), len(considered),
        f"{len(drifting)} of {len(considered)} sessions covered four or more "
        f"kinds of work",
        "Everything in a session is context for everything after it. A "
        "debugging thread that turns into a refactor carries the whole bug "
        "hunt into the refactor.",
        [f"{sid[:8]}  {', '.join(kinds)}" for sid, kinds in drifting[:3]],
    )


# ── Model and spend ──────────────────────────────────────────────────

BIG_PROMPT_TOKENS = 5_000
SLOW_CALL_MS = 60_000
VERBOSE_REPLY_TOKENS = 5_000


def _model_monoculture(snap: Snapshot) -> Finding | None:
    used: Counter = Counter()
    for work in snap.work.values():
        for model in work.models:
            used[model] += 1
    total = sum(used.values())
    if total < 30 or len(used) < 2:
        return None
    top, count = used.most_common(1)[0]
    if count / total <= 0.90:
        return None
    return Finding(
        "model-monoculture", "One model for everything", "model & spend",
        "medium", count, total,
        f"{top} handled {count} of {total} turns",
        "Reserve the expensive model for work that needs the reasoning. "
        "Lookups, renames and one-line fixes come back just as right from a "
        "cheaper one — 'cs cost' shows what the difference is worth.",
        [f"{name}  {n} turns" for name, n in used.most_common(4)],
    )


def _cache_starvation(snap: Snapshot) -> Finding | None:
    """Big prompts that were not cached are the most avoidable spend there is."""
    big = [w for w in snap.work.values() if w.input_tokens >= BIG_PROMPT_TOKENS]
    if len(big) < 20:
        return None
    cold = [w for w in big if w.cache_read < 0.2 * w.input_tokens]
    if len(cold) / len(big) <= 0.4:
        return None
    wasted = sum(w.input_tokens - w.cache_read for w in cold)
    return Finding(
        "cache-starvation", "Large prompts, sent cold", "model & spend",
        "medium", len(cold), len(big),
        f"{len(cold)} of {len(big)} large turns re-sent their context "
        f"uncached ({wasted / 1_000_000:.1f}M tokens)",
        "The prompt prefix is cached only while it stays identical. Editing "
        "instruction files mid-session, or reordering what is attached, "
        "throws the cache away and you pay full price for the same context.",
        [f"{w.input_tokens:,} in · {w.cache_read:,} from cache"
         for w in sorted(cold, key=lambda w: w.input_tokens, reverse=True)[:3]],
    )


def _reasoning_overuse(snap: Snapshot) -> Finding | None:
    efforts: Counter = Counter()
    for work in snap.work.values():
        efforts.update(work.efforts)
    total = sum(efforts.values())
    if total < 50:
        return None
    heavy = efforts["high"] + efforts["xhigh"] + efforts["max"]
    if heavy / total <= 0.6:
        return None
    return Finding(
        "reasoning-overuse", "Deep thinking, by default", "model & spend",
        "medium", heavy, total,
        f"{heavy} of {total} calls ran at high reasoning effort or above",
        "Reasoning tokens are billed and slow. They earn their place on "
        "design and debugging; on 'rename this' they are pure cost.",
        [f"{name}: {n} calls" for name, n in efforts.most_common(5)],
    )


def _premium_lookups(snap: Snapshot) -> Finding | None:
    lookups = [
        t for t in snap.turns
        if 10 < t.prompt_len < 120 and _LOOKUP.match(t.prompt.strip())
    ]
    if len(lookups) < 8:
        return None
    asked = sum(1 for t in snap.turns if t.prompt_len > 0)
    return Finding(
        "premium-lookups", "Asking the agent what a search engine knows",
        "model & spend", "low", len(lookups), asked,
        f"{len(lookups)} turns were plain lookup questions",
        "A lookup does not need a repository, a tool loop or a reasoning "
        "budget. It is the cheapest thing to move off the expensive path.",
        [_example(t) for t in lookups[:3]],
    )


def _verbose_output(snap: Snapshot) -> Finding | None:
    long_replies = [w for w in snap.work.values()
                    if w.output_tokens >= VERBOSE_REPLY_TOKENS]
    if len(long_replies) < 10 or not snap.work:
        return None
    if len(long_replies) / len(snap.work) <= 0.15:
        return None
    return Finding(
        "verbose-output", "Answers nobody read to the end", "model & spend",
        "low", len(long_replies), len(snap.work),
        f"{len(long_replies)} turns produced over {VERBOSE_REPLY_TOKENS:,} "
        f"tokens of reply",
        "Output tokens are the expensive half. Ask for the diff, the "
        "decision or the three lines that changed — not the essay around "
        "them.",
        [f"{w.output_tokens:,} tokens out"
         for w in sorted(long_replies, key=lambda w: w.output_tokens,
                         reverse=True)[:3]],
    )


def _slow_calls(snap: Snapshot) -> Finding | None:
    timed = [w for w in snap.work.values() if w.slowest_ms > 0]
    if len(timed) < 30:
        return None
    slow = [w for w in timed if w.slowest_ms >= SLOW_CALL_MS]
    if len(slow) / len(timed) <= 0.1:
        return None
    worst = sorted(slow, key=lambda w: w.slowest_ms, reverse=True)
    return Finding(
        "slow-calls", "Turns you waited a minute for", "model & spend", "low",
        len(slow), len(timed),
        f"{len(slow)} of {len(timed)} turns had a call take over "
        f"{SLOW_CALL_MS // 1000}s",
        "Usually a prompt carrying more context than the question needs. "
        "Narrowing what is attached is the fastest thing that makes a "
        "session feel faster.",
        [f"{w.slowest_ms / 1000:.0f}s · {w.input_tokens:,} input tokens"
         for w in worst[:3]],
    )


def _failed_calls(snap: Snapshot) -> Finding | None:
    endings: Counter = Counter()
    for work in snap.work.values():
        endings.update(work.endings)
    total = sum(endings.values())
    if total < 50:
        return None
    bad = endings["error"] + endings["content_filter"]
    if not bad:
        return None
    return Finding(
        "failed-calls", "Calls that ended badly", "model & spend", "medium",
        bad, total,
        f"{bad} of {total:,} calls ended in an error or a content filter",
        "Billed the same as any other call. A content filter usually means "
        "a credential or a licence header went up with the prompt — "
        "'cs audit' finds the first kind.",
        [f"{name}: {n}" for name, n in endings.most_common(4)],
    )


# ── Review habits ────────────────────────────────────────────────────

SPEED_ACCEPT_SEC = 20
RUNAWAY_STEPS = 25


def _speed_accept(snap: Snapshot) -> Finding | None:
    """Replying inside twenty seconds to a reply that changed files."""
    quick = []
    considered = 0
    for sid, group in snap.turns_by_session().items():
        for earlier, later in zip(group, group[1:], strict=False):
            if not (earlier.when and later.when):
                continue
            if not snap.files.get((sid, earlier.index)):
                continue
            considered += 1
            gap = (later.when - earlier.when).total_seconds()
            if 0 <= gap < SPEED_ACCEPT_SEC:
                quick.append((later, gap,
                              len(snap.files[(sid, earlier.index)])))
    if considered < 20 or len(quick) / considered <= 0.25:
        return None
    return Finding(
        "speed-accept", "Moving on before reading", "review habits", "high",
        len(quick), considered,
        f"{len(quick)} of {considered} file-changing turns were followed by "
        f"the next prompt within {SPEED_ACCEPT_SEC}s",
        "Nobody reads a diff that fast. This is the habit that puts the "
        "agent's mistakes into main — and it is the one worth breaking "
        "first.",
        [_example(turn, f"{gap:.0f}s after {files} files changed")
         for turn, gap, files in quick[:3]],
    )


def _unreviewed_bulk(snap: Snapshot) -> Finding | None:
    """Many files changed across very few turns: volume without conversation."""
    by_session = snap.turns_by_session()
    considered, bulk = 0, []
    for sid, group in by_session.items():
        touched = sum(len(snap.files.get((sid, t.index), ())) for t in group)
        if touched < 5:
            continue
        considered += 1
        if touched / len(group) >= 4:
            bulk.append((sid, touched, len(group)))
    if considered < 8 or len(bulk) / considered <= 0.3:
        return None
    bulk.sort(key=lambda row: row[1], reverse=True)
    return Finding(
        "unreviewed-bulk", "Volume without conversation", "review habits",
        "high", len(bulk), considered,
        f"{len(bulk)} of {considered} sessions changed four or more files per "
        f"turn",
        "Files-per-turn is how much you are agreeing to at once. Ask for one "
        "change, read it, then ask for the next — the total is the same and "
        "the reverts are not.",
        [f"{sid[:8]}  {touched} files across {turns} turns"
         for sid, touched, turns in bulk[:3]],
    )


def _runaway_turns(snap: Snapshot) -> Finding | None:
    stepped = [w for w in snap.work.values() if w.steps]
    if len(stepped) < 20:
        return None
    runaway = [w for w in stepped if w.steps >= RUNAWAY_STEPS]
    if len(runaway) / len(stepped) <= 0.1:
        return None
    return Finding(
        "runaway-turns", "Single turns that ran for ages", "review habits",
        "medium", len(runaway), len(stepped),
        f"{len(runaway)} turns took {RUNAWAY_STEPS}+ agent steps before "
        f"coming back",
        "A long tool loop is either real work or the agent circling. The "
        "difference is visible only if you look — 'cs yolo' shows which "
        "sessions run this way as a matter of course.",
        [f"{w.steps} steps · {w.calls} calls"
         for w in sorted(runaway, key=lambda w: w.steps, reverse=True)[:3]],
    )


def _single_repo(snap: Snapshot) -> Finding | None:
    repos = Counter(
        session["repo"] for session in snap.sessions.values() if session["repo"]
    )
    total = sum(repos.values())
    if total < 40 or len(repos) < 3:
        return None
    top, count = repos.most_common(1)[0]
    if count / total <= 0.9:
        return None
    return Finding(
        "single-repo", "One repository, everything else by hand",
        "review habits", "low", count, total,
        f"{count} of {total} attributed sessions were in {top}",
        "Nothing wrong with focus — but the repositories you never point the "
        "agent at are the ones where its help would be newest.",
        [f"{name}: {n} sessions" for name, n in repos.most_common(4)],
    )


RULES = (
    _thin_prompts, _repeated_prompts, _frustration, _no_constraints,
    _unstructured_openings,
    _mega_sessions, _one_shot_sessions, _late_night, _weekend, _broken_flow,
    _session_drift,
    _model_monoculture, _cache_starvation, _reasoning_overuse,
    _premium_lookups, _verbose_output, _slow_calls, _failed_calls,
    _speed_accept, _unreviewed_bulk, _runaway_turns, _single_repo,
)


def review(snap: Snapshot) -> tuple[list[Finding], dict[str, int]]:
    """Every rule that fired, worst first, and a score for each group.

    A group starts at 100 and each finding costs what its severity costs.
    That is the whole calculation — a score you can recompute from the list
    beneath it is a score worth printing.
    """
    findings = [found for rule in RULES if (found := rule(snap))]
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f.severity], -f.share))
    scores = {group: 100 for group in GROUPS}
    for found in findings:
        scores[found.group] = max(0, scores[found.group] - COST[found.severity])
    return findings, scores


# ── When the work happens ────────────────────────────────────────────

def rhythm(snap: Snapshot) -> dict:
    """The shape of the week: hours, days, streaks, and how long turns took.

    Separate from the rules because it is description, not judgement. Two
    people with identical histograms can be working perfectly well and
    heading for a wall respectively, and the report should not pretend to
    know which.
    """
    timed = snap.clock_turns()
    hours = Counter(t.when.hour for t in timed)
    weekdays = Counter(t.when.weekday() for t in timed)
    days = sorted({t.when.date() for t in timed})

    streak = longest = 0
    previous = None
    for day in days:
        streak = streak + 1 if previous and (day - previous).days == 1 else 1
        longest = max(longest, streak)
        previous = day

    durations = sorted(w.slowest_ms for w in snap.work.values() if w.slowest_ms)
    return {
        "turns": len(timed),
        "hours": hours,
        "weekdays": weekdays,
        "days_active": len(days),
        "span_days": (days[-1] - days[0]).days + 1 if days else 0,
        "longest_streak": longest,
        "late_night": sum(1 for t in timed if t.when.hour >= 22 or t.when.hour < 5),
        "weekend": sum(1 for t in timed if t.when.weekday() >= 5),
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "median_ms": statistics.median(durations) if durations else 0,
        "p90_ms": durations[int(len(durations) * 0.9)] if durations else 0,
        "busiest_day": max(
            Counter(t.when.date() for t in timed).items(),
            key=lambda kv: kv[1], default=(None, 0),
        ),
    }

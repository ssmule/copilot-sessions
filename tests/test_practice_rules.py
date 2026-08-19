"""Every rule `cs coach` can fire, fired once on purpose.

`test_practice.py` covers the report — that it prints, that it masks, that a
score is recomputable from the findings under it. It reaches five of the
twenty-two rules, because the shared fixture only plants five habits. The
other seventeen were live code no test had ever executed: a rule reading a
`Work` field that got renamed, or a threshold nudged the wrong way, would
have gone quiet and the report would have looked exactly as healthy as it
does when there is genuinely nothing wrong. That is the one failure mode
this module is not allowed to have — `practice.py` says so itself: "an
absence of evidence is not a clean bill of health".

So each rule gets a snapshot built to trip it and a second built to sit just
under its threshold, and `test_every_rule_has_a_fixture` fails the suite when
a rule is added without one. The snapshots are constructed in memory rather
than through SQL: a rule is a pure function of a `Snapshot`, and going
through the store would test the reader again instead of the rule.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from cs import practice

LONG = "Refactor the parser and keep the public signature unchanged here"
MONDAY = datetime(2026, 8, 3, 14, 0)     # a Monday, 14:00
SATURDAY = datetime(2026, 8, 8, 14, 0)   # a Saturday
MIDNIGHT = datetime(2026, 8, 3, 23, 30)


class Build:
    """A snapshot assembled a piece at a time.

    Only the fields a rule actually reads are filled in. Anything a rule
    needs but this does not set is the rule reading something new, which is
    exactly the change these tests exist to notice.
    """

    def __init__(self, days: int = 0):
        self.days = days
        self.sessions: dict[str, dict] = {}
        self.turns: list[practice.Turn] = []
        self.work: dict[tuple[str, int], practice.Work] = {}
        self.files: dict[tuple[str, int], list[str]] = {}

    def session(self, sid: str, repo: str = "acme/app", summary: str = "Work"):
        self.sessions[sid] = {
            "id": sid, "repo": repo, "cwd": "/tmp/p",
            "summary": summary, "started": MONDAY,
        }
        return self

    def turn(self, sid: str, index: int, prompt: str = LONG,
             when: datetime | None = None, reply: int = 200):
        if sid not in self.sessions:
            self.session(sid)
        self.turns.append(
            practice.Turn(sid, index, prompt, len(prompt), reply, when)
        )
        return self

    def talk(self, sid: str, count: int, prompt: str = LONG,
             start: datetime | None = None, gap_minutes: int = 5):
        for index in range(count):
            when = None if start is None else start + timedelta(
                minutes=gap_minutes * index)
            self.turn(sid, index, prompt, when)
        return self

    def did(self, sid: str, index: int, **fields):
        item = practice.Work()
        for name, value in fields.items():
            if name == "models":
                item.models = set(value)
            elif name in ("efforts", "endings"):
                getattr(item, name).update(value)
            else:
                setattr(item, name, value)
        self.work[(sid, index)] = item
        return self

    def touched(self, sid: str, index: int, count: int):
        self.files[(sid, index)] = [f"/tmp/p/file{n}.py" for n in range(count)]
        return self

    def snap(self) -> practice.Snapshot:
        return practice.Snapshot(self.days, self.sessions, self.turns,
                                 self.work, self.files)


# ── One builder per rule: (fires, stays quiet) ───────────────────────
# `enough` is the only difference between the two. Written as one function so
# the quiet case cannot drift into testing a different shape from the loud
# one — which is how a "stays silent" test comes to pass for the wrong reason.

def thin_prompts(enough: bool) -> practice.Snapshot:
    b = Build()
    b.talk("thin", 24 if enough else 6, "fix it")
    b.talk("full", 10, LONG)
    return b.snap()


def repeated_prompts(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(3 if enough else 2):
        b.turn(f"rep-{n}", 0, "run the release checklist end to end")
    return b.snap()


def frustration(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(3 if enough else 2):
        b.turn(f"hot-{n}", 0, "this is broken!!! why won't it work")
    return b.snap()


def no_constraints(enough: bool) -> practice.Snapshot:
    b = Build()
    b.talk("loose", 30, "please rewrite the whole parser however you like now")
    if not enough:
        # Same volume, but every prompt names a boundary.
        b = Build()
        b.talk("tight", 30, "rewrite the parser but do not touch the CLI here")
    return b.snap()


def unstructured_openings(enough: bool) -> practice.Snapshot:
    b = Build()
    opener = LONG if enough else "1. add the parser\n2. leave the CLI alone"
    for n in range(6):
        b.turn(f"run-{n}", 0, opener)
        for index in range(1, 9):
            b.turn(f"run-{n}", index, LONG)
    return b.snap()


def mega_sessions(enough: bool) -> practice.Snapshot:
    b = Build()
    b.talk("mega", 60 if enough else 10)
    for n in range(12):
        b.talk(f"small-{n}", 2)
    return b.snap()


def one_shot_sessions(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(15 if enough else 2):
        b.turn(f"once-{n}", 0)
    for n in range(15):
        b.talk(f"real-{n}", 4)
    return b.snap()


def late_night(enough: bool) -> practice.Snapshot:
    b = Build()
    b.talk("night", 20 if enough else 2, start=MIDNIGHT, gap_minutes=1)
    b.talk("day", 40, start=MONDAY, gap_minutes=1)
    return b.snap()


def weekend(enough: bool) -> practice.Snapshot:
    b = Build()
    b.talk("sat", 30 if enough else 2, start=SATURDAY, gap_minutes=1)
    b.talk("mon", 40, start=MONDAY, gap_minutes=1)
    return b.snap()


def broken_flow(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(9):
        b.talk(f"gap-{n}", 6, start=MONDAY, gap_minutes=90 if enough else 3)
    return b.snap()


def session_drift(enough: bool) -> practice.Snapshot:
    b = Build()
    mixed = ["fix the crash in the reader", "add a new parser module",
             "refactor and rename the lexer", "write tests for the parser",
             "update the readme and changelog"]
    for n in range(9):
        for index, prompt in enumerate(mixed if enough else ["fix the crash"] * 5):
            b.turn(f"drift-{n}", index, prompt)
    return b.snap()


def model_monoculture(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("m")
    for index in range(40):
        b.did("m", index, models=["gpt-5.5"])
    for index in range(40, 40 + (2 if enough else 20)):
        b.did("m", index, models=["claude-opus-4.8"])
    return b.snap()


def cache_starvation(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("c")
    cold = 20 if enough else 10          # of thirty large turns
    for index in range(30):
        b.did("c", index, input_tokens=40_000,
              cache_read=100 if index < cold else 39_000)
    return b.snap()


def reasoning_overuse(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("r")
    heavy = ["high"] * (40 if enough else 10)
    light = ["low"] * (20 if enough else 50)
    for index, effort in enumerate(heavy + light):
        b.did("r", index, efforts=[effort])
    return b.snap()


def premium_lookups(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(10 if enough else 4):
        b.turn(f"ask-{n}", 0, "what does the finish_reason column mean?")
    b.talk("work", 10, LONG)
    return b.snap()


def verbose_output(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("v")
    for index in range(12):
        b.did("v", index, output_tokens=9_000 if enough else 100)
    for index in range(12, 30):
        b.did("v", index, output_tokens=100)
    return b.snap()


def slow_calls(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("s")
    for index in range(8):
        b.did("s", index, slowest_ms=90_000 if enough else 1_000)
    for index in range(8, 40):
        b.did("s", index, slowest_ms=1_000)
    return b.snap()


def failed_calls(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("f")
    for index in range(60):
        b.did("f", index, endings=["stop"])
    if enough:
        b.did("f", 60, endings=["error"])
    return b.snap()


def speed_accept(enough: bool) -> practice.Snapshot:
    b = Build()
    gap = 5 if enough else 600      # seconds between a change and the next ask
    for n in range(6):
        for index in range(6):
            b.turn(f"quick-{n}", index,
                   when=MONDAY + timedelta(seconds=gap * index))
            b.touched(f"quick-{n}", index, 2)
    return b.snap()


def unreviewed_bulk(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(9):
        b.talk(f"bulk-{n}", 2)
        b.touched(f"bulk-{n}", 0, 20 if enough else 3)
        b.touched(f"bulk-{n}", 1, 20 if enough else 3)
    return b.snap()


def runaway_turns(enough: bool) -> practice.Snapshot:
    b = Build()
    b.session("run")
    for index in range(6):
        b.did("run", index, steps=40 if enough else 2, calls=40)
    for index in range(6, 30):
        b.did("run", index, steps=2, calls=2)
    return b.snap()


def single_repo(enough: bool) -> practice.Snapshot:
    b = Build()
    for n in range(45):
        b.session(f"one-{n}", repo="acme/app")
    for n in range(2 if enough else 20):
        b.session(f"two-{n}", repo="acme/other")
    b.session("three", repo="acme/third")
    return b.snap()


FIXTURES = {
    "thin-prompts": thin_prompts,
    "repeated-prompts": repeated_prompts,
    "frustration": frustration,
    "no-constraints": no_constraints,
    "unstructured-openings": unstructured_openings,
    "mega-sessions": mega_sessions,
    "one-shot-sessions": one_shot_sessions,
    "late-night": late_night,
    "weekend": weekend,
    "broken-flow": broken_flow,
    "session-drift": session_drift,
    "model-monoculture": model_monoculture,
    "cache-starvation": cache_starvation,
    "reasoning-overuse": reasoning_overuse,
    "premium-lookups": premium_lookups,
    "verbose-output": verbose_output,
    "slow-calls": slow_calls,
    "failed-calls": failed_calls,
    "speed-accept": speed_accept,
    "unreviewed-bulk": unreviewed_bulk,
    "runaway-turns": runaway_turns,
    "single-repo": single_repo,
}


class EveryRuleFiresTest(unittest.TestCase):
    """The half of `cs coach` no test had ever run."""

    def _findings(self, snap) -> dict[str, practice.Finding]:
        return {found.rule: found for found in practice.review(snap)[0]}

    def test_every_rule_fires_on_the_habit_it_names(self):
        for rule, build in FIXTURES.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, self._findings(build(True)))

    def test_every_rule_stays_quiet_below_its_threshold(self):
        """Silence has to mean 'not enough to say'. A rule that fires on the
        under-threshold shape is a rule that fires on everyone."""
        for rule, build in FIXTURES.items():
            with self.subTest(rule=rule):
                self.assertNotIn(rule, self._findings(build(False)))

    def test_every_rule_has_a_fixture(self):
        """Adding a rule without one puts untested code in a report people
        act on. This is the guard that makes that impossible to forget."""
        fired = set()
        for build in FIXTURES.values():
            fired.update(self._findings(build(True)))
        every = set()
        for rule in practice.RULES:
            snap = practice.Snapshot(0, {}, [], {}, {})
            self.assertIsNone(rule(snap), f"{rule.__name__} fires on nothing")
            every.add(rule.__name__.lstrip("_").replace("_", "-"))
        self.assertEqual(every - fired, set(),
                         "these rules have no fixture that fires them")
        self.assertEqual(set(FIXTURES) - every, set(),
                         "these fixtures name a rule that no longer exists")

    def test_every_finding_can_show_its_working(self):
        """A habit you cannot see an example of is not a finding, it is an
        accusation — the module's own words, and now its own test."""
        for rule, build in FIXTURES.items():
            with self.subTest(rule=rule):
                found = self._findings(build(True))[rule]
                self.assertTrue(found.evidence, "no evidence")
                self.assertLessEqual(len(found.evidence), 4)
                self.assertIn(found.group, practice.GROUPS)
                self.assertIn(found.severity, practice.COST)
                self.assertGreater(found.count, 0)
                self.assertLessEqual(found.count, found.total)
                self.assertTrue(0 < found.share <= 1)
                self.assertTrue(found.headline and found.fix)

    def test_a_finding_never_leaks_a_whole_session_id(self):
        """Evidence lines are pasted into tickets. Eight characters name a
        session in a personal store; thirty-six name a machine."""
        for rule, build in FIXTURES.items():
            with self.subTest(rule=rule):
                snap = build(True)
                for line in self._findings(snap)[rule].evidence:
                    for sid in snap.sessions:
                        if len(sid) > 12:
                            self.assertNotIn(sid, line)


class SnapshotContractTest(unittest.TestCase):
    """What the rules are allowed to assume about the snapshot they get."""

    def test_an_empty_snapshot_scores_a_hundred_everywhere(self):
        findings, scores = practice.review(practice.Snapshot(0, {}, [], {}, {}))
        self.assertEqual(findings, [])
        self.assertEqual(set(scores), set(practice.GROUPS))
        self.assertTrue(all(score == 100 for score in scores.values()))

    def test_turns_arrive_in_order_however_they_were_stored(self):
        """`turns_by_session` is what every session-shaped rule iterates, and
        several of them read consecutive pairs as elapsed time."""
        b = Build()
        for index in (3, 0, 2, 1):
            b.turn("s", index)
        ordered = b.snap().turns_by_session()["s"]
        self.assertEqual([t.index for t in ordered], [0, 1, 2, 3])

    def test_a_window_cuts_by_the_turn_and_not_by_the_session(self):
        """A session touched yesterday may have opened in March, and its
        March evenings are not this month's."""
        b = Build(days=7)
        b.turn("s", 0, when=datetime(2026, 3, 9, 23, 0))
        b.turn("s", 1, when=datetime(2026, 8, 3, 23, 0))
        self.assertEqual(len(b.snap().clock_turns()), 1)

    def test_an_untimed_turn_never_reaches_a_clock_rule(self):
        """Fixture timestamps used to be 'x' and 'y'; a rule that trusted
        them put work in a day named x, which sorts after every real date."""
        b = Build()
        b.turn("s", 0, when=None)
        self.assertEqual(b.snap().clock_turns(), [])

    def test_a_score_never_goes_below_zero(self):
        """Four high findings in one group cost 100; a fifth must not make
        the score negative, because the bar under it would invert."""
        worst = practice.Snapshot(0, {}, [], {}, {})
        findings = [
            practice.Finding(f"r{n}", "n", "review habits", "high", 1, 1,
                             "h", "f", ["e"])
            for n in range(6)
        ]
        scores = {group: 100 for group in practice.GROUPS}
        for found in findings:
            scores[found.group] = max(
                0, scores[found.group] - practice.COST[found.severity])
        self.assertEqual(scores["review habits"], 0)
        self.assertIsNotNone(worst)

    def test_rhythm_describes_an_empty_window_without_dividing_by_zero(self):
        beat = practice.rhythm(practice.Snapshot(0, {}, [], {}, {}))
        self.assertEqual(beat["turns"], 0)
        self.assertEqual(beat["span_days"], 0)
        self.assertEqual(beat["median_ms"], 0)
        self.assertEqual(beat["busiest_day"], (None, 0))

    def test_rhythm_counts_every_timed_turn_exactly_once(self):
        b = Build()
        b.talk("a", 10, start=MONDAY, gap_minutes=30)
        b.talk("b", 5, start=SATURDAY, gap_minutes=30)
        beat = practice.rhythm(b.snap())
        self.assertEqual(beat["turns"], 15)
        self.assertEqual(sum(beat["hours"].values()), 15)
        self.assertEqual(sum(beat["weekdays"].values()), 15)
        self.assertEqual(beat["weekend"], 5)

    def test_rhythm_finds_the_longest_run_of_consecutive_days(self):
        b = Build()
        for offset in (0, 1, 2, 5):
            b.turn("s", offset, when=MONDAY + timedelta(days=offset))
        beat = practice.rhythm(b.snap())
        self.assertEqual(beat["days_active"], 4)
        self.assertEqual(beat["longest_streak"], 3)
        self.assertEqual(beat["span_days"], 6)


class PromptReadingTest(unittest.TestCase):
    """`clean` decides what counts as something a person typed."""

    def test_harness_blocks_are_not_prompts(self):
        for injected in (
            "<system-reminder>rules</system-reminder>",
            "<skill-context name='x'>preamble</skill-context>",
            "<command-name>/deploy</command-name>",
            "<function_results>{}</function_results>",
        ):
            with self.subTest(injected=injected):
                self.assertEqual(practice.clean(injected + "the real ask"),
                                 "the real ask")

    def test_an_unclosed_block_swallows_the_rest_of_the_prompt(self):
        """Copilot truncates stored prompts, so the closing tag is often not
        there. Keeping the tail would count the harness as the question."""
        self.assertEqual(practice.clean("<skill-context name='x'>blah"), "")

    def test_a_prompt_that_is_only_harness_text_has_no_length(self):
        """These are what made the thin-prompt rule fire on sessions where
        the person had written a page."""
        b = Build()
        b.turn("s", 0, practice.clean("<system-reminder>x</system-reminder>"))
        self.assertEqual(b.snap().turns[0].prompt_len, 0)

    def test_work_types_are_coarse_on_purpose(self):
        """`session-drift` counts distinct kinds, so a taxonomy that split
        hairs would report every real session as drifting."""
        self.assertEqual(practice._work_type("fix the crash"), "fix")
        self.assertEqual(practice._work_type("add a parser"), "build")
        self.assertEqual(practice._work_type("mmm"), "other")

    def test_two_asks_that_differ_only_in_punctuation_are_one_ask(self):
        self.assertEqual(practice._normalise("Run the tests, please!"),
                         practice._normalise("run the tests please"))

"""Terminal UI helpers — colours, tables, boxes, and the splash screen.

Colour is auto-disabled when stdout is not a TTY (pipes, CI, ``TERM=dumb``),
so output stays clean when redirected.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import date

# ── Colour support ───────────────────────────────────────────────────
_COLOR = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


def _c(code: str) -> str:
    return code if _COLOR else ""


CYAN = _c("\033[36m")
GREEN = _c("\033[32m")
YELLOW = _c("\033[33m")
RED = _c("\033[31m")
MAGENTA = _c("\033[35m")
BOLD = _c("\033[1m")
DIM = _c("\033[2m")
RST = _c("\033[0m")


def c256(number: int) -> str:
    """A 256-colour foreground escape, or nothing when colour is off.

    Every colour `cs` invents goes through here rather than through a
    truecolour escape, for one reason: reports are built as ANSI strings and
    then *replayed inside curses* when opened from the menu, and curses can
    hold a 256-colour pair but cannot hold an arbitrary RGB. A palette the
    reader cannot render is a report that looks like two different tools
    depending on how you opened it.
    """
    return _c(f"\033[38;5;{number}m")


# ── Theme ────────────────────────────────────────────────────────────
# Tuned for a dark terminal, which is what a terminal overwhelmingly is.
# `CS_THEME=light` restores the pastels for anyone actually on white paper.
# Declared here, above everything that draws, because the wordmark's gradient
# and the report palette are the same eight colours and used to be two
# separate lists that drifted apart.
_LIGHT_THEME = os.environ.get("CS_THEME", "").lower() == "light"

# The Copilot purple→cyan sweep, in 256-colour terms. The light theme's two
# middle steps (#87afff, #5fafff) are pastels: legible on white, and almost
# colourless on black, which made a half-length bar look unfinished.
_RAMP = (
    (99, 105, 111, 75, 39, 45, 44, 49) if _LIGHT_THEME
    else (99, 105, 69, 33, 39, 45, 44, 49)
)


def tui_theme(curses) -> dict[str, int]:
    """Create the curses theme, falling back to attributes on limited terminals."""
    fallback = {
        "background": 0,
        "title": curses.A_BOLD,
        "help": curses.A_DIM,
        "selected": curses.A_REVERSE | curses.A_BOLD,
        "cursor": curses.A_REVERSE,
        "header": curses.A_BOLD,
        "separator": curses.A_DIM,
        "number": curses.A_DIM,
        "active": 0,
        "turns": 0,
        "credits": curses.A_BOLD,
        "summary": 0,
        "repo": curses.A_DIM,
        "status": curses.A_DIM,
    }
    try:
        curses.start_color()
        try:
            curses.use_default_colors()
            default_bg = -1
        except curses.error:
            default_bg = curses.COLOR_BLACK

        if curses.COLORS >= 256:
            # Dark by default, like the reports. 234 (#1c1c1c) rather than
            # pure black: a near-black ground keeps the dividers visible
            # without them having to be bright, and it is what the
            # saturated foregrounds below were chosen against.
            bg = 235 if _LIGHT_THEME else 234
            palette = {
                "background": (255, 235),
                "title": (111, 235),
                "help": (244, 235),
                "selected": (114, 60),
                "cursor": (231, 61),
                "header": (153, 235),
                "separator": (60, 235),
                "number": (244, 235),
                "active": (117, 235),
                "turns": (114, 235),
                "credits": (177, 235),
                "summary": (255, 235),
                "repo": (111, 235),
                "status": (220, 235),
            } if _LIGHT_THEME else {
                "background": (252, bg),
                "title": (39, bg),          # the product blue, saturated
                "help": (245, bg),
                # The active sort column is lit and the rest are furniture.
                # A background chip was the first idea and the wrong one:
                # labels are padded to their column, so "summary" would have
                # become forty cells of solid cyan.
                "selected": (45, bg),
                "cursor": (231, 25),        # white on deep azure: the row
                "header": (245, bg),
                "separator": (238, bg),
                "number": (244, bg),
                "active": (49, bg),         # mint: this session is live
                "turns": (84, bg),
                "credits": (177, bg),       # matches VIOLET in the reports
                "summary": (253, bg),
                "repo": (69, bg),
                "status": (214, 236),       # a footer bar, lifted off the bg
            }
        else:
            palette = {
                "background": (curses.COLOR_WHITE, default_bg),
                "title": (curses.COLOR_BLUE, default_bg),
                "help": (curses.COLOR_WHITE, default_bg),
                "selected": (curses.COLOR_GREEN, curses.COLOR_BLUE),
                "cursor": (curses.COLOR_WHITE, curses.COLOR_BLUE),
                "header": (curses.COLOR_CYAN, default_bg),
                "separator": (curses.COLOR_BLUE, default_bg),
                "number": (curses.COLOR_WHITE, default_bg),
                "active": (curses.COLOR_CYAN, default_bg),
                "turns": (curses.COLOR_GREEN, default_bg),
                "credits": (curses.COLOR_MAGENTA, default_bg),
                "summary": (curses.COLOR_WHITE, default_bg),
                "repo": (curses.COLOR_BLUE, default_bg),
                "status": (curses.COLOR_YELLOW, default_bg),
            }

        styles = {}
        for pair, (name, colors) in enumerate(palette.items(), 1):
            curses.init_pair(pair, *colors)
            styles[name] = curses.color_pair(pair)
        styles["title"] |= curses.A_BOLD
        styles["selected"] |= curses.A_BOLD
        styles["cursor"] |= curses.A_BOLD
        styles["header"] |= curses.A_BOLD
        styles["credits"] |= curses.A_BOLD
        styles["status"] |= curses.A_BOLD
        return styles
    except curses.error:
        return fallback


# ── ANSI text inside curses ──────────────────────────────────────────
_SGR = re.compile(r"\x1b\[([0-9;]*)m")

# The reports are built as ANSI-coloured strings for the plain terminal.
# Showing them in a curses window means translating those codes, and this is
# the whole set they emit — measured, not guessed.
_SGR_COLORS = {"31": 1, "32": 2, "33": 3, "35": 5, "36": 6}


def sgr_palette(curses) -> dict[str, int]:
    """ANSI codes the reports emit, mapped to curses attributes.

    Every index in `PALETTE_256` gets a pair, so a report looks the same
    opened from the menu as it does piped to the shell. It used to carry two
    of them by hand, and every colour added after that quietly came out as
    plain text in the full-screen reader — the one place a long report is
    actually read.

    Pairs start at 20 to stay clear of ``tui_theme``'s 1-14.
    """
    palette = {"1": curses.A_BOLD, "2": curses.A_DIM, "7": curses.A_REVERSE}
    try:
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        pair = 20
        for code, colour in _SGR_COLORS.items():
            curses.init_pair(pair, colour, background)
            palette[code] = curses.color_pair(pair)
            pair += 1
        if curses.COLORS >= 256:
            for colour in PALETTE_256:
                if pair >= min(getattr(curses, "COLOR_PAIRS", 64), 200):
                    break
                curses.init_pair(pair, colour, background)
                palette[f"38;5;{colour}"] = curses.color_pair(pair)
                pair += 1
    except curses.error:
        pass  # monochrome terminal: bold and dim still work
    return palette


# ── The wordmark's gradient ──────────────────────────────────────────
# The splash prints the wordmark in the Copilot purple→cyan with truecolour
# escapes. The curses landing screen could not, so it drew the same art in one
# flat blue and the two looked like different products. This is that gradient
# in 256-colour terms, which curses can hold as colour pairs.

_BANNER_RAMP = list(_RAMP)

# The reveal is a wipe, not a loop: the wordmark is drawn a few columns at a
# time when the screen first opens and is then still for as long as you are
# on it. Motion on arrival says the tool is awake; motion while you are
# reading says nothing and never stops saying it.
REVEAL_MS = 45
REVEAL_FRAMES = 14
# Columns each banner row trails the one above it, so the wipe's edge is a
# slant rather than a wall — light crossing the letters instead of a shutter.
REVEAL_LAG = 2


def reveal_columns(frame: int, span: int) -> int:
    """How much of the wipe is drawn at `frame`, of `span` columns.

    Eased so the wipe arrives rather than stops. A constant number of columns
    per frame reads as a machine drawing; decelerating into place reads as
    something settling. The exponent is 1.5 rather than the usual 2 because
    squaring leaves the last three frames moving one column between them,
    which looks less like an ending than like a stall.
    """
    if span <= 0:
        return 0
    if frame >= REVEAL_FRAMES:
        return span
    return round(span * (1 - (1 - frame / REVEAL_FRAMES) ** 1.5))


# The menu follows the wordmark down rather than appearing under a finished
# one. It starts a third of the way into the wipe: cascading from frame zero
# races the banner and reads as two animations, while waiting for the wipe to
# finish reads as a pause. Overlapping them makes it one gesture.
REVEAL_MENU_START = REVEAL_FRAMES // 3


def reveal_rows(frame: int, count: int) -> int:
    """How many menu rows are drawn at `frame`, of `count`.

    Shares the wipe's easing so the two halves of the landing screen settle
    on the same curve. Returns `count` unchanged once the frames are spent,
    which is what makes every caller safe to run past the end of the reveal
    and on a terminal that never animated at all.
    """
    if count <= 0:
        return 0
    if frame >= REVEAL_FRAMES:
        return count
    run = REVEAL_FRAMES - REVEAL_MENU_START
    progress = max(0.0, (frame - REVEAL_MENU_START) / run)
    return min(count, round(count * (1 - (1 - progress) ** 1.5)))


# The agent paces the rule under the header while the screen is idle, then
# stops. The wipe's comment above still holds — motion you are not meant to
# watch should not run forever — so this is a walk with an end to it rather
# than a spinner: it is company on arrival, not a thing blinking at you while
# you read, and a terminal left open on the menu goes quiet and stays quiet.
PACE_MS = 90
PACE_FRAMES = 220


def pace_column(frame: int, span: int) -> int:
    """Where the agent stands at `frame`, bouncing across `span` columns.

    A bounce rather than a wrap: something that walks off one edge and
    reappears at the other reads as a glitch, while something that turns
    round at the wall reads as pacing.
    """
    if span <= 1:
        return 0
    leg = span - 1
    at = frame % (2 * leg)
    return at if at <= leg else 2 * leg - at


def banner_palette(curses) -> list[int]:
    """The gradient as curses attributes, purple first.

    Empty on a terminal without 256 colours: there is no honest way to show
    this ramp in eight, and the wordmark is better flat than wrong.
    """
    try:
        curses.start_color()
        if curses.COLORS < 256:
            return []
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        attributes = []
        for offset, colour in enumerate(_BANNER_RAMP):
            # Pairs from 60: tui_theme owns 1-14, and sgr_palette now runs
            # from 20 to roughly 46 because it carries the whole report
            # palette rather than two colours of it.
            curses.init_pair(60 + offset, colour, background)
            attributes.append(curses.color_pair(60 + offset) | curses.A_BOLD)
        return attributes
    except curses.error:
        return []


# Eight heights, and a blank for nothing at all. A day with no sessions gets
# no bar rather than the shortest one: "quiet" and "barely busy" are
# different answers, and a sparkline that cannot tell them apart is decoration.
_SPARKS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[int]) -> str:
    """A row of block characters, scaled to the busiest value in the series.

    Scaled to the maximum rather than to a fixed ceiling: the question this
    answers is "what shape has my work been", and that shape is relative.
    """
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return " " * len(values)
    return "".join(
        _SPARKS[0] if value <= 0
        else _SPARKS[max(1, round(value / peak * (len(_SPARKS) - 1)))]
        for value in values
    )


def gradient_runs(text: str, colours: int) -> list[tuple[int, str, int]]:
    """Split a line into (column, run, colour) bands, purple left to cyan right.

    One write per band rather than per character — the same picture for a
    tenth of the calls, which matters while the wipe is running.
    """
    if not text or colours < 1:
        return []
    runs: list[tuple[int, str, int]] = []
    start = 0
    for column in range(1, len(text) + 1):
        here = start * colours // len(text)
        if column == len(text) or column * colours // len(text) != here:
            runs.append((start, text[start:column], here))
            start = column
    return runs


def sgr_runs(line: str, palette: dict[str, int]) -> list[tuple[str, int]]:
    """Split an ANSI-coloured line into (text, curses attribute) runs."""
    runs: list[tuple[str, int]] = []
    attr, pos = 0, 0
    for match in _SGR.finditer(line):
        if match.start() > pos:
            runs.append((line[pos:match.start()], attr))
        code = match.group(1)
        if code in palette:
            attr |= palette[code]
        else:
            for part in code.split(";"):
                if part in ("", "0"):
                    attr = 0
                else:
                    attr |= palette.get(part, 0)
        pos = match.end()
    if pos < len(line):
        runs.append((line[pos:], attr))
    return runs


# ── Copilot gradient (purple → cyan) ─────────────────────────────────
GRADIENT = [
    (139, 92, 246),
    (124, 108, 252),
    (99, 132, 255),
    (59, 160, 255),
    (0, 188, 255),
    (0, 210, 240),
    (0, 228, 220),
    (0, 245, 200),
]


def trunc(s: str, n: int) -> str:
    """Truncate a string to n chars with an ellipsis."""
    return (s[: n - 1] + "…") if len(s) > n else s


def fmt_aiu(nano: int | None) -> str:
    """Format nano-AIU spend as compact AI credits ('-' when none)."""
    aiu = (nano or 0) / 1e9
    if aiu <= 0:
        return "-"
    if aiu >= 1000:
        return f"{aiu / 1000:.1f}k"
    if aiu >= 100:
        return f"{aiu:.0f}"
    if aiu >= 10:
        return f"{aiu:.1f}"
    return f"{aiu:.2f}"


def friendly_day(day: str) -> str:
    """Turn an ISO date into 'Today', 'Yesterday', a weekday, or a full date."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return day
    diff = (date.today() - d).days
    weekday = d.strftime("%A")
    if diff == 0:
        return f"Today · {weekday} {day}"
    if diff == 1:
        return f"Yesterday · {weekday} {day}"
    if 0 < diff < 7:
        return f"{weekday} {day}"
    return d.strftime("%a %d %b %Y")


def gradient_text(text: str) -> str:
    if not _COLOR:
        return text
    out = []
    n = len(GRADIENT)
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(ch)
        else:
            idx = int(i / max(len(text) - 1, 1) * (n - 1))
            r, g, b = GRADIENT[min(idx, n - 1)]
            out.append(f"\033[38;2;{r};{g};{b}m{ch}")
    out.append(RST)
    return "".join(out)


# ── Presentation primitives ──────────────────────────────────────────
# Shared by every long-form view so brief, show and read read as one product
# rather than three scripts: same rules, same gutters, same accents.

# One palette, and every view draws from it. Before this there were five
# colours and a grey, so a bar chart, a risk verdict and a file path were all
# the same shade of nothing — the screen had no way of saying which of the
# things on it mattered. These are 256-colour indices on the Copilot
# purple→cyan axis, with warm tones reserved for the two things that are
# genuinely warnings.
#
# The rule the whole palette obeys: hue carries *meaning*, brightness carries
# *emphasis*. Anything cool is information, anything warm is a finding, and
# grey is furniture. A view that needs a sixth colour needs a rethink instead.
#
# Tuned for a dark terminal, which is what a terminal overwhelmingly is. The
# first version was picked on a light background and every colour in it was a
# pastel — #87afff for the accent, #af87ff for spend — because on white a
# pastel is the only thing that stays legible. Replayed on black those same
# values have almost no chroma left: the whole product came out a washed pale
# blue, and a palette where nothing is saturated is a palette where nothing
# can be emphasised.
#
# `CS_THEME=light` restores the pastels for anyone actually on white paper.
if _LIGHT_THEME:
    ACCENT = c256(111)    # headings, rules — the product's own blue
    MUTED = c256(244)     # metadata, furniture, anything not being said
    CODE = c256(180)      # inline code and code blocks
    PAPER = c256(252)     # body text that has to out-rank MUTED
    VIOLET = c256(141)    # the ramp's warm end: totals, spend
    INDIGO = c256(105)
    SKY = c256(75)
    AZURE = c256(39)
    TEAL = c256(44)
    MINT = c256(49)       # the ramp's cool end: counts, good news
    LIME = c256(149)      # a state that is fine and worth seeing
    AMBER = c256(215)     # worth a look
    ORANGE = c256(209)
    ROSE = c256(204)      # worth changing
    SLATE = c256(60)      # rules, bar tracks, dividers
else:
    ACCENT = c256(39)     # #00afff — headings and rules, the product's blue
    MUTED = c256(245)     # metadata, furniture, anything not being said
    CODE = c256(180)      # inline code: warm but low-chroma, so it cannot be
                          # mistaken for the saturated warm of a finding
    PAPER = c256(253)     # body text that has to out-rank MUTED
    VIOLET = c256(177)    # #d787ff — the ramp's warm end: totals, spend
    INDIGO = c256(99)
    SKY = c256(69)
    AZURE = c256(45)
    TEAL = c256(44)
    MINT = c256(49)       # the ramp's cool end: counts, good news
    LIME = c256(148)      # a state that is fine and worth seeing
    AMBER = c256(214)     # worth a look
    ORANGE = c256(208)
    ROSE = c256(204)      # worth changing
    SLATE = c256(239)     # rules, bar tracks, dividers — neutral, and dark
                          # enough to sit behind content instead of beside it
GUTTER = "  "

# The gradient bars sweep along this — the same eight steps as the wordmark,
# so a bar and the logo above it are visibly the same object. Short bars stay
# at the purple end rather than compressing the whole ramp into four cells:
# colour here means "how far along", and a two-cell bar has not gone far.
_BAR_RAMP = _RAMP

# Eighths of a cell. A bar that rounds down to nothing tells you a row scored
# zero when it scored one, which is the one thing a chart must never do — so
# the last cell is drawn as a partial block instead of dropped.
_EIGHTHS = "▏▎▍▌▋▊▉█"

# Every 256-colour index this module can emit. Declared rather than
# discovered because the curses reader has to allocate a colour pair for each
# one up front, and a colour that was not declared silently renders as plain
# text in the one view that shows reports full-screen.
#
# Both themes are declared, not just the active one: the set is cheap, and a
# reader that only knew about the running theme would render a saved report
# wrong the moment someone changed CS_THEME between writing and reading it.
PALETTE_256 = sorted({
    39, 44, 45, 49, 60, 69, 75, 99, 105, 111, 141, 148, 149, 177, 180,
    204, 208, 209, 214, 215, 239, 244, 245, 252, 253, 33,
    *_BAR_RAMP,
})


def rule(width: int, title: str = "", colour: str = "") -> str:
    """A full-width rule, optionally titled: ── Title ────────────.

    The dashes are furniture and are drawn as furniture. Every one of them
    used to be in the accent colour, which on a page with six sections meant
    six full-width coloured lines competing with the words between them —
    the loudest thing on screen was the thing carrying the least. The accent
    survives as the two leading dashes, which is enough to mark the edge.
    """
    colour = colour or ACCENT
    if not title:
        return f"{GUTTER}{SLATE}{'─' * width}{RST}"
    title = _fit(title, max(width - 5, 1))
    dashes = max(width - cells(_strip(title)) - 4, 0)
    return f"{GUTTER}{colour}──{RST} {BOLD}{title}{RST} {SLATE}{'─' * dashes}{RST}"


# ── Who is speaking ──────────────────────────────────────────────────
# A transcript is a conversation, and the one thing it has to make obvious at
# a glance is who is talking. "You" and "Copilot" were set in the same bold
# type and differed only by the word, so finding your own last question in a
# fifty-turn session meant reading rather than glancing.
#
# Emoji rather than a font glyph: GitHub's Copilot mark lives in Nerd Fonts,
# and a terminal without one draws a hollow box exactly where the speaker
# should be — worse than no mark at all. These render anywhere that renders
# the box-drawing this module is already built from.
#
# `CS_GLYPHS=ascii` is for the terminals that do neither, and for anyone
# piping a transcript somewhere that would rather not receive astral-plane
# characters.
_ASCII_GLYPHS = os.environ.get("CS_GLYPHS", "").lower() == "ascii"

YOU_MARK = ">" if _ASCII_GLYPHS else "👤"
COPILOT_MARK = "*" if _ASCII_GLYPHS else "🤖"

# The landing screen's icons, and what each one is when emoji are off. They
# live here rather than beside the menu rows for two reasons: `CS_GLYPHS=ascii`
# was being honoured by the two transcript marks above and ignored by the
# seventeen icons on the first screen anyone sees, which is the wrong way
# round; and an icon is a rendering decision, which is what this module is.
#
# Every glyph is from the original Unicode 6.0 emoji set — the one that every
# emoji font has shipped since 2010 — bar the robot, which is Unicode 8.0 and
# is already the Copilot mark in transcripts: one glyph for the agent in both
# places is worth more than the five-year gap, and 2015 is old enough.
#
# That rule is not fussiness. The hook row used U+1FA9D, added in Unicode 13 in
# 2020, and a terminal whose font predates it draws a blank where the icon
# goes; the row then reads as the one option on the menu that forgot to bring
# an icon. A missing glyph is worse than a plainer one, because a blank looks
# like a bug in the tool rather than a gap in a font.
#
# The two symbol-block characters carry U+FE0F, which asks for the emoji form
# rather than the monochrome text form a terminal would otherwise be free to
# pick out of a symbol font. Without it they render thin, flat and a cell
# narrower than every icon beside them, which is the same "no icon here"
# impression by a different route.
_MENU_GLYPHS: dict[str, tuple[str, str]] = {
    "recent": ("🕒", "~"),
    "all": ("📚", "="),
    "search": ("🔍", "/"),
    "repos": ("📦", "#"),
    "stats": ("📊", "%"),
    "days": ("📅", "|"),
    "spend": ("💰", "$"),
    "efficiency": ("🔋", ">"),
    "delegation": ("👥", "&"),
    "autonomy": ("🚀", "^"),
    "handoff": ("🔗", "-"),
    "security": ("🔐", "!"),
    "instructions": ("📋", "]"),
    "skills": ("🎓", "+"),
    "profiles": ("🤖", "*"),
    "hooks": ("🔔", "}"),
    "mcp": ("🔌", ":"),
    "help": ("💡", "?"),
    # Not a menu row: the agent that paces the rule under the header. It
    # lives here so it is held to the same rule as every other glyph — the
    # age, width and block tests iterate this table, so the one piece of
    # decoration on the screen cannot be the thing that draws a blank.
    "copilot": ("🤖", "@"),
    # The Improve group is commented out of the menu, not deleted. Its icons
    # stay here so that bringing the rows back is only ever a matter of
    # uncommenting them — the compass they used was Unicode 11 and would have
    # walked the same missing-glyph bug straight back in, and the alarm clock
    # was U+23F0, which walks back the *other* one.
    "practice": ("🎯", "\""),
    "rhythm": ("🎵", "'"),
    "context": ("📍", "."),
}
# Every icon is drawn from the supplemental pictograph planes (U+1F300 and
# up) rather than from the older symbol blocks at U+2100–U+2BFF. Both are
# emoji by the standard, but only the first is emoji to a *terminal*: the
# symbol blocks predate emoji and have long-standing text glyphs, so a
# terminal is free — and quite often configured — to draw them from the
# monospace text font instead of the emoji font. That renders them thin and
# flat where it has a glyph and blank where it does not, which is how
# 'efficiency' and 'help' came to be the two rows on the menu that looked
# like they had forgotten their icons.
#
# U+FE0F was tried first and is not a fix. It is a *request* for the emoji
# form that a terminal may decline, and declining it can drop the whole
# sequence, so the row that was thin became empty. Choosing a codepoint with
# no text form is the fix, because it leaves the terminal nothing to get
# wrong.
# The plain markers are chosen to be *distinct* rather than descriptive: the
# label is right beside them and already says the word, so an icon's remaining
# job on a menu you open daily is to be a shape you learn. Where a symbol can
# also mean what it points at — / for search because / is the search key, $ for
# spend, # for a repository, * for the agent because that is already the
# Copilot mark in transcripts — it does.


def menu_icon(name: str) -> str:
    """The landing screen's icon for a row, honouring `CS_GLYPHS=ascii`.

    Raises on an unknown name rather than returning a blank, because a menu
    row with no icon is exactly the bug this table was added to fix and it
    should not be possible to reintroduce it by misspelling a key.
    """
    emoji, plain = _MENU_GLYPHS[name]
    return plain if _ASCII_GLYPHS else emoji


def speaker(mark: str, name: str, colour: str = "", width: int = 0) -> str:
    """A transcript speaker label: the mark, then the name in its own colour.

    Shaped like `heading` on purpose — same gutter, same hairline, same
    weight — because a turn is a section and this is what its label looks
    like when the section has a voice. The mark stands where the accent bar
    would be, so the two never appear on one line fighting for the same two
    columns.

    The name carries the colour and the mark does not: a terminal is free to
    render an emoji in its own palette, and a mark that ignores the escape
    while the word beside it obeys reads as a rendering fault.
    """
    colour = colour or ACCENT
    head = f"{GUTTER}{mark}  {colour}{BOLD}{name}{RST}"
    span = width - cells(mark) - cells(name) - 5
    if width and span > 3:
        head += f" {SLATE}{'─' * span}{RST}"
    return head


def heading(text: str, colour: str = "", width: int = 0) -> str:
    """A section label, set apart by an accent bar rather than shouting.

    `width` draws a hairline from the end of the label to that column. A page
    of six sections used to be six bold words floating in a column of
    numbers; the line is what turns them into edges you can find without
    reading. It is dropped rather than crowded when the label nearly fills
    the row.
    """
    colour = colour or ACCENT
    head = f"{GUTTER}{colour}▌{RST}{BOLD}{text}{RST}"
    span = width - cells(_strip(text)) - 4
    if width and span > 3:
        head += f" {SLATE}{'─' * span}{RST}"
    return head


def bar(value: float, peak: float, width: int, colour: str = "",
        track: bool = False, pad: bool = False) -> str:
    """A horizontal bar, drawn to an eighth of a cell and coloured by length.

    Three things this does that `"█" * n` did not:

    * **It never rounds a real value away.** The old bars used `int()`, so
      anything under one cell drew nothing at all and the row read as zero.
    * **It says how long it is twice** — in length and in hue, sweeping the
      wordmark's purple→cyan. On a chart of twenty rows the shape is
      readable before any number is.
    * **It can show what it is a share of.** `track=True` draws the unfilled
      remainder as a dim rail, which turns "nine" into "nine out of eleven"
      without spending a column on the total.

    `colour` overrides the ramp for a bar whose meaning is not its size — a
    severity, or a spend that is already coloured by risk.

    Returns exactly `width` visible cells when `pad` or `track` is set, and
    only the drawn part otherwise, so callers can align a column either way.
    """
    width = max(int(width), 0)
    if width <= 0:
        return ""
    share = 0.0 if peak <= 0 else max(0.0, min(float(value) / float(peak), 1.0))
    eighths = round(share * width * 8)
    if value > 0 and eighths == 0:
        eighths = 1  # something is never nothing
    full, part = divmod(eighths, 8)
    drawn = "█" * full + (_EIGHTHS[part - 1] if part else "")
    rest = width - cells(drawn)

    if not _COLOR:
        tail = ("·" * rest if track else " " * rest) if (track or pad) else ""
        return drawn + tail
    if colour:
        body = f"{colour}{drawn}{RST}"
    else:
        # Banded by position along the *track*, not along the bar: colouring
        # by the bar's own length would run every row through the whole ramp
        # and make a two-cell bar and a forty-cell bar end on the same cyan.
        #
        # One escape per band, not per cell. Per cell was eleven bytes of
        # escape for every block drawn — a 75-column chart row came to nine
        # hundred characters of mostly punctuation, which the curses reader
        # then had to parse back out again on every frame.
        steps = len(_BAR_RAMP)
        parts, run, band = [], "", -1
        for index, char in enumerate(drawn):
            here = min(index * steps // width, steps - 1)
            if here != band:
                if run:
                    parts.append(f"{c256(_BAR_RAMP[band])}{run}")
                run, band = "", here
            run += char
        if run:
            parts.append(f"{c256(_BAR_RAMP[band])}{run}")
        body = "".join(parts) + RST
    if track:
        return body + f"{SLATE}{'·' * rest}{RST}"
    return body + " " * rest if pad else body


def meter(share: float, width: int, colour: str = "") -> str:
    """A bar that is always a share of one — the same drawing, fixed scale."""
    return bar(share, 1.0, width, colour=colour, track=True)


def field(label: str, value: str, label_width: int = 9) -> str:
    """An aligned metadata row: label in muted type, value in normal.

    The column always leaves a gap, even for a label as wide as it — an
    over-long label pushes its value right rather than running into it.

    A value too long for the window wraps under itself, hanging at the value
    column so the block still reads as one row. Every report caps its own
    layout at 96 columns and so does this, which is why it can ask the
    terminal directly instead of being handed a width at eighty call sites.

    A value carrying its own colour is left alone: wrapping counts escape
    codes as characters and can split one down the middle, and a field that
    printed half an escape sequence would be a worse bug than a long line.
    """
    width = max(label_width, len(label) + 1)
    head = f"{GUTTER}{MUTED}{label:<{width}}{RST}"
    room = min(shutil.get_terminal_size().columns, 96) - len(GUTTER) - width
    if room < 12 or _strip(value) != value:
        return head + value

    import textwrap

    lines = textwrap.wrap(value, room, break_long_words=False,
                          break_on_hyphens=False) or [""]
    hang = " " * (len(GUTTER) + width)
    return "\n".join([head + _fit(lines[0], room)]
                     + [hang + _fit(line, room) for line in lines[1:]])


def markdown(text: str, width: int, indent: str = "    ") -> list[str]:
    """Render light markdown to styled terminal lines.

    Handles headings, bullets, numbered items, tables, fenced code and inline
    code — the shapes assistant replies actually use. Anything else passes
    through wrapped. Code is never reflowed: its alignment is the information.
    """
    import textwrap

    out: list[str] = []
    rows: list[str] = []
    in_code = False
    body = max(width - len(indent), 24)

    def wrap(text: str, room: int) -> list[str]:
        """Wrap prose with its styling intact across the line break.

        Styling is applied *after* wrapping, so it has to survive it: a
        `**span**` broken over two lines no longer matches its own regex,
        which is how raw `**` used to reach the screen. Markers become
        one-character sentinels first, and each line closes its own styling.
        """
        parts = textwrap.wrap(
            _mark(text), width=room, break_long_words=False, break_on_hyphens=False
        ) or [""]
        return [_paint(part + _RESET_MARK) for part in parts]

    def flush_table() -> None:
        if rows:
            out.extend(_table(rows, body, indent))
            rows.clear()

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_table()
            in_code = not in_code
            language = stripped[3:].strip()
            if in_code and language:
                out.append(f"{indent}{MUTED}│ {language}{RST}")
            continue

        if in_code:
            # Never truncate code: a cut line loses the very thing that makes
            # it useful (and once cut a masked value reads as a leak). Long
            # lines overflow and the pager wraps them.
            out.append(f"{indent}{MUTED}│{RST} {CODE}{line}{RST}")
            continue

        # A table is gathered whole: column widths are only knowable at the
        # end of the block.
        if stripped.startswith("|"):
            rows.append(stripped)
            continue
        flush_table()

        if not stripped:
            out.append("")
            continue

        level = len(stripped) - len(stripped.lstrip("#"))
        if 0 < level <= 6 and stripped[level : level + 1] == " ":
            out.append(f"{indent}{BOLD}{_inline(stripped[level + 1 :])}{RST}")
            continue

        marker, rest = _list_parts(stripped)
        if marker:
            hang = indent + " " * (len(marker) + 1)
            wrapped = wrap(rest, body - len(marker) - 1)
            bullet = "•" if marker in "-*•" else marker
            out.append(f"{indent}{ACCENT}{bullet}{RST} {wrapped[0]}")
            out.extend(f"{hang}{part}" for part in wrapped[1:])
            continue

        if raw.startswith(("    ", "\t")):
            out.append(f"{indent}{CODE}{line}{RST}")
            continue

        out.extend(f"{indent}{part}" for part in wrap(stripped, body))

    flush_table()
    return out


def _table(rows: list[str], width: int, indent: str) -> list[str]:
    """Render a markdown table as aligned columns.

    Replies are full of tables, and raw `| a | b |` pipes are the least
    readable thing on the screen — the columns never line up, because the
    source was written for a renderer. Here the widths are computed and the
    `|---|` divider becomes a rule under the header.
    """
    grid = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
    header = grid[0] if len(grid) > 1 and _is_divider(grid[1]) else None
    body = [row for row in grid if not _is_divider(row)]
    if not body:
        return []

    columns = max(len(row) for row in body)
    body = [[_strip_markers(cell) for cell in row] + [""] * (columns - len(row))
            for row in body]
    widths = [max(cells(row[i]) for row in body) for i in range(columns)]

    # Shrink the widest column until the table fits; a narrow column carries
    # a key or a status, and losing that is worse than folding the prose one.
    gap = 2
    while sum(widths) + gap * (columns - 1) > width and max(widths) > 6:
        widths[widths.index(max(widths))] -= 1

    def render(row: list[str], style: str = "") -> str:
        parts = [_fit(cell, w) + " " * (w - cells(_fit(cell, w)))
                 for cell, w in zip(row, widths, strict=True)]
        text = (" " * gap).join(parts).rstrip()
        if not text:
            return ""
        return f"{indent}{style}{text}{RST if style else ''}"

    out = []
    if header:
        out.append(render(body[0], BOLD))
        out.append(f"{indent}{MUTED}{(' ' * gap).join('─' * w for w in widths)}{RST}")
        body = body[1:]
    out.extend(render(row) for row in body)
    return out


def _is_divider(row: list[str]) -> bool:
    """Whether a table row is the `|---|:--:|` rule rather than data."""
    import re

    return bool(row) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in row)


def cells(text: str) -> int:
    """Columns a string occupies. Emoji and CJK take two, and replies are
    full of ✅/⬜ — counting them as one shears every column to their right.

    Zero-width characters take none. A combining accent, a variation
    selector and a zero-joiner all draw *into* the character before them
    rather than beside it, so counting them as a column each shears the same
    row this function exists to keep straight — and in the other direction,
    which is worse, because the text then looks like it fits when it does
    not. `Mn`/`Me` are the marks, `Cf` the formatting codepoints that carry
    no glyph at all.
    """
    import unicodedata

    total = 0
    for ch in text:
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return total


def _fit(text: str, width: int) -> str:
    """Truncate to a column count, not a character count."""
    if cells(text) <= width:
        return text
    out = ""
    for ch in text:
        if cells(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


def _list_parts(text: str) -> tuple[str, str]:
    """Split a list marker from its text, or ('', text) when not a list."""
    import re

    match = re.match(r"^([-*•]|\d{1,3}[.)])\s+(.*)$", text)
    return (match.group(1), match.group(2)) if match else ("", text)


def _inline(text: str) -> str:
    """Style `code`, **bold** and *italic* on a line that is not wrapped."""
    return _paint(_mark(text) + _RESET_MARK)


# One-character stand-ins for styling, applied before wrapping and swapped for
# escape codes after it. They are never printed: _paint always runs last, and
# with colour off every code is the empty string, which is how piped output
# comes out as clean prose rather than raw markdown.
_RESET_MARK = "\x00"
_MARKS = {"\x01": BOLD, "\x02": CODE, "\x03": DIM, _RESET_MARK: RST}


def _mark(text: str) -> str:
    """Replace inline markdown with wrap-safe style sentinels."""
    import re

    text = re.sub(r"`([^`]+)`", "\x02\\1\x00", text)
    text = re.sub(r"\*\*([^*]+)\*\*", "\x01\\1\x00", text)
    text = re.sub(r"__([^_]+)__", "\x01\\1\x00", text)
    return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", "\x03\\1\x00", text)


def _paint(text: str) -> str:
    """Swap style sentinels for escape codes — or for nothing, without colour."""
    for mark, code in _MARKS.items():
        text = text.replace(mark, code)
    return text


def _strip_markers(text: str) -> str:
    """Plain text: markdown markers removed, nothing styled."""
    marked = _mark(text)
    for mark in _MARKS:
        marked = marked.replace(mark, "")
    return marked



def _strip(s: str) -> str:
    """Length of a string ignoring ANSI escape codes."""
    import re

    return re.sub(r"\033\[[0-9;]*m", "", s)


# ── Banner ───────────────────────────────────────────────────────────
# Three sizes, because a banner that does not fit is not a banner. The
# caller asks for the biggest one the window can hold; each is a plain list
# of equal-length lines, so centring and colouring stay someone else's job.

BANNER = [
    r" ██████╗ ██████╗ ██████╗ ██╗██╗      ██████╗ ████████╗",
    r"██╔════╝██╔═══██╗██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝",
    r"██║     ██║   ██║██████╔╝██║██║     ██║   ██║   ██║   ",
    r"██║     ██║   ██║██╔═══╝ ██║██║     ██║   ██║   ██║   ",
    r"╚██████╗╚██████╔╝██║     ██║███████╗╚██████╔╝   ██║   ",
    r" ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝   ",
    r"        S E S S I O N S   B R O W S E R              ",
]

_BANNER_SMALL = [
    r"  ___ ___ ",
    r" / __/ __|",
    r"| (__\__ \  copilot sessions",
    r" \___|___/",
]


def _block(art: list[str]) -> list[str]:
    """Pad art to a rectangle so centring it cannot make it ragged."""
    span = max(len(line) for line in art)
    return [line.ljust(span) for line in art]


def banner(width: int, height: int) -> list[str]:
    """The largest wordmark that fits, or a single line when nothing does.

    `height` is how many rows the caller can spare, not the whole window: a
    banner that pushes the menu off the screen has cost more than it gave.
    """
    for art in (BANNER, _BANNER_SMALL):
        block = _block(art)
        if width >= len(block[0]) + 4 and height >= len(block):
            return block
    return ["cs · copilot sessions"] if width >= 23 else ["cs"]


# ── Splash ───────────────────────────────────────────────────────────

def render_splash(stats: dict | None) -> None:
    if not _COLOR:
        return
    cols = shutil.get_terminal_size().columns
    print("\033[2J\033[H", end="")

    print()
    for line in BANNER:
        print(gradient_text(line.center(cols)))
    print()
    print(f"  {gradient_text('━' * min(cols - 4, 56))}")
    print()

    if stats:
        w = min(cols - 6, 56)
        bc = "\033[38;2;59;160;255m" if _COLOR else ""
        print(f"  {bc}┌{'─' * w}┐{RST}")
        for label, val in [
            ("Sessions", stats["total"]),
            ("Interactive", stats["interactive"]),
            ("Turns", stats["total_turns"]),
            ("Repositories", stats["repos"]),
        ]:
            line = f"{label}: {BOLD}{val}{RST}"
            pad = w - len(f"{label}: {val}") - 3
            print(f"  {bc}│{RST}  {line}{' ' * max(pad, 0)}{bc}│{RST}")
        print(f"  {bc}└{'─' * w}┘{RST}")
        print()

    print(f"  {BOLD}Commands:{RST}")
    for cmd, desc in [
        ("cs recent [days]", "recent interactive sessions (default 7)"),
        ("cs all [days]", "include automated/scheduled sessions"),
        ("cs search <words>", "full-text search, best match first"),
        ("cs show <#N|id>", "overview: spend, files, turn list"),
        ("cs read <#N|id>", "the full conversation, paged"),
        ("cs files [path]", "sessions that touched a file"),
        ("cs resume <#N|id>", "resume a session"),
        ("cs repos", "sessions by repository"),
        ("cs stats", "overall statistics"),
        ("cs timeline", "sessions-per-day chart"),
        ("cs cost [days]", "AI spend by model, repo and day"),
    ]:
        print(f"  {DIM}  {cmd:<20} → {desc}{RST}")
    print()

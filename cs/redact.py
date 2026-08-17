"""Mask credentials before session text reaches the screen.

Sessions record whatever was typed or echoed — keys, tokens, connection
strings, private keys. `cs` only ever *displays* that text, so masking happens
at the render edge: the store is never modified, and nothing here can lose
data. What it can do is keep a secret out of your scrollback, out of a
screen-share, and out of anything you paste from a terminal.

Set ``CS_REDACT=0`` to see the raw text (it is your own machine, and sometimes
you genuinely need the value back).
"""

from __future__ import annotations

import os
import re
from base64 import b64decode
from binascii import Error as Base64Error

MASK = "[redacted]"


def _mask(kind: str) -> str:
    return f"[redacted:{kind}]"


# Each rule masks group 'secret' when present, else the whole match. Ordered:
# the most specific shapes run first so a key is labelled by what it is.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    # A private key is the whole block, header to footer.
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
    ),
    # A PGP secret key is its own block, and does not say "PRIVATE KEY-----".
    (
        "pgp-private-key",
        re.compile(
            r"-----BEGIN PGP PRIVATE KEY BLOCK-----.*?"
            r"-----END PGP PRIVATE KEY BLOCK-----",
            re.S,
        ),
    ),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}")),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{12,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # The path *is* the credential: anyone with the URL can post as the app.
    (
        "slack-webhook",
        re.compile(
            r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+"
            r"/[A-Za-z0-9]{16,}"
        ),
    ),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("google-oauth-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}\b")),
    # Stripe uses an underscore where OpenAI uses a hyphen, so the rule above
    # never saw one. Test keys match too: the hint says which it was.
    ("stripe-key", re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_\-]{40,}")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("databricks-token", re.compile(r"\bdapi[0-9a-f]{28,}")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{40,}")),
    ("azure-sas", re.compile(r"\bsig=[A-Za-z0-9%/+]{20,}")),
    # The storage account key, as it appears in every Azure connection string.
    # 'AccountKey' is not 'AccessKey', so the assignment rule below never
    # matched the one shape an Azure store leaks most often.
    (
        "azure-storage-key",
        re.compile(
            r"(?i)(?P<keep>\bAccountKey\s*=\s*)(?P<secret>[A-Za-z0-9+/]{30,}={0,2})"
        ),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}"),
    ),
    (
        "bearer-token",
        re.compile(r"(?i)\b(?P<keep>bearer\s+)(?P<secret>[A-Za-z0-9._\-]{20,})"),
    ),
    # Basic is a username and password in base64 — reversible, not hashed.
    (
        "basic-auth",
        re.compile(r"(?i)\b(?P<keep>basic\s+)(?P<secret>[A-Za-z0-9+/]{4,}={0,2})"),
    ),
    # scheme://user:password@host — mask only the password.
    (
        "url-credentials",
        re.compile(
            r"(?P<keep>\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:)(?P<secret>[^\s@/]{3,})(?=@)"
        ),
    ),
]

# key = value / key: value, for names that mean a credential.
# 'pass' alone is not one of them: it is an English word, and matching it
# reported "Wording pass:" and "Temporary bypass:" as credentials.
_SECRET_NAME = (
    r"passw(?:or)?d|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|account[_-]?key|sas[_-]?token"
    r"|client[_-]?secret|auth[_-]?token|private[_-]?key|credential|conn(?:ection)?[_-]?string"
)
# The name may be part of a longer identifier — DB_PASSWORD, dbpassword,
# appsecret, spring.datasource.password — so a joined prefix is allowed. The
# old false positives came from accepting bare "pass"; now that only password
# and pwd are names, the extra boundary rejected real lowercase identifiers
# without protecting anything.
_ASSIGNMENT = re.compile(
    # A quote may close the name before the separator: JSON writes
    # {"password": "…"}, and transcripts are full of JSON payloads.
    # 'name' is the identifier alone — the audit reports it, and the
    # separator and any newline after it are no part of a name.
    rf"(?i)(?P<keep>(?P<name>(?<![A-Za-z0-9])[A-Za-z0-9_.\-]*"
    rf"(?:{_SECRET_NAME}))"
    r"[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])?"
    # Quoted values run to their closing quote, so a secret with a space in
    # it is still masked. Unquoted, take the whole value, including ';' and
    # ',' — connection strings pack the secret between them — but stop
    # before the next `key=` pair so the rest of the string stays readable.
    r"(?P<secret>(?(quote)[^\"'\n]+"
    r"|(?:(?![;,]\s*[A-Za-z0-9_.\-]+\s*=)[^\s\"'])+))"
    # The closing quote is optional. A value cut off by truncation — a stored
    # snippet, a clipped prompt — opens its quote and never closes it, and
    # requiring the pair meant the whole match failed and the value printed
    # in clear. That is exactly the case where masking matters most.
    r"(?(quote)(?P=quote)?)"
)

# Values that are not secrets even when they sit beside a secret-ish name.
_PLACEHOLDER = re.compile(
    # A mask this module already wrote is one of these: the rules run before
    # the assignment pass, and `AccountKey=[redacted:azure-storage-key]` was
    # being masked a second time, losing the label that said what it was.
    r"(?i)^(?:\[redacted[^\]]*\]"
    r"|none|null|true|false|yes|no|unset|empty|redacted|masked|hidden"
    r"|\*+|x+|\.+|<[^>]*>|\$\{[^}]*\}|\$[A-Z_][A-Z0-9_]*|%[A-Za-z_]+%)$"
)


def _is_secretish(value: str) -> bool:
    """Whether an assigned value looks like a credential rather than prose.

    Sessions about AI talk about tokens constantly ("token: 1523",
    "max_tokens: 4096"), so a bare number is never masked, and neither are
    obvious placeholders.
    """
    if value.isdigit() or _PLACEHOLDER.match(value):
        return False
    # 4 is enough once the name says "password": short values are exactly the
    # ones worth hiding, and the placeholder rule already spares $VARS.
    return len(value) >= 4


def _valid_rule(kind: str, value: str) -> bool:
    """Validate shapes whose alphabet alone is too broad."""
    if kind != "basic-auth":
        return True
    try:
        decoded = b64decode(value, validate=True)
    except (Base64Error, ValueError):
        return False
    return b":" in decoded


# Kinds whose leading characters are a public, documented prefix — printing
# those identifies the credential without revealing any of it. Every other
# kind is reported by name only.
_PUBLIC_PREFIX = {
    "github-token": 4,
    "github-pat": 11,
    "gitlab-token": 6,
    "aws-key-id": 4,
    "slack-token": 4,
    "google-api-key": 4,
    "google-oauth-secret": 7,
    "api-key": 3,
    "stripe-key": 8,          # sk_live_ / rk_test_ — which one matters
    "npm-token": 4,
    "pypi-token": 5,
    "huggingface-token": 3,
    "databricks-token": 4,
    "sendgrid-key": 3,
}

# How certain a finding is, not how valuable the account behind it is — that
# is the one thing this module can honestly rank. A documented key format is
# unmistakable; a value assigned to a password-ish name is credible but is
# sometimes prose, so it stays below the shapes that can only be credentials.
SEVERITY = {
    "private-key": "critical",
    "pgp-private-key": "critical",
    "aws-key-id": "critical",
    "github-token": "critical",
    "github-pat": "critical",
    "gitlab-token": "critical",
    "slack-token": "critical",
    "slack-webhook": "critical",
    "google-api-key": "critical",
    "google-oauth-secret": "critical",
    "api-key": "critical",
    "stripe-key": "critical",
    "npm-token": "critical",
    "pypi-token": "critical",
    "huggingface-token": "critical",
    "databricks-token": "critical",
    "sendgrid-key": "critical",
    "azure-sas": "critical",
    "azure-storage-key": "critical",
    "jwt": "high",
    "bearer-token": "high",
    "basic-auth": "high",
    "url-credentials": "high",
    "credential": "medium",
}
RANK = ("critical", "high", "medium")


def severity(kind: str) -> str:
    """How sure we are that `kind` really is a credential."""
    return SEVERITY.get(kind, "medium")


def _hint(kind: str, matched: str, name: str) -> str:
    """A safe way to tell two findings apart: a public prefix, or the name."""
    if name:
        return name
    keep = _PUBLIC_PREFIX.get(kind)
    return f"{matched[:keep]}…" if keep else ""


def findings(text: str) -> list[tuple[str, str]]:
    """Every credential-shaped span in `text`, as (kind, hint).

    This is the audit half of the module: it counts and names exposures
    without ever handing back the secret. A hint is either a public prefix
    (``ghp_…``) or the identifier the value was assigned to (``DB_PASSWORD``)
    — enough to find the line yourself, never enough to use.
    """
    if not text:
        return []
    found: list[tuple[str, str]] = []
    claimed: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        """Whether a span is untouched by an earlier, more specific rule."""
        return not any(span[0] < end and start < span[1] for start, end in claimed)

    for kind, pattern in _RULES:
        for match in pattern.finditer(text):
            secret = match.groupdict().get("secret")
            if secret is not None and not _valid_rule(kind, secret):
                continue
            span = match.span("secret") if secret is not None else match.span()
            if not free(span):
                continue
            claimed.append(span)
            found.append((kind, _hint(kind, text[span[0]:span[1]], "")))

    for match in _ASSIGNMENT.finditer(text):
        if not _is_secretish(match.group("secret")) or not free(match.span("secret")):
            continue
        claimed.append(match.span("secret"))
        found.append(("credential", _hint("credential", "", match.group("name"))))
    return found


def enabled() -> bool:
    return os.environ.get("CS_REDACT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# A terminal executes escape sequences it is sent, and a transcript is not
# trusted text: a repository's own instructions, a fetched page or a pasted
# tool output can all put raw bytes in the store. Printed unfiltered they can
# rewrite the window title, drive OSC 52 to plant text in the clipboard, or
# erase lines that were already drawn — including the `[redacted]` markers
# this module just wrote. Whole sequences go first so nothing is left behind
# as literal noise, then any stray control byte.
_ESCAPE_SEQUENCE = re.compile(
    # OSC — closed by BEL, by ST, or by the end of the text.
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)"
    r"|\x9d[^\x07\x1b]*(?:\x07|\x1b\\|$)"
    # CSI, in both its two-byte and single-byte C1 forms.
    r"|\x1b\[[0-?]*[ -/]*[@-~]?"
    r"|\x9b[0-?]*[ -/]*[@-~]?"
    # DCS/SOS/PM/APC run until a string terminator.
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\|$)"
    # Anything else introduced by ESC is at most one more byte.
    r"|\x1b[ -/]*[0-~]?"
)
_CONTROL = re.compile(
    # C0 and C1, less the newline and tab that are content.
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f"
    # Directional overrides and isolates reorder what is drawn without
    # changing what is stored, so `evil\u202egnp.exe` reads as something
    # else entirely — Trojan Source. A report the user reads to decide
    # whether to trust a hook or a path cannot afford that gap between the
    # bytes and the glyphs. Zero-width and invisible formatting characters
    # go with them: they hide text rather than reorder it, to the same end.
    r"\u200b\u200e-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]"
)

# FTS snippets can start and end inside a private-key block. In that window
# the BEGIN/END lines that identify the value are gone, leaving only one long
# base64 token or several wrapped ones. The ordinary private-key rule cannot
# recognise a block it cannot see, so snippets get this deliberately narrow
# fallback: mixed-case base64-like material, long enough to be key data.
_KEY_FRAGMENT = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{64,}(?![A-Za-z0-9+/=])")
_KEY_FRAGMENT_RUN = re.compile(
    r"(?<![A-Za-z0-9+/=])(?:[A-Za-z0-9+/=]{32,}[ \t\n]+){1,}"
    r"[A-Za-z0-9+/=]{32,}(?![A-Za-z0-9+/=])"
)


def plain(text: str) -> str:
    """Strip terminal control sequences from stored text.

    Newlines and tabs survive because they are content; everything else that
    a terminal would act on rather than show does not. This is not part of
    masking and is never optional — `CS_REDACT=0` asks to see a secret, not
    to hand the terminal over.
    """
    if not text:
        return text
    return _CONTROL.sub("", _ESCAPE_SEQUENCE.sub("", text))


def one_line(text: str) -> str:
    """Sanitise an untrusted value that occupies one terminal row.

    ``plain`` keeps newlines and tabs because transcripts need them. Table
    cells, headings and identifiers do not: either character can escape its
    allotted column and forge part of the report around it.
    """
    return re.sub(r"[\n\t]+", " ", plain(text))


def redact(text: str) -> str:
    """Return `text` with anything credential-shaped masked."""
    text = plain(text)
    if not text or not enabled():
        return text
    for kind, pattern in _RULES:
        def replace(match: re.Match[str], kind: str = kind) -> str:
            keep = match.groupdict().get("keep") or ""
            secret = match.groupdict().get("secret")
            if secret is not None and not _valid_rule(kind, secret):
                return match.group(0)
            return keep + _mask(kind)

        text = pattern.sub(replace, text)

    def replace_assignment(match: re.Match[str]) -> str:
        value = match.group("secret")
        if not _is_secretish(value):
            return match.group(0)
        quote = match.group("quote") or ""
        return f"{match.group('keep')}{quote}{MASK}{quote}"

    return _ASSIGNMENT.sub(replace_assignment, text)


def snippet(text: str) -> str:
    """Redact an FTS window, including private-key bodies cut from their header."""
    text = redact(text)
    if not text or not enabled():
        return text

    def replace_fragment(match: re.Match[str]) -> str:
        value = match.group(0)
        compact = re.sub(r"\s+", "", value)
        # Hex digests and long identifiers are common search results. Key
        # bodies use the wider base64 alphabet and, in practice, mix case.
        if not (any(c.islower() for c in compact) and any(c.isupper() for c in compact)):
            return value
        return _mask("private-key-fragment")

    text = _KEY_FRAGMENT_RUN.sub(replace_fragment, text)
    return _KEY_FRAGMENT.sub(replace_fragment, text)

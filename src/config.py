"""Reads the two plain-text config files in config/.

Both files use the same forgiving format so a non-technical person can edit
them in the GitHub web editor without breaking anything:

    - one entry per line
    - anything after a '#' is a comment and is ignored
    - blank lines are ignored
    - a fully commented-out line is simply skipped ("paused")

recipients.txt additionally accepts "Name <email@domain>" as well as a bare
email address.
"""

import os
import re
from email.utils import parseaddr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")

TICKER_RE = re.compile(r"^[A-Z0-9]{2,5}$")


def _strip_comments(path):
    """Yield (line_number, cleaned_text) for every meaningful line."""
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, start=1):
            text = raw.split("#", 1)[0].strip()
            if text:
                yield n, text


class ConfigError(Exception):
    """Raised when a config file cannot be understood. Never guessed around."""


def load_watchlist(path=None):
    """Return an ordered, de-duplicated list of ASX codes."""
    path = path or os.path.join(CONFIG_DIR, "watchlist.txt")
    if not os.path.exists(path):
        raise ConfigError("watchlist.txt is missing from config/")

    codes, seen, problems = [], set(), []
    for n, text in _strip_comments(path):
        code = text.upper()
        if not TICKER_RE.match(code):
            problems.append(f"  line {n}: '{text}' does not look like an ASX code")
            continue
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)

    if problems:
        raise ConfigError(
            "watchlist.txt has lines that are not valid ASX codes:\n"
            + "\n".join(problems)
            + "\n\nFix the line, or put a # in front of it to ignore it."
        )
    if not codes:
        raise ConfigError("watchlist.txt has no tickers in it.")
    return codes


def load_recipients(path=None):
    """Return a list of RFC-compliant recipient strings for the To: header."""
    path = path or os.path.join(CONFIG_DIR, "recipients.txt")
    if not os.path.exists(path):
        raise ConfigError("recipients.txt is missing from config/")

    people, seen, problems = [], set(), []
    for n, text in _strip_comments(path):
        name, addr = parseaddr(text)
        if "@" not in addr or "." not in addr.split("@")[-1]:
            problems.append(f"  line {n}: '{text}' is not a valid email address")
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        people.append(f"{name} <{addr}>" if name else addr)

    if problems:
        raise ConfigError(
            "recipients.txt has lines that are not valid email addresses:\n"
            + "\n".join(problems)
            + "\n\nUse either  someone@example.com  or  Their Name <someone@example.com>"
        )
    if not people:
        raise ConfigError(
            "recipients.txt has nobody in it, so the briefing would go nowhere. "
            "Add at least one email address."
        )
    return people


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ConfigError(
            f"Environment variable {name} is not set. "
            "In GitHub, add it under Settings > Secrets and variables > Actions."
        )
    return value

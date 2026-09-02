"""Reads the plain-text config files in config/.

All of them use the same forgiving format so a non-technical person can edit
them in the GitHub web editor without breaking anything:

    - one entry per line
    - anything after a '#' is a comment and is ignored
    - blank lines are ignored
    - a fully commented-out line is simply skipped ("paused")

watchlist.txt       one ASX code per line, with an optional commodity tag:
                        PRU            # no tag: goes to the default commodity
                        BOE    U       # tagged uranium
commodities.txt     the commodities the briefing is split into, in the order
                    they appear in the email. The first one is the default.
recipients.txt      "Name <email@domain>" or a bare email address.

Two different rules apply to mistakes, on purpose.

A line in watchlist.txt that is not an ASX code stops the run, as it always
has: a company that is silently not watched is worse than no email.

A commodity tag that is unknown, or missing, or a commodities.txt that cannot
be read, never stops the run and never drops a name. The name goes to the
default commodity and the problem is printed. A heading in the wrong colour is
a small thing; a company that vanishes from the briefing because of a typo in a
tag is exactly the kind of silent failure this program exists to avoid.
"""

import os
import re
from email.utils import parseaddr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")

TICKER_RE = re.compile(r"^[A-Z0-9]{2,5}$")
COMMODITY_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,3}$")


def _strip_comments(path):
    """Yield (line_number, cleaned_text) for every meaningful line."""
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, start=1):
            text = raw.split("#", 1)[0].strip()
            if text:
                yield n, text


class ConfigError(Exception):
    """Raised when a config file cannot be understood. Never guessed around."""


# --------------------------------------------------------------------- watchlist

def _parse_watchlist(path):
    """Read watchlist.txt once and return everything the two loaders need.

    Returns (codes, tags, problems):
        codes     ordered, de-duplicated ASX codes
        tags      {code: raw tag text or None}, first occurrence wins
        problems  human-readable lines for anything that is not an ASX code
    """
    codes, tags, seen, problems = [], {}, set(), []
    for n, text in _strip_comments(path):
        parts = text.split()
        code = parts[0].upper()
        if not TICKER_RE.match(code):
            problems.append(f"  line {n}: '{text}' does not look like an ASX code")
            continue
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        # Everything after the code is the tag. Two words there is almost
        # certainly a comment that lost its '#'; it will not match any
        # commodity and is reported as such rather than silently read as one.
        tags[code] = " ".join(parts[1:]) if len(parts) > 1 else None
    return codes, tags, problems


def load_watchlist(path=None):
    """Return an ordered, de-duplicated list of ASX codes."""
    path = path or os.path.join(CONFIG_DIR, "watchlist.txt")
    if not os.path.exists(path):
        raise ConfigError("watchlist.txt is missing from config/")

    codes, _tags, problems = _parse_watchlist(path)

    if problems:
        raise ConfigError(
            "watchlist.txt has lines that are not valid ASX codes:\n"
            + "\n".join(problems)
            + "\n\nFix the line, or put a # in front of it to ignore it."
        )
    if not codes:
        raise ConfigError("watchlist.txt has no tickers in it.")
    return codes


# ------------------------------------------------------------------ commodities

def load_commodities(path=None):
    """Return ([(code, label), ...] in file order, warnings).

    The first entry is the default for any name without a tag. An empty list
    means the briefing is not split by commodity at all, which is what every
    run before this feature existed did, so a missing or unreadable file falls
    back to that rather than stopping anything.
    """
    path = path or os.path.join(CONFIG_DIR, "commodities.txt")
    warnings = []
    if not os.path.exists(path):
        warnings.append("config/commodities.txt is missing, so the briefing is "
                        "not split by commodity today.")
        return [], warnings

    entries, seen = [], set()
    for n, text in _strip_comments(path):
        parts = text.split(None, 1)
        code = parts[0]
        if not COMMODITY_CODE_RE.match(code):
            warnings.append(f"commodities.txt line {n}: '{code}' is not a short "
                            f"code like Au or Cu, line ignored.")
            continue
        label = parts[1].strip() if len(parts) > 1 else code
        key = code.lower()
        if key in seen:
            warnings.append(f"commodities.txt line {n}: '{code}' listed twice, "
                            f"second one ignored.")
            continue
        seen.add(key)
        entries.append((code, label))

    if not entries:
        warnings.append("config/commodities.txt has no commodities in it, so the "
                        "briefing is not split by commodity today.")
    return entries, warnings


def load_watchlist_tags(path=None, commodities=None):
    """Return ({code: commodity_code}, warnings) for every name on the watchlist.

    Every code on the watchlist gets exactly one commodity. A missing tag takes
    the default (the first commodity listed). An unknown tag also takes the
    default and is reported. Nothing here raises: see the module docstring.
    """
    path = path or os.path.join(CONFIG_DIR, "watchlist.txt")
    if commodities is None:
        commodities, _ = load_commodities()
    warnings = []
    if not commodities:
        return {}, warnings

    default = commodities[0][0]
    by_key = {code.lower(): code for code, _label in commodities}
    codes, raw_tags, _problems = _parse_watchlist(path)

    assigned, untagged, unknown = {}, [], []
    for code in codes:
        raw = raw_tags.get(code)
        if not raw:
            assigned[code] = default
            untagged.append(code)
            continue
        hit = by_key.get(raw.lower())
        if hit is None:
            assigned[code] = default
            unknown.append((code, raw))
            continue
        assigned[code] = hit

    if unknown:
        listed = ", ".join(f"{c} '{t}'" for c, t in unknown)
        warnings.append(f"{len(unknown)} watchlist tag(s) do not match anything "
                        f"in commodities.txt and were placed in {default}: {listed}")
    if untagged:
        warnings.append(f"{len(untagged)} name(s) carry no commodity tag and "
                        f"default to {default}: {', '.join(untagged)}")
    return assigned, warnings


# ------------------------------------------------------------------- recipients

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

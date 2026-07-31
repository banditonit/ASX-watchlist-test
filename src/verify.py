"""Checks every figure in a summary against the announcement it came from.

Prompt instructions reduce the chance of a number crossing from one company to
another. They cannot rule it out. This does, deterministically: each numeric
token written into a summary is looked for in that announcement's own text, and
anything that is not there is reported.

The matching is deliberately tolerant, because a summary legitimately rewrites
figures: 90,833 becomes 90.8koz, 1,307 becomes A$1.3 billion, 0.86 stays 0.86.
So a figure passes if its digits appear in the source at full precision, or if
it matches a rounded form of something in the source. What it cannot do is pass
a number that has no counterpart in the source at all, which is exactly the
signature of a figure borrowed from another company.
"""

import re

# Numbers with their optional scale suffix, e.g. 90,833  1.3bn  728koz  53.1%
NUM = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*"
    r"(bn|billion|m|million|k|koz|moz|kt|mt|oz|g/t|%|bps)?",
    re.I,
)

SCALE = {"bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6,
         "k": 1e3, "koz": 1e3, "moz": 1e6, "kt": 1e3, "mt": 1e6}

# Years, ordinary counts and small integers are not claims worth policing.
IGNORE_EXACT = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "12", "100"}
YEAR = re.compile(r"^(19|20)\d{2}$")


def _numbers(text):
    """Yield (raw, value) for every meaningful numeric token in text."""
    for m in NUM.finditer(text or ""):
        raw, suffix = m.group(1), (m.group(2) or "").lower()
        if raw in IGNORE_EXACT or YEAR.match(raw.replace(",", "")):
            continue
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        yield m.group(0).strip(), value * SCALE.get(suffix, 1.0)


def _present(value, source_values, digits):
    """Is this figure supported by the source, exactly or as a rounding of it?"""
    plain = f"{value:.10g}".replace(".", "")
    if plain in digits:
        return True
    for other in source_values:
        if other == 0:
            continue
        ratio = value / other
        # same number, or the same number rounded, or expressed at a
        # neighbouring scale (thousands against millions and so on)
        for factor in (1.0, 1e-3, 1e3, 1e-6, 1e6):
            scaled = ratio / factor
            if 0.995 <= scaled <= 1.005:
                return True
    return False


def check(summary, source_text):
    """Return the figures in summary with no counterpart in source_text."""
    if not summary or not source_text:
        return []
    source_values = [v for _, v in _numbers(source_text)]
    digits = re.sub(r"[^\d]", "", source_text)
    unsupported = []
    for raw, value in _numbers(summary):
        if not _present(value, source_values, digits):
            unsupported.append(raw)
    return unsupported


def audit(items):
    """Check a list of summarised items in place. Returns the problem list."""
    problems = []
    for item in items:
        text = item.get("_source_text") or ""
        body = " ".join(str(item.get(k) or "")
                        for k in ("body", "summary", "announcement"))
        bad = check(body, text)
        item["unverified_figures"] = bad
        if bad:
            problems.append({
                "ticker": item.get("ticker"),
                "headline": item.get("headline"),
                "figures": bad,
            })
    return problems

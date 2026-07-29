"""House formatting for the one-line quarterly summaries.

Reproduces the established note format:

    NST ($28B) - Northern Star sold 433koz gold at AISC $2.7k/oz, lifted cash
    and bullion to $1.2B (+$52m QoQ), and began commissioning Stage 1

Market cap drives the ordering, largest first, so the note reads top-down by
size the way the desk already reads it.
"""


def market_cap(value):
    """28852302506 -> '$29B'   833_000_000 -> '$833m'   None -> ''."""
    if not value:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v >= 1e9:
        b = v / 1e9
        return f"${b:.0f}B" if b >= 10 else f"${b:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}m"
    return f"${v / 1e3:.0f}k"


def label(record):
    """'NST ($29B)' for the start of a line."""
    cap = market_cap(record.get("market_cap"))
    return f"{record['ticker']} ({cap})" if cap else record["ticker"]


def by_size(records):
    """Largest market cap first. Unknown caps sort last, alphabetically."""
    return sorted(
        records,
        key=lambda r: (-(r.get("market_cap") or 0), r.get("ticker", "")),
    )


def enrich(entries, announcements):
    """Attach market cap to model output, then sort by size.

    The model writes the words; the market cap that frames them is joined back
    on from the collected data so it cannot be invented.
    """
    caps = {}
    for a in announcements:
        caps.setdefault(a["ticker"], a.get("market_cap"))
    for e in entries:
        cap = caps.get(e.get("ticker"))
        e["market_cap"] = cap
        e["cap_label"] = market_cap(cap)
    return by_size(entries)

"""Collects ASX announcements for the watchlist and extracts their full text.

Three endpoints on the ASX's own backing data service do all the work:

  markets/announcements          every announcement market-wide, newest first
  companies/{CODE}/announcements the same, filtered to one company
  file/{documentKey}             the announcement document itself

The market-wide sweep is one request rather than one per ticker, so adding
names to the watchlist costs nothing. It is also a single point of failure:
anything the market-wide feed omits, or that sits beyond the pages this fetches,
is simply never seen, and nothing downstream can tell the difference between
"nothing was announced" and "we did not look far enough".

That is not theoretical. On 25 August 2026 Ramelius lodged its 2026 Resources
and Reserves Statement and only the accompanying investor presentation reached
the pack. The statement was never collected, so no filter, score or summary
could have saved it.

So every watchlist code is now also asked directly, through its own
announcements feed, and the two results are reconciled. The per-company pass is
authoritative: it is the company's own list. Anything it finds that the sweep
missed is added and recorded in `sweep_missed`, so a feed that starts dropping
announcements shows up in the evidence pack instead of looking like a quiet day.
That costs one request per code, about a minute for 93 names, and it is the only
way to know the day is complete.

Nothing here decides what is interesting. This module's only job is to come
back with a complete, dated, full-text record of what was published. Judgement
happens later, on the text.
"""

import io
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

AWST = ZoneInfo("Australia/Perth")
API = "https://asx.api.markitdigital.com/asx-research/1.0"
UA = "DiscoveryCapital-WatchlistBriefing/1.0 (internal monitoring)"

PAGE_SIZE = 500
MAX_PAGES = 12
TIMEOUT = 45
PAUSE = 0.4          # be a polite client, this is someone else's server


class FeedError(Exception):
    """The upstream feed did not look the way we expect. Never worked around."""


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    return s


MAX_LOOKBACK_HOURS = 168     # a week, so a long outage cannot fetch forever


def window(hours=24, now=None, since=None):
    """Return (start, end) as timezone-aware UTC datetimes.

    The briefing is always described in AWST, so the AWST calendar date is
    computed here once, explicitly, rather than being read off a UTC string
    somewhere downstream. Getting this backwards is the single easiest way to
    misdate an announcement lodged late in the UTC evening.

    `since` is where the previous run stopped. Without it the window is simply
    the last N hours measured from whenever this run happens, which leaves a
    hole every time a run lands later than the one before it: the 7 August 2026
    run started at 09:38 after GitHub queued it, so 6 August 08:10 to 09:38 was
    covered by neither day. Passing the previous window end makes the two abut,
    and a run that is late reaches further back by exactly the amount it is
    late. Announcements already reported are dropped by document key
    afterwards, so the overlap costs nothing.
    """
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    if since is not None and since < start:
        start = since
    return max(start, end - timedelta(hours=MAX_LOOKBACK_HOURS)), end


def to_awst(dt):
    return dt.astimezone(AWST)


def _parse_dt(raw):
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def sweep(codes, start, end, session=None):
    """Page through the market-wide feed and keep anything on the watchlist.

    Stops as soon as a page ends older than the window, since the feed is
    ordered newest first.
    """
    session = session or _session()
    wanted = {c.upper() for c in codes}
    found, oldest_seen, covered = {}, None, False

    for page in range(MAX_PAGES):
        url = f"{API}/markets/announcements"
        resp = session.get(url, params={"count": PAGE_SIZE, "page": page},
                           timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()

        items = (payload.get("data") or {}).get("items")
        if items is None:
            raise FeedError(
                "The market-wide announcements feed did not contain data.items. "
                "The API shape may have changed; stopping rather than reporting "
                "an empty day."
            )
        if not items:
            break

        for item in items:
            when = _parse_dt(item.get("date"))
            if when is None:
                continue
            oldest_seen = when if oldest_seen is None else min(oldest_seen, when)
            if when < start or when > end:
                continue
            symbol = (item.get("symbol") or "").upper()
            if symbol not in wanted:
                continue
            key = item.get("documentKey")
            if not key or key in found:
                continue
            found[key] = _record(item, symbol, when, key)

        if oldest_seen is not None and oldest_seen < start:
            covered = True
            break
        time.sleep(PAUSE)

    # Reaching the page limit without reaching the start of the window means
    # the sweep stopped early. It used to return its partial result silently,
    # which is indistinguishable from a complete one.
    if not covered:
        print(f"  ! market-wide sweep stopped after {MAX_PAGES} pages without "
              f"reaching the start of the window. Results may be incomplete; "
              f"the per-company pass is the backstop.")

    return sorted(found.values(), key=lambda r: r["lodged_utc"], reverse=True)


def company_feed(ticker, session=None, count=60):
    """Raw recent announcement items for one company, newest first.

    Fetched once per code and used twice: for the reconciliation below, and for
    the prior-announcement history that tells a first release from a
    restatement. One request, not two.
    """
    session = session or _session()
    try:
        resp = session.get(f"{API}/companies/{ticker}/announcements",
                           params={"count": count, "page": 0}, timeout=TIMEOUT)
        resp.raise_for_status()
        return (resp.json().get("data") or {}).get("items") or []
    except Exception as exc:                                   # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {exc}"}


def in_window(items, code, start, end):
    """The records from one company's own feed that fall inside the window."""
    out = []
    for item in items or []:
        when = _parse_dt(item.get("date"))
        if when is None or when < start or when > end:
            continue
        key = item.get("documentKey")
        if key:
            out.append(_record(item, code.upper(), when, key))
    return out


def _record(item, symbol, when, key):
    info = (item.get("companyInfo") or [{}])
    info = info[0] if isinstance(info, list) and info else {}
    awst = to_awst(when)
    return {
        "ticker": symbol,
        "company": info.get("displayName") or item.get("symbolDisplay") or symbol,
        "sector": info.get("sector"),
        "headline": (item.get("headline") or "").strip(),
        "types": item.get("announcementTypes") or [],
        "price_sensitive": bool(item.get("isPriceSensitive")),
        "document_key": key,
        "size": item.get("fileSize"),
        "lodged_utc": when.isoformat(),
        "lodged_awst": awst.isoformat(),
        "date_awst": awst.strftime("%-d %B %Y"),
        "time_awst": awst.strftime("%H:%M"),
        "text": None,
        "text_status": "not fetched",
    }


def fetch_text(record, session=None, max_chars=180_000):
    """Download one announcement and extract its text in place.

    A document we could not read is recorded as unreadable. It is never
    silently dropped: an announcement that exists but could not be parsed is
    a fact the briefing has to state, not hide.
    """
    session = session or _session()
    key = record["document_key"]
    try:
        resp = session.get(f"{API}/file/{key}", timeout=TIMEOUT)
        resp.raise_for_status()
        raw = resp.content
    except Exception as exc:                                  # noqa: BLE001
        record["text_status"] = f"download failed: {type(exc).__name__}"
        return record

    if raw[:5] != b"%PDF-":
        text = raw.decode("utf-8", errors="replace").strip()
        record["text"] = text[:max_chars]
        record["text_status"] = "ok (plain)" if text else "empty response"
        return record

    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            record["pages"] = len(pdf.pages)
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
                if sum(len(p) for p in pages) > max_chars:
                    break
        text = "\n".join(pages).strip()
    except Exception as exc:                                  # noqa: BLE001
        record["text_status"] = f"extract failed: {type(exc).__name__}"
        return record

    if not text:
        record["text_status"] = "unreadable (likely a scanned image, needs OCR)"
        return record

    record["text"] = text[:max_chars]
    record["text_status"] = "ok"
    return record


def fetch_quote(ticker, session=None):
    """Market cap, last price and the day's move for one code.

    Only called for tickers that actually announced something, so this stays a
    handful of requests rather than one per watchlist name.
    """
    session = session or _session()
    try:
        resp = session.get(f"{API}/companies/{ticker}/header", timeout=TIMEOUT)
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
    except Exception:                                          # noqa: BLE001
        return {}
    return {
        "market_cap": data.get("marketCap"),
        "price_last": data.get("priceLast"),
        "price_change_pct": data.get("priceChangePercent"),
        "volume": data.get("volume"),
        "status_code": data.get("statusCode"),
        "long_name": data.get("displayName"),
    }


def history_from(items, before, days=120):
    """Headlines this company published before the window, from a fetched feed."""
    cutoff = before - timedelta(days=days)
    out = []
    for item in items or []:
        when = _parse_dt(item.get("date"))
        if when is None or when >= before or when < cutoff:
            continue
        out.append({
            "headline": (item.get("headline") or "").strip(),
            "date_awst": to_awst(when).strftime("%-d %B %Y"),
            "price_sensitive": bool(item.get("isPriceSensitive")),
        })
    return out


def fetch_history(ticker, before, days=120, session=None, limit=40):
    """Headlines this company published in the months before the window.

    Used to tell a first release from a restatement. If a quarterly writes up a
    definitive feasibility study, and a standalone DFS announcement already sits
    in this list, the quarterly is restating, not breaking, the news. That is a
    lookup rather than a judgement, which is what makes it reliable.
    """
    session = session or _session()
    cutoff = before - timedelta(days=days)
    try:
        resp = session.get(f"{API}/companies/{ticker}/announcements",
                           params={"count": limit, "page": 0}, timeout=TIMEOUT)
        resp.raise_for_status()
        items = (resp.json().get("data") or {}).get("items") or []
    except Exception:                                          # noqa: BLE001
        return []

    out = []
    for item in items:
        when = _parse_dt(item.get("date"))
        if when is None or when >= before or when < cutoff:
            continue
        out.append({
            "headline": (item.get("headline") or "").strip(),
            "date_awst": to_awst(when).strftime("%-d %B %Y"),
            "price_sensitive": bool(item.get("isPriceSensitive")),
        })
    return out


def collect(codes, hours=24, now=None, since=None):
    """Full pass: sweep, reconcile against every company, read, then price."""
    start, end = window(hours=hours, now=now, since=since)
    session = _session()
    records = sweep(codes, start, end, session=session)

    # Ask every watchlist company directly and reconcile. The market-wide feed
    # is fast but it is one source; a company's own feed is the record of what
    # that company lodged. Anything only the company knows about is added here
    # and reported, so a gap in the sweep is visible rather than silent.
    feeds, feed_errors, missed = {}, {}, []
    seen = {r["document_key"] for r in records}
    for code in codes:
        items = company_feed(code, session=session)
        if isinstance(items, dict):
            feed_errors[code] = items["_error"]
            items = []
        feeds[code] = items
        for record in in_window(items, code, start, end):
            if record["document_key"] in seen:
                continue
            seen.add(record["document_key"])
            missed.append(record)
            records.append(record)
        time.sleep(PAUSE)

    if missed:
        print(f"  ! the market-wide sweep missed {len(missed)} announcement(s) "
              f"that the companies' own feeds reported:")
        for r in missed:
            print(f"      {r['ticker']}  {r['date_awst']} {r['time_awst']}  "
                  f"{r['headline'][:56]}")
    if feed_errors:
        print(f"  ! could not reach the company feed for {len(feed_errors)} "
              f"code(s): {', '.join(sorted(feed_errors))}. Those names rely on "
              f"the market-wide sweep alone today.")

    records.sort(key=lambda r: r["lodged_utc"], reverse=True)
    for record in records:
        fetch_text(record, session=session)
        time.sleep(PAUSE)

    quotes = {}
    for ticker in sorted({r["ticker"] for r in records}):
        quotes[ticker] = fetch_quote(ticker, session=session)
        time.sleep(PAUSE)
    for record in records:
        record.update(quotes.get(record["ticker"]) or {})

    # A company's own announcements feed does not carry the companyInfo block
    # that the market-wide feed does, so a record found by the reconciliation
    # pass falls back to its ticker for a name and the Company column reads
    # "WGX" instead of "WESTGOLD RESOURCES LIMITED". The name is already in
    # hand twice over: on any sweep record for the same code, and on the
    # company header fetched just above for its market cap. Use it.
    names = {}
    for record in records:
        company = record.get("company")
        if company and company != record["ticker"]:
            names.setdefault(record["ticker"], company)
    for ticker, quote in quotes.items():
        if quote and quote.get("long_name"):
            names.setdefault(ticker, quote["long_name"])
    for record in records:
        if record.get("company") == record["ticker"]:
            record["company"] = names.get(record["ticker"], record["ticker"])

    # Prior announcements, so a restatement can be told from a first release.
    # Read out of the feeds already fetched above rather than re-requesting.
    for record in records:
        items = feeds.get(record["ticker"])
        if items is None:
            items = company_feed(record["ticker"], session=session)
            if isinstance(items, dict):
                items = []
            feeds[record["ticker"]] = items
            time.sleep(PAUSE)
        record["prior_announcements"] = history_from(items, start)
    return {
        "window_start_awst": to_awst(start).isoformat(),
        "window_end_awst": to_awst(end).isoformat(),
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "date_awst": to_awst(end).strftime("%-d %B %Y"),
        "tickers_checked": len(codes),
        "window_hours": round((end - start).total_seconds() / 3600, 2),
        "sweep_missed": [{"ticker": r["ticker"], "headline": r["headline"],
                          "lodged_awst": r["lodged_awst"],
                          "document_key": r["document_key"]} for r in missed],
        "feed_errors": feed_errors,
        "announcements": records,
    }

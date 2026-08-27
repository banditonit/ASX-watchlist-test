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

# The feed caps how many items it will return per page, whatever is asked for,
# and it does not say so. Paging stops when the window is covered, so the page
# budget only has to be large enough to get there: on 26 August 2026 twelve
# pages reached back 88 minutes against a 24 hour window, and the sweep found
# 12 of the day's 32 announcements. The 20 it missed were not missing from the
# feed, they were on page 13 and beyond.
PAGE_SIZE = 500          # asked for; the feed returns what it wants to
MAX_PAGES = 80           # a budget, not a target: paging stops at the window
MAX_ITEMS = 40_000       # runaway guard, roughly a fortnight of ASX filings
TIMEOUT = 45
PAUSE = 0.4          # be a polite client, this is someone else's server


# Statuses worth trying again. 429 is the feed asking us to slow down, and the
# 5xx range is it having a bad moment; neither is a reason to lose the day.
# A 400 is not here: it is the feed saying the request itself is wrong, and
# repeating it verbatim would just be rude. AQI, PGO and PDI have returned 400
# on every run since 25 August 2026 while the other 90 codes were fine, which
# is a per-code problem rather than a volume one.
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF = 2.0            # seconds, doubling, and Retry-After wins if it is set
BACKOFF_CAP = 60.0


class FeedError(Exception):
    """The upstream feed did not look the way we expect. Never worked around."""


def _get(session, url, params=None, timeout=TIMEOUT, label=""):
    """One request, with backoff on the statuses that mean 'try again'.

    The page budget went from 12 to 80 to cover the whole window, so a run now
    makes roughly 225 requests where it made 150. That is still gentle, but it
    is enough that being told to slow down is a question of when rather than
    whether, and being told to slow down should cost a pause, not a briefing.
    """
    delay = BACKOFF
    for attempt in range(MAX_RETRIES + 1):
        resp = session.get(url, params=params, timeout=timeout)
        if resp.status_code not in RETRY_STATUS or attempt == MAX_RETRIES:
            resp.raise_for_status()
            return resp
        after = (resp.headers or {}).get("Retry-After")
        try:
            wait = float(after) if after else delay
        except (TypeError, ValueError):
            wait = delay
        wait = min(wait, BACKOFF_CAP)
        print(f"  feed returned {resp.status_code}{' on ' + label if label else ''}, "
              f"waiting {wait:.0f}s and trying again "
              f"({attempt + 1} of {MAX_RETRIES})")
        time.sleep(wait)
        delay *= 2
    raise FeedError("unreachable")


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
    found, oldest_seen, covered, broke = {}, None, False, False

    # The market-wide feed used to be the only source, so a change in its shape
    # had to stop the run rather than report an empty day. It is not the only
    # source any more, and on 25 and 26 August 2026 it found 7 and 8 of the 32
    # announcements collected: the per-company pass supplied the other three
    # quarters. A feed in that state must not be able to take the briefing down
    # with it, so failures here are reported and stepped over, and the caller
    # decides whether what remains is enough.

    scanned, per_page = 0, None
    for page in range(MAX_PAGES):
        url = f"{API}/markets/announcements"
        try:
            resp = _get(session, url, params={"count": PAGE_SIZE, "page": page},
                        label=f"market page {page}")
            payload = resp.json()
        except Exception as exc:                               # noqa: BLE001
            print(f"  ! market-wide feed failed on page {page}: "
                  f"{type(exc).__name__}: {exc}")
            print("    Falling back to the per-company pass for the whole day.")
            broke = True
            break

        items = (payload.get("data") or {}).get("items")
        if items is None:
            print("  ! the market-wide feed returned no data.items. Its shape may "
                  "have changed. Falling back to the per-company pass.")
            broke = True
            break
        if not items:
            break

        if per_page is None:
            per_page = len(items)
            if per_page < PAGE_SIZE:
                print(f"  the feed returned {per_page} items per page, not the "
                      f"{PAGE_SIZE} requested. Paging until the window is covered.")
        scanned += len(items)
        if scanned > MAX_ITEMS:
            print(f"  ! scanned {scanned} market announcements without reaching "
                  f"the start of the window. Stopping.")
            broke = True
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
    if not covered and not broke:
        reached = to_awst(oldest_seen).strftime("%d %b %H:%M") if oldest_seen else "nothing"
        print(f"  ! market-wide sweep exhausted {MAX_PAGES} pages and only "
              f"reached back to {reached} AWST, short of the window start. "
              f"Raise MAX_PAGES. The per-company pass covers the gap meanwhile.")
    elif covered and per_page:
        print(f"  market-wide sweep covered the window in "
              f"{scanned} announcements.")

    return sorted(found.values(), key=lambda r: r["lodged_utc"], reverse=True)


COMPANY_FEED_COUNTS = (60, 25)


def company_feed(ticker, session=None, count=None):
    """Raw recent announcement items for one company, newest first.

    Fetched once per code and used twice: for the reconciliation below, and for
    the prior-announcement history that tells a first release from a
    restatement. One request, not two.
    """
    session = session or _session()
    # AQI, PGO and PDI returned 400 Bad Request on count=60 on both 25 and 26
    # August 2026 while every other code was fine, so a smaller page is tried
    # before the code is written off. A name that errors is a name collected
    # from the market-wide sweep alone that day, which is now the weaker source.
    sizes = (count,) if count else COMPANY_FEED_COUNTS
    last = ""
    for size in sizes:
        try:
            resp = _get(session, f"{API}/companies/{ticker}/announcements",
                        params={"count": size, "page": 0}, label=ticker)
            return (resp.json().get("data") or {}).get("items") or []
        except Exception as exc:                               # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(PAUSE)
    return {"_error": last}


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
        resp = _get(session, f"{API}/file/{key}", label=record.get("ticker", ""))
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
        resp = _get(session, f"{API}/companies/{ticker}/header", label=ticker)
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
        resp = _get(session, f"{API}/companies/{ticker}/announcements",
                    params={"count": limit, "page": 0}, label=ticker)
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

    # Nothing found by either source is not the same as a quiet day. It is what
    # a broken feed also looks like, and the two must not be confused.
    if not records and feed_errors:
        raise FeedError(
            f"No announcements found, and {len(feed_errors)} company feed(s) "
            f"could not be read. Refusing to report an empty day when the "
            f"sources themselves were failing: {', '.join(sorted(feed_errors))}"
        )

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

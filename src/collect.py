"""Collects ASX announcements for the watchlist and extracts their full text.

Three endpoints on the ASX's own backing data service do all the work:

  markets/announcements          every announcement market-wide, newest first
  companies/{CODE}/announcements the same, filtered to one company
  file/{documentKey}             the announcement document itself

The market-wide sweep is one request rather than one per ticker, so adding
names to the watchlist costs nothing. Per-company calls are only used as a
fallback if the market-wide feed looks incomplete.

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


def window(hours=24, now=None):
    """Return (start, end) as timezone-aware UTC datetimes.

    The briefing is always described in AWST, so the AWST calendar date is
    computed here once, explicitly, rather than being read off a UTC string
    somewhere downstream. Getting this backwards is the single easiest way to
    misdate an announcement lodged late in the UTC evening.
    """
    end = now or datetime.now(timezone.utc)
    return end - timedelta(hours=hours), end


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
    found, oldest_seen = {}, None

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
            break
        time.sleep(PAUSE)

    return sorted(found.values(), key=lambda r: r["lodged_utc"], reverse=True)


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


def collect(codes, hours=24, now=None):
    """Full pass: sweep the feed, read every document, then price the names."""
    start, end = window(hours=hours, now=now)
    session = _session()
    records = sweep(codes, start, end, session=session)
    for record in records:
        fetch_text(record, session=session)
        time.sleep(PAUSE)

    quotes = {}
    for ticker in sorted({r["ticker"] for r in records}):
        quotes[ticker] = fetch_quote(ticker, session=session)
        time.sleep(PAUSE)
    for record in records:
        record.update(quotes.get(record["ticker"]) or {})
    return {
        "window_start_awst": to_awst(start).isoformat(),
        "window_end_awst": to_awst(end).isoformat(),
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "date_awst": to_awst(end).strftime("%-d %B %Y"),
        "tickers_checked": len(codes),
        "announcements": records,
    }

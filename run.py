#!/usr/bin/env python3
"""Daily ASX watchlist briefing.

  python run.py                 collect, summarise, build, email
  python run.py --dry-run       everything except sending
  python run.py --no-llm        collect only, write the evidence pack
  python run.py --pack FILE     rebuild from a saved pack, no network

Exits non-zero if anything failed, so the scheduler reports it rather than a
clean-looking empty briefing going out unnoticed.

The shape of a morning:

  wake      collect the window, plus anything from the last seven days that no
            previous run reported; summarise; build the whole email
  hold      until a few minutes before the send time
  top-up    sweep the minutes since the first collection, re-check any company
            feed that errored earlier, summarise only what is new, redo the
            lead and subject, rebuild
  hold      until the send time
  send

The top-up exists because building early and holding moved the collection
cutoff from 08:10 to about 07:32, and 2.3 announcements a day were being lodged
in that gap. Sweeping again just before sending brings the cutoff back to within
a few minutes of the send time without moving the send time itself.
"""

import argparse
import glob
import json
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from config import (load_recipients, load_watchlist, load_commodities,  # noqa: E402
                    load_watchlist_tags, ConfigError, env)

ROOT = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(ROOT, "archive")


# How many archived days to read back when working out what has already been
# reported. Comfortably more than any window, cheap to scan, and short enough
# that it can never suppress something from a previous month. It must also
# cover the seven-day lookback in collect.py, which it does twice over.
HISTORY_DAYS = 10


def previous_run(archive=None, include_today=False):
    """Where the last briefing stopped, what it covered, and who was on the list.

    Only packs that produced an email count. A pack is written before the
    summaries are built, so a run that collected announcements and then died
    would otherwise mark them as reported and they would never be seen. The
    rendered email beside it is the proof the day actually went out.

    Returns (latest_window_end, seen_document_keys, tickers_on_last_list).
    The last of those drives the lookback: a name added to the watchlist today
    has no previous run to have been missed by, so it is read for today's
    window only rather than arriving with a week of history to summarise.

    Today's own morning archive is ignored by default, so re-running by hand on
    a day that has already gone out rebuilds that day in full rather than
    reporting an empty window because everything in it was already sent. An
    update run (--since-last-run) passes include_today=True and gets the
    opposite: it continues from wherever the morning stopped.
    """
    archive = archive or ARCHIVE
    paths = sorted(glob.glob(os.path.join(archive, "*-pack.json")))[-HISTORY_DAYS:]
    today = os.path.join(archive, f"{datetime.now().strftime('%Y-%m-%d')}-pack.json")
    latest, seen, last_tickers, watched_since = None, set(), None, {}
    for path in paths:
        if not include_today and os.path.abspath(path) == os.path.abspath(today):
            continue
        if not os.path.exists(path.replace("-pack.json", "-email.html")):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                pack = json.load(fh)
        except (OSError, ValueError):
            continue
        for a in pack.get("announcements") or []:
            if a.get("document_key"):
                seen.add(a["document_key"])
        try:
            end = datetime.fromisoformat(pack["window_end_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if latest is None or end > latest:
            latest = end
            last_tickers = pack.get("all_tickers")
        # When each name joined the list: the start of the earliest window
        # that was watching it. The lookback may only recover items lodged
        # after that, otherwise a name added yesterday arrives with a week of
        # history that was never missed, merely never watched. On 3 September
        # 2026 that was 82 items in one morning.
        try:
            start = datetime.fromisoformat(pack["window_start_utc"])
        except (KeyError, TypeError, ValueError):
            start = end
        for code in pack.get("all_tickers") or []:
            if code not in watched_since or start < watched_since[code]:
                watched_since[code] = start
    return latest, seen, last_tickers, watched_since


SUBJECT_MAX = 72
SUBJECT_SUFFIX = " | DCP ASX Watchlist"


def build_subject(briefing, pack):
    """The subject is the day's headline. Nothing else.

    It used to read "ASX Watchlist, 25 August 2026: 3 items", which is identical
    on the morning a name is taken over and on a day of routine drilling. There
    is no standing prefix now: the line is the news, the way a wire headline is.
    The model writes it as part of the framing; if it does not, the most
    material announcement is used, which is the first row, because rows are
    ranked by materiality.
    """
    lead = (briefing.get("subject") or "").strip()
    rows = briefing.get("rows") or []
    if not lead and rows:
        top = rows[0]
        lead = f"{top.get('ticker', '')} {top.get('announcement', '')}".strip()

    if not lead:
        date = (pack.get("date_awst") or "").split()
        when = f"{date[0]} {date[1][:3]}" if len(date) == 3 else ""
        lead = f"No confirmed announcements{', ' + when if when else ''}"
    elif len(lead) > SUBJECT_MAX:
        lead = lead[:SUBJECT_MAX].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    return f"{lead}{SUBJECT_SUFFIX}"


# ------------------------------------------------------------------- timing

class Phases:
    """Wall-clock per phase, printed as it goes and summed at the end.

    The question "is the run getting slower as names are added" should be
    answered by the log, not by a feeling. Every phase prints its own line and
    the summary at the end is one line to compare across days.
    """

    def __init__(self):
        self.times = []

    @contextmanager
    def __call__(self, name):
        t0 = time.monotonic()
        try:
            yield
        finally:
            dt = time.monotonic() - t0
            self.times.append((name, dt))
            print(f"  [{name}: {dt:.0f}s]")

    def summary(self):
        total = sum(t for _, t in self.times)
        parts = ", ".join(f"{n} {t:.0f}s" for n, t in self.times)
        return f"timings: {total / 60:.1f} min working ({parts})"


# --------------------------------------------------------------------- hold

# Never hold longer than this. The gate only starts a run within 45 minutes of
# the send time, so a longer wait means something is wrong with the clock or
# the argument, and a briefing sent late beats a job that sits for hours.
MAX_HOLD_MIN = 75


def _target(hhmm, minus_minutes=0):
    hour, minute = (int(x) for x in hhmm.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if minus_minutes:
        target -= timedelta(minutes=minus_minutes)
    return now, target


def hold_until(hhmm, minus_minutes=0, what="send"):
    """Wait until a wall-clock time, if it is still ahead. True if it was.

    The finished email is built as soon as the run starts and held until the
    minute. The top-up between the two holds is what stops the early build
    from costing anything: see the module docstring.
    """
    if not hhmm:
        return False
    try:
        now, target = _target(hhmm, minus_minutes)
    except ValueError:
        print(f"  ! --send-at {hhmm!r} is not HH:MM, not holding")
        return False
    wait = (target - now).total_seconds()
    if wait <= 0:
        print(f"  it is {now.strftime('%H:%M:%S')}, past the {what} time "
              f"{target.strftime('%H:%M')}. Not holding.")
        return False
    if wait > MAX_HOLD_MIN * 60:
        print(f"  ! {target.strftime('%H:%M')} is {wait / 60:.0f} min away, more "
              f"than the {MAX_HOLD_MIN} minute limit. Not holding.")
        return False
    print(f"  {now.strftime('%H:%M:%S')}, holding {wait / 60:.1f} min for the "
          f"{what} time {target.strftime('%H:%M')}.")
    time.sleep(wait)
    return True


def before(hhmm):
    """True if the wall clock has not yet reached HH:MM today."""
    if not hhmm:
        return False
    try:
        now, target = _target(hhmm)
    except ValueError:
        return False
    return now < target


# -------------------------------------------------------------------- build

def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _summarise_cached(records, cache, summarise_items):
    """summarise_items(), but never twice for the same document.

    The top-up rebuilds the whole briefing from the union of both collections.
    Every summary from the first build is reused by document key, so the
    second build pays only for what the top-up found. Order follows `records`,
    which rank() has already sorted by materiality.
    """
    todo = [r for r in records if r["document_key"] not in cache]
    for s in summarise_items(todo):
        cache[s["document_key"]] = s
    return [cache[r["document_key"]] for r in records if r["document_key"] in cache]


def build(pack, cache):
    """Score, summarise, synthesise and render one pack. Returns (briefing, html, plain).

    Pure in the sense that matters: the same pack and the same cache give the
    same briefing. That is what lets the top-up run it a second time on a
    bigger pack and get a consistent email rather than a patched one.
    """
    from score import rank
    from summarise import summarise_items, synthesise
    from fmt import enrich, add_links
    from verify import audit
    from render_email import render

    ranked = rank(pack["announcements"])
    print(f"material: {len(ranked['full'])}, routine: {len(ranked['digest'])}")

    # One API call per announcement, so no announcement can see another's
    # figures. Only the closing synthesis sees the whole day, and it works from
    # summaries that have already been checked.
    for r in ranked["full"]:
        r["tier"] = "full"
    for r in ranked.get("periodic") or []:
        r["tier"] = "quarterly"          # the one-line desk-note writing style

    materials = _summarise_cached(ranked["full"], cache, summarise_items)
    quarterlies = _summarise_cached(ranked.get("periodic") or [], cache, summarise_items)
    print(f"summarised: {len(materials)} confirmed, {len(quarterlies)} periodic")

    # Every figure must trace back to the announcement it came from.
    problems = audit(materials) + audit(quarterlies)
    for p in problems:
        print(f"  ! UNVERIFIED FIGURES {p['ticker']}: {', '.join(p['figures'])}"
              f"  ({p['headline'][:44]})")
    if problems:
        print(f"  ! {len(problems)} item(s) contain figures not found in their "
              f"source. Check before relying on them.")

    restated = [q['ticker'] for q in materials + quarterlies if q.get("is_restatement")]
    if restated:
        print(f"  restatements flagged: {', '.join(restated)}")

    framing = synthesise(materials, quarterlies, pack)

    briefing = dict(framing)
    briefing["rows"] = [{k: m.get(k) for k in
                         ("ticker", "company", "announcement", "type", "date",
                          "document_key")} for m in materials]
    briefing["summaries"] = [{k: m.get(k) for k in
                              ("ticker", "heading", "body", "document_key")}
                             for m in materials]
    briefing["other"] = [{k: q.get(k) for k in
                          ("ticker", "company", "headline", "summary",
                           "document_key")} for q in quarterlies]
    briefing["_unverified"] = problems

    # Everything collected but not written up: marketing decks and routine
    # filings alike. 82 routine items across the archive to date were collected,
    # scored, and then rendered nowhere at all. Listing them by headline costs
    # four lines and means every announcement the collector saw appears
    # somewhere in the email.
    briefing["also_lodged"] = [
        {k: r.get(k) for k in ("ticker", "company", "headline", "document_key")}
        for r in ((ranked.get("presentation") or []) + (ranked.get("digest") or []))
    ]

    # An item whose summary call failed (rate limit, outage, a document the
    # model choked on) used to vanish: it was in no tier's output and so in no
    # section. It is listed here by headline instead, marked, so a failed call
    # costs a summary and never an announcement.
    unsummarised = [r for r in (ranked["full"] + (ranked.get("periodic") or []))
                    if r["document_key"] not in cache]
    if unsummarised:
        print(f"  ! {len(unsummarised)} announcement(s) could not be summarised and "
              f"are listed under Also Lodged by headline: "
              f"{', '.join(r['ticker'] for r in unsummarised)}")
        briefing["also_lodged"] = [
            {"ticker": r.get("ticker"), "company": r.get("company"),
             "headline": f"{r.get('headline')} (summary unavailable, see document)",
             "document_key": r.get("document_key")}
            for r in unsummarised
        ] + briefing["also_lodged"]

    # The commodity split is decided in config and carried on the pack so a
    # rebuild from the archive draws the same panels. The renderer falls back
    # to the classic layout if either key is missing or does not add up.
    briefing["commodities"] = pack.get("commodities") or []
    briefing["commodity_of"] = pack.get("commodity_of") or {}

    briefing["other"] = enrich(briefing["other"], pack["announcements"])
    add_links(briefing["rows"], pack["announcements"])
    add_links(briefing["summaries"], pack["announcements"])
    add_links(briefing["also_lodged"], pack["announcements"])

    html, plain = render(briefing, pack)
    return briefing, html, plain


def merge_topup(pack, delta):
    """Fold a top-up collection into the day's pack, in place.

    The announcements are appended (they are new by construction: the top-up
    was given every key already in the pack), the window end moves to the
    top-up's end so tomorrow starts there, and the top-up's own diagnostics are
    kept under their own key so the log of what happened when survives.
    """
    pack["announcements"].extend(delta["announcements"])
    # Same order collect() produces, newest first, so a rebuild from this pack
    # is indistinguishable from one collected in a single pass.
    pack["announcements"].sort(key=lambda r: r.get("lodged_utc") or "", reverse=True)
    for key in ("window_end_awst", "window_end_utc", "date_awst"):
        pack[key] = delta[key]
    try:
        start = datetime.fromisoformat(pack["window_start_utc"])
        end = datetime.fromisoformat(pack["window_end_utc"])
        pack["window_hours"] = round((end - start).total_seconds() / 3600, 2)
    except (KeyError, TypeError, ValueError):
        pass
    pack["sweep_missed"] = (pack.get("sweep_missed") or []) + (delta.get("sweep_missed") or [])
    pack["recovered"] = (pack.get("recovered") or []) + (delta.get("recovered") or [])
    pack["topup"] = {
        "from_utc": delta["window_start_utc"],
        "to_utc": delta["window_end_utc"],
        "found": len(delta["announcements"]),
        "feeds_rechecked": delta.get("tickers_checked", 0),
        "feed_errors": delta.get("feed_errors") or {},
    }


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build but do not send")
    ap.add_argument("--no-llm", action="store_true", help="collect only")
    ap.add_argument("--pack", help="rebuild from a saved evidence pack")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--send-at", metavar="HH:MM",
                    help="hold the finished email until this local time")
    ap.add_argument("--topup-lead", type=int, metavar="MIN",
                    default=int(env("TOPUP_LEAD_MIN", "6") or 6),
                    help="minutes before --send-at to sweep again (0 disables)")
    ap.add_argument("--since-last-run", action="store_true",
                    help="update run: only what has been lodged since the last "
                         "briefing, including this morning's; sends immediately")
    args = ap.parse_args()
    if args.since_last_run and args.send_at:
        print("  update run: sending as soon as it is built, not holding for "
              f"{args.send_at}")
        args.send_at = None
    phases = Phases()

    tickers = load_watchlist()
    commodities, notes = load_commodities()
    commodity_of, tag_notes = load_watchlist_tags(commodities=commodities)
    for note in notes + tag_notes:
        print(f"  ! {note}")
    recipients = [] if (args.dry_run or args.no_llm) else load_recipients()
    print(f"watchlist: {len(tickers)} codes"
          + (f", split into {', '.join(label for _, label in commodities)}"
             if commodities else ""))

    # ---------------------------------------------------------------- collect
    since = already_seen = None
    if args.pack:
        with open(args.pack, encoding="utf-8") as fh:
            pack = json.load(fh)
        print(f"loaded pack: {args.pack}")
    else:
        from collect import collect
        since, already_seen, last_tickers, watched_since = previous_run(
            include_today=args.since_last_run)
        if since:
            print(f"last briefing covered up to {since.isoformat()}")
        hours = args.hours
        if args.since_last_run and since:
            # An update is "since the last one", so the window starts exactly
            # there rather than reaching back a full day. The lookback still
            # runs behind it, so nothing the morning missed is lost.
            hours = 0
        # The seven-day lookback applies to names that were on the list last
        # time, and only from the day they joined it. A name added today is
        # read for today's window only.
        lookback_codes = [t for t in tickers if t in watched_since]
        if len(lookback_codes) < len(tickers):
            print(f"  {len(tickers) - len(lookback_codes)} name(s) are new to the "
                  f"list today and are read for the window only, not the lookback.")
        with phases("collect"):
            pack = collect(tickers, hours=hours, since=since,
                           already_seen=already_seen, lookback_codes=lookback_codes,
                           watched_since=watched_since)
        print(f"window: {pack['window_start_awst'][:16]} to "
              f"{pack['window_end_awst'][:16]} AWST "
              f"({pack.get('window_hours', args.hours)}h)")

        # collect() already excludes anything reported by an earlier briefing.
        # This is the belt to that pair of braces: if a key slips through, drop
        # it here and say so, because a repeat is a visible fault and a missing
        # item is not, and the check is free.
        repeats = [a for a in pack["announcements"]
                   if a.get("document_key") in already_seen]
        if repeats:
            keys = {a["document_key"] for a in repeats}
            pack["announcements"] = [a for a in pack["announcements"]
                                     if a.get("document_key") not in keys]
            print(f"  ! skipped {len(repeats)} already reported in an earlier "
                  f"briefing: {', '.join(sorted({a['ticker'] for a in repeats}))}")
        print(f"found {len(pack['announcements'])} new announcements in window"
              + (f" ({len(pack.get('recovered') or [])} recovered from earlier days)"
                 if pack.get("recovered") else ""))

    pack["all_tickers"] = tickers
    pack["commodities"] = [list(c) for c in commodities]
    pack["commodity_of"] = commodity_of
    # An update run gets its own files beside the morning's rather than over
    # them: the morning's pack is the record of what was sent at 08:10, and
    # tomorrow reads every pack, so both are remembered.
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M" if args.since_last_run
                                    else "%Y-%m-%d")
    os.makedirs(ARCHIVE, exist_ok=True)
    pack_path = os.path.join(ARCHIVE, f"{stamp}-pack.json")
    briefing_path = os.path.join(ARCHIVE, f"{stamp}-briefing.json")
    email_path = os.path.join(ARCHIVE, f"{stamp}-email.html")
    _write_json(pack_path, pack)
    print(f"evidence pack: {pack_path}")

    unread = [a for a in pack["announcements"] if a.get("text_status") != "ok"]
    for a in unread:
        print(f"  ! could not read {a['ticker']} {a['headline'][:48]!r}: {a['text_status']}")

    if args.no_llm:
        from score import rank
        ranked = rank(pack["announcements"])
        print(f"material: {len(ranked['full'])}, routine: {len(ranked['digest'])}")
        print(phases.summary())
        return 0

    # ------------------------------------------------------------------ build
    cache = {}
    with phases("build"):
        briefing, html, plain = build(pack, cache)
    _write_json(briefing_path, briefing)
    _write_text(email_path, html)
    subject = build_subject(briefing, pack)

    if args.dry_run:
        # A dry run never emails anyone, but if the Graph transport is set up
        # it does fetch a token, so wrong tenant, client or secret values fail
        # here rather than at 08:10 tomorrow. Older send.py has no Graph.
        try:
            from send import send as _send, graph_configured
        except ImportError:
            graph_configured = None
        if graph_configured and graph_configured():
            _send(html, plain, subject, load_recipients(), dry_run=True,
                  logo=os.path.join(ROOT, "dcp", "assets", "logo-dcp-white.png"))
        print(f"dry run, would send to {len(load_recipients())} recipients: {subject}")
        print(phases.summary())
        return 0

    # ----------------------------------------------------------------- top-up
    # Only on a scheduled morning run: a rebuild from a pack has no live window
    # to extend, and a run that is already past the send time has a cutoff
    # later than the send time anyway.
    if args.send_at and not args.pack and args.topup_lead > 0:
        hold_until(args.send_at, minus_minutes=args.topup_lead, what="top-up")
        if before(args.send_at):
            with phases("top-up"):
                try:
                    from collect import collect, retryable_feed_errors
                    seen_now = set(already_seen or ()) | {
                        a["document_key"] for a in pack["announcements"]}
                    recheck = retryable_feed_errors(pack.get("feed_errors"))
                    if recheck:
                        print(f"  re-checking {len(recheck)} feed(s) that errored "
                              f"earlier: {', '.join(recheck)}")
                    delta = collect(tickers, hours=0,
                                    since=datetime.fromisoformat(pack["window_end_utc"]),
                                    already_seen=seen_now, company_codes=recheck,
                                    strict=False)
                    found = delta["announcements"]
                    print(f"  top-up {delta['window_start_awst'][11:16]} to "
                          f"{delta['window_end_awst'][11:16]} AWST found "
                          f"{len(found)} new announcement(s)"
                          + (": " + ", ".join(sorted({a['ticker'] for a in found}))
                             if found else ""))
                    merge_topup(pack, delta)
                    if found:
                        briefing, html, plain = build(pack, cache)
                        subject = build_subject(briefing, pack)
                    # The window end moved either way, so tomorrow starts here.
                    _write_json(pack_path, pack)
                    _write_json(briefing_path, briefing)
                    _write_text(email_path, html)
                except Exception as exc:                          # noqa: BLE001
                    # The first build is complete and correct up to its own
                    # cutoff. Send it. Whatever the top-up would have found is
                    # inside tomorrow's window and lookback.
                    print(f"  ! top-up failed, sending the briefing built earlier: "
                          f"{type(exc).__name__}: {exc}")
        else:
            print("  past the send time, so no top-up: the collection cutoff is "
                  "already later than the send time.")

    # ------------------------------------------------------------------- send
    from send import send
    hold_until(args.send_at, what="send")
    with phases("send"):
        send(html, plain, subject, recipients,
             logo=os.path.join(ROOT, "dcp", "assets", "logo-dcp-white.png"))
    print(f"sent {datetime.now().strftime('%H:%M:%S')} to {len(recipients)} "
          f"recipients: {subject}")
    print(phases.summary())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as exc:
        print(f"\nCONFIG PROBLEM\n{exc}\n", file=sys.stderr)
        sys.exit(2)
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("\nRun failed. No briefing was sent.", file=sys.stderr)
        sys.exit(1)

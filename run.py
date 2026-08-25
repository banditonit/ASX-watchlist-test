#!/usr/bin/env python3
"""Daily ASX watchlist briefing.

  python run.py                 collect, summarise, build, email
  python run.py --dry-run       everything except sending
  python run.py --no-llm        collect only, write the evidence pack
  python run.py --pack FILE     rebuild from a saved pack, no network

Exits non-zero if anything failed, so the scheduler reports it rather than a
clean-looking empty briefing going out unnoticed.
"""

import argparse
import glob
import json
import os
import sys
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from config import load_recipients, load_watchlist, ConfigError, env  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(ROOT, "archive")


# How many archived days to read back when working out what has already been
# reported. Comfortably more than any window, cheap to scan, and short enough
# that it can never suppress something from a previous month.
HISTORY_DAYS = 10


def previous_run(archive=None):
    """Where the last briefing stopped, and every document it already covered.

    Only packs that produced an email count. A pack is written before the
    summaries are built, so a run that collected announcements and then died
    would otherwise mark them as reported and they would never be seen. The
    rendered email beside it is the proof the day actually went out.
    """
    archive = archive or ARCHIVE
    paths = sorted(glob.glob(os.path.join(archive, "*-pack.json")))[-HISTORY_DAYS:]
    # Today's own archive is ignored, so re-running by hand on a day that has
    # already gone out rebuilds that day in full rather than reporting an empty
    # window because everything in it was already sent.
    today = os.path.join(archive, f"{datetime.now().strftime('%Y-%m-%d')}-pack.json")
    latest, seen = None, set()
    for path in paths:
        if os.path.abspath(path) == os.path.abspath(today):
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
        latest = end if latest is None else max(latest, end)
    return latest, seen


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build but do not send")
    ap.add_argument("--no-llm", action="store_true", help="collect only")
    ap.add_argument("--pack", help="rebuild from a saved evidence pack")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    tickers = load_watchlist()
    recipients = [] if (args.dry_run or args.no_llm) else load_recipients()
    print(f"watchlist: {len(tickers)} codes")

    # ---------------------------------------------------------------- collect
    if args.pack:
        with open(args.pack, encoding="utf-8") as fh:
            pack = json.load(fh)
        print(f"loaded pack: {args.pack}")
    else:
        from collect import collect
        since, already_seen = previous_run()
        if since:
            print(f"last briefing covered up to {since.isoformat()}")
        pack = collect(tickers, hours=args.hours, since=since)
        print(f"window: {pack['window_start_awst'][:16]} to "
              f"{pack['window_end_awst'][:16]} AWST "
              f"({pack.get('window_hours', args.hours)}h)")

        # The window deliberately overlaps the previous one so nothing can fall
        # between two runs. Anything already reported is dropped here, by
        # document key, so the overlap never shows up as a repeat.
        repeats = [a for a in pack["announcements"]
                   if a.get("document_key") in already_seen]
        if repeats:
            keys = {a["document_key"] for a in repeats}
            pack["announcements"] = [a for a in pack["announcements"]
                                     if a.get("document_key") not in keys]
            print(f"skipped {len(repeats)} already reported in an earlier "
                  f"briefing: {', '.join(sorted({a['ticker'] for a in repeats}))}")
        print(f"found {len(pack['announcements'])} new announcements in window")

    pack["all_tickers"] = tickers
    stamp = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(ARCHIVE, exist_ok=True)
    pack_path = os.path.join(ARCHIVE, f"{stamp}-pack.json")
    with open(pack_path, "w", encoding="utf-8") as fh:
        json.dump(pack, fh, indent=2, ensure_ascii=False)
    print(f"evidence pack: {pack_path}")

    unread = [a for a in pack["announcements"] if a.get("text_status") != "ok"]
    for a in unread:
        print(f"  ! could not read {a['ticker']} {a['headline'][:48]!r}: {a['text_status']}")

    # ------------------------------------------------------------------ score
    from score import rank
    ranked = rank(pack["announcements"])
    print(f"material: {len(ranked['full'])}, routine: {len(ranked['digest'])}")

    if args.no_llm:
        return 0

    # -------------------------------------------------------------- summarise
    # One API call per announcement, so no announcement can see another's
    # figures. Only the closing synthesis sees the whole day, and it works from
    # summaries that have already been checked.
    from summarise import summarise_items, synthesise
    from fmt import enrich, add_links
    from verify import audit

    for r in ranked["full"]:
        r["tier"] = "full"
    for r in ranked.get("periodic") or []:
        r["tier"] = "quarterly"          # the one-line desk-note writing style

    materials = summarise_items(ranked["full"])
    quarterlies = summarise_items(ranked.get("periodic") or [])
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

    # Marketing decks are not summarised, but they are named. A filter that
    # drops things silently is indistinguishable from a filter that is broken,
    # which is how the Ramelius resource and reserve update went missing on
    # 25 August 2026 without anyone being able to see that it had. The reader
    # gets one line per suppressed deck at the foot of the email.
    # Everything collected but not written up: marketing decks and routine
    # filings alike. 82 routine items across the archive to date were collected,
    # scored, and then rendered nowhere at all. Listing them by headline costs
    # four lines and means every announcement the collector saw appears
    # somewhere in the email.
    briefing["also_lodged"] = [
        {k: r.get(k) for k in ("ticker", "company", "headline", "document_key")}
        for r in ((ranked.get("presentation") or []) + (ranked.get("digest") or []))
    ]

    briefing["other"] = enrich(briefing["other"], pack["announcements"])
    add_links(briefing["rows"], pack["announcements"])
    add_links(briefing["summaries"], pack["announcements"])
    add_links(briefing["also_lodged"], pack["announcements"])

    with open(os.path.join(ARCHIVE, f"{stamp}-briefing.json"), "w", encoding="utf-8") as fh:
        json.dump(briefing, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ build
    from render_email import render

    html, plain = render(briefing, pack)
    with open(os.path.join(ARCHIVE, f"{stamp}-email.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    # ------------------------------------------------------------------- send
    subject = build_subject(briefing, pack)

    if args.dry_run:
        print(f"dry run, would send to {len(load_recipients())} recipients: {subject}")
        return 0

    from send import send
    send(html, plain, subject, recipients,
         logo=os.path.join(ROOT, "dcp", "assets", "logo-dcp-white.png"))
    print(f"sent to {len(recipients)} recipients: {subject}")
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

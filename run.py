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
import json
import os
import sys
import traceback
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from config import load_recipients, load_watchlist, ConfigError, env  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(ROOT, "archive")


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
        pack = collect(tickers, hours=args.hours)
        print(f"found {len(pack['announcements'])} announcements in window")

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
    from summarise import summarise
    from fmt import enrich

    briefing = summarise(pack, ranked)

    # Market cap is joined on from collected data rather than written by the
    # model, then the quarterlies are ordered largest first.
    briefing["quarterlies"] = enrich(briefing.get("quarterlies") or [],
                                     pack["announcements"])

    with open(os.path.join(ARCHIVE, f"{stamp}-briefing.json"), "w", encoding="utf-8") as fh:
        json.dump(briefing, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ build
    from build_pdf import build
    from render_email import render

    dd = datetime.now().strftime("%d%b%y").lower()
    pdf_path = os.path.join(ARCHIVE, f"watchlist_catchup_{dd}.pdf")
    build(briefing, pack, pdf_path)
    print(f"pdf: {pdf_path}")

    html, plain = render(briefing, pack, pdf_name=os.path.basename(pdf_path))
    with open(os.path.join(ARCHIVE, f"{stamp}-email.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    # ------------------------------------------------------------------- send
    n = len(briefing.get("rows") or [])
    subject = (f"ASX Watchlist, {pack['date_awst']}: "
               + (f"{n} item{'s' if n != 1 else ''}" if n else "nothing confirmed"))

    if args.dry_run:
        print(f"dry run, would send to {len(load_recipients())} recipients: {subject}")
        return 0

    from send import send
    send(html, plain, subject, recipients,
         attachments=[pdf_path],
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

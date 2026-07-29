"""Builds the A4 briefing PDF using the vendored DCP report engine."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "dcp"))

from dcp_report import DCPReport, DEFAULT_CONTACTS, CONTENT_W  # noqa: E402

COL_WIDTHS = [0.62, 1.72, 3.40, 1.05, 0.95]


def build(briefing, pack, out_path):
    r = DCPReport(out_path, subject_logo=None)
    date = pack.get("date_awst", "")
    rows = briefing.get("rows") or []
    unread = [a for a in pack["announcements"] if a.get("text_status") != "ok"]

    r.cover(
        title="ASX Watchlist Catch Up",
        subtitle=f"Daily Announcements Briefing, {date}",
        confidential="Internal Use Only",
        blurb=(
            f"This briefing covers confirmed ASX announcements across Discovery's "
            f"{pack['tickers_checked']} ticker watchlist for the 24 hours to "
            f"{pack['window_end_awst'][11:16]} AWST on {date}."
            "\n\n"
            "Every item is traced to a dated primary announcement retrieved from the "
            "ASX announcements platform, and the text of each material announcement "
            "was read in full. News coverage of older announcements is excluded."
        ),
    )

    r.page("Confirmed Announcements", style="band")
    r.text(briefing.get("lead", ""))

    if rows:
        data = [["Ticker", "Company", "Announcement", "Type", "Date"]]
        data += [[x.get("ticker", ""), x.get("company", "")[:34],
                  x.get("announcement", "")[:52], x.get("type", ""), x.get("date", "")]
                 for x in rows]
        r.table(data, col_widths=COL_WIDTHS, fit=True)

    r.source(
        "ASX company announcements platform, retrieved directly from the exchange "
        f"announcements feed at {pack['window_end_awst'][11:16]} AWST on {date}."
    )
    keys = "; ".join(
        f"{a['ticker']} {a['document_key']} ({a['time_awst']} AWST)"
        for a in pack["announcements"][:14]
    )
    if keys:
        r.caption("Document keys and lodgement times: " + keys)

    cards = []
    if briefing.get("watch_items"):
        cards.append({"heading": "Watch items", "bullets": briefing["watch_items"]})
    if briefing.get("unconfirmed"):
        cards.append({"heading": "Unconfirmed", "bullets": briefing["unconfirmed"]})
    if cards:
        r.cards_row(cards)

    summaries = briefing.get("summaries") or []
    if summaries:
        r.page("Announcement Detail", style="band")
        for s in summaries:
            r.card(f"{s.get('ticker','')}: {s.get('heading','')}",
                   body=s.get("body", ""), w=CONTENT_W)

    quarterlies = briefing.get("quarterlies") or []
    if quarterlies:
        r.page("Quarterlies",
               subtitle=("Quarterly activities and cash flow reports lodged in the "
                         "window, with production, costs and cash position."),
               style="title")
        lines = []
        for q in quarterlies:
            cap = f" ({q['cap_label']})" if q.get("cap_label") else ""
            lines.append(f"<b>{q.get('ticker','')}{cap}:</b> {q.get('summary','')}")
        r.bullets(lines)

    r.page("Coverage Notes", subtitle=briefing.get("subtitle", ""), style="title")
    covered = {a["ticker"] for a in pack["announcements"]}
    quiet = [t for t in pack["all_tickers"] if t not in covered]
    if quiet:
        r.text(f"No dated announcement was found inside the window for the "
               f"remaining {len(quiet)} names:")
        r.text(", ".join(quiet), size=9)

    method = [
        "Every ticker on the watchlist was checked against the ASX announcements "
        "feed for an explicit lodgement timestamp inside the window. The feed is "
        "the exchange's own, so coverage does not depend on a search engine "
        "surfacing the item.",
        "Materiality was assessed on the text of each announcement, not its "
        "headline, so that a routine-sounding title cannot conceal a resource "
        "estimate, a guidance change or a transaction.",
        "Halts and price-sensitive announcements were opened and read for their "
        "stated reason and headline figures. This is true of every item reported.",
        "All dates are AWST, computed as UTC+8 from the exchange's own timestamp.",
    ]
    if unread:
        method.append(
            "Documents that could not be read are named in Unconfirmed rather than "
            f"omitted. {len(unread)} could not be parsed on this run."
        )
    r.card("Method", bullets=method, w=CONTENT_W)

    r.closing(
        summary_title="Day in Brief",
        summary=briefing.get("day_in_brief", ""),
        subject="any company referenced",
        intro=("This briefing is an internal working document prepared for the "
               "Discovery team. Questions on coverage, sources or method should be "
               "directed to one of the representatives of Discovery below."),
        contacts=DEFAULT_CONTACTS,
    )
    r.save(quiet=True)
    return out_path

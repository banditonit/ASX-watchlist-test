"""Renders the briefing as an HTML email in Discovery house style.

Written for email clients, not browsers. That means tables for layout, inline
styles only, no flexbox, no grid, no external stylesheet and no background
images, because Outlook renders with Word's engine and silently discards most
modern CSS. The DCP mark is attached and referenced by content ID rather than
base64, which Outlook blocks.
"""

import re
from datetime import datetime
from html import escape

NAVY = "#002B56"
GREY = "#E6E7E8"
GOLD = "#BA9C67"
ICE = "#C9E4FF"
MUTED = "#585858"
WHITE = "#FFFFFF"

FONT = "'Open Sans','Segoe UI',Helvetica,Arial,sans-serif"

# WIDTH is the widest the email will ever draw. Set it to 0 for full bleed, so
# the briefing fills whatever window it is opened in.
#
# PROSE is a second, tighter cap that applies only to running text. The table
# genuinely wants the room: company names and drill intercepts were wrapping
# inside a 350px column at the old 680. Paragraphs do not. A line of body text
# stretched across a 1400px monitor runs to about 190 characters, and the eye
# loses its place returning to the left margin. Newspapers set columns, and for
# the same reason. So the frame goes as wide as the window and the prose stays
# at a length that can be read.
WIDTH = 0
PROSE = 900

DISCLAIMER_HEADING = "General Advice Only"

DISCLAIMER = (
    "This email is for informational purposes only. It does not constitute "
    "investment or financial advice nor an offer to acquire a financial product. "
    "Before acting on any information contained in this email, each person should "
    "obtain independent taxation, financial and legal advice relating to this "
    "information and consider it carefully before making any decision or "
    "recommendation."
    "\n\n"
    "To the extent this email does contain advice, in preparing any such advice, "
    "we have not taken into account any particular person's objectives, financial "
    "situation or needs. Furthermore, you may not rely on this message as advice "
    "unless subsequently confirmed by letter signed by an authorised representative "
    "of Discovery Capital Partners Pty Ltd. This email and its contents are intended for wholesale "
    "investors only."
)

CONTACTS = [
    ("Adam Miethke", "Managing Director", "am@discoverycapital.com.au"),
    ("Kale Pervan", "Director", "kp@discoverycapital.com.au"),
    ("Darcy Frazer", "Associate", "df@discoverycapital.com.au"),
]


def _link(text, url, colour=NAVY, weight="bold", size=None):
    """A ticker that links to its announcement, or plain text when it cannot.

    Colour is set explicitly because email clients otherwise impose their own
    link styling, which in Outlook is a blue that is not in the house palette.
    """
    sz = f"font-size:{size}px;" if size else ""
    if not url:
        return f'<span style="font-weight:{weight};{sz}">{escape(text)}</span>'
    return (f'<a href="{escape(url, quote=True)}" style="color:{colour};'
            f'font-weight:{weight};{sz}text-decoration:underline;">{escape(text)}</a>')


def _plain_label(text, weight="bold", size=None):
    """Bold navy text that is not a link, for a label with no single target."""
    sz = f"font-size:{size}px;" if size else ""
    return (f'<span style="font-weight:{weight};{sz}color:{NAVY};">'
            f'{escape(text)}</span>')


def _p(text, size=15, colour=NAVY, weight="normal", top=0, bottom=14):
    """Render text as one or more paragraphs, preserving blank-line breaks."""
    blocks = [b.strip() for b in _text(text).split("\n\n") if b.strip()]
    if not blocks:
        return ""
    cap = f"max-width:{PROSE}px;" if PROSE else ""
    return "".join(
        f'<p style="margin:{top if i == 0 else 0}px 0 {bottom}px 0;font-family:{FONT};'
        f'font-size:{size}px;line-height:1.55;color:{colour};{cap}'
        f'font-weight:{weight};">{escape(b)}</p>'
        for i, b in enumerate(blocks)
    )


# How several announcements from one name are laid out in the Announcement
# cell. "line" gives each its own line, "pipe" runs them together separated by
# a rule. Flip this one word to change it.
MULTI_SEPARATOR = "line"

# How many columns Also Lodged uses at full desktop width.
LODGED_COLUMNS = 3


def _group_by_ticker(entries):
    """[(ticker, [entry, ...]), ...] in first-appearance order.

    Order is preserved rather than sorted, because the caller has already
    ranked by materiality: a group lands where its strongest announcement would
    have, which is where the reader expects to find the name.
    """
    order, groups = [], {}
    for entry in entries or []:
        ticker = entry.get("ticker") or ""
        if ticker not in groups:
            groups[ticker] = []
            order.append(ticker)
        groups[ticker].append(entry)
    return [(t, groups[t]) for t in order]


def _date_span(entries):
    """One date, or a range when a group straddles days.

    Only bites on a Monday, when the window reaches back 72 hours and a name
    can have lodged on Friday and again this morning.
    """
    dates = list(dict.fromkeys(
        d for d in (_text(e.get("date")) for e in entries) if d))
    if len(dates) <= 1:
        return dates[0] if dates else ""
    parsed = []
    for d in dates:
        try:
            parsed.append((datetime.strptime(d, "%d %B %Y"), d))
        except ValueError:
            return f"{dates[0]} to {dates[-1]}"
    parsed.sort()
    first, last = parsed[0][0], parsed[-1][0]
    if (first.month, first.year) == (last.month, last.year):
        return f"{first.day} to {last.day} {last.strftime('%B %Y')}"
    if first.year == last.year:
        return (f"{first.day} {first.strftime('%B')} to "
                f"{last.day} {last.strftime('%B %Y')}")
    return (f"{first.day} {first.strftime('%B %Y')} to "
            f"{last.day} {last.strftime('%B %Y')}")


def _frame_attr():
    """Outlook reads the width attribute, so give it one only when capped."""
    return f'width="{WIDTH}"' if WIDTH else 'width="100%"'


def _frame_cap():
    return f"max-width:{WIDTH}px;" if WIDTH else ""


# A paragraph of the closing summary is written as "Theme: sentences." The
# theme is set in bold so the section can be scanned rather than read whole.
# Nothing is required: a paragraph with no colon, or with a long run of text
# before one, is rendered exactly as written.
THEME = re.compile(r"^([A-Z][^:.]{2,34}):\s+(.*)$", re.S)


def _themed(text, size=15, colour=NAVY, bottom=14):
    """Render the closing summary, bolding each paragraph's opening theme."""
    blocks = [b.strip() for b in _text(text).split("\n\n") if b.strip()]
    if not blocks:
        return ""
    out = []
    for block in blocks:
        m = THEME.match(block)
        if m:
            body = (f'<span style="font-weight:bold;">{escape(m.group(1))}:</span> '
                    f'{escape(m.group(2))}')
        else:
            body = escape(block)
        out.append(
            f'<p style="margin:0 0 {bottom}px 0;font-family:{FONT};'
            f'font-size:{size}px;line-height:1.55;color:{colour};'
            f'max-width:{PROSE}px;">{body}</p>'
        )
    return "".join(out)


def _band(title, subtitle=None):
    sub = ""
    if subtitle:
        sub = (
            f'<div style="font-family:{FONT};font-size:13px;color:{ICE};'
            f'padding:4px 0 0 0;">{escape(subtitle)}</div>'
        )
    return f"""
    <tr><td style="background-color:{NAVY};padding:14px 18px;">
      <div style="font-family:{FONT};font-size:18px;font-weight:bold;
                  color:{WHITE};letter-spacing:.2px;">{escape(title)}</div>{sub}
    </td></tr>
    <tr><td style="height:16px;line-height:16px;font-size:0;">&nbsp;</td></tr>
    """


def _table(rows):
    if not rows:
        return ""
    head = "".join(
        f'<th align="left" style="font-family:{FONT};font-size:12px;'
        f'font-weight:bold;color:{WHITE};background-color:{NAVY};'
        f'padding:9px 10px;">{escape(h)}</th>'
        for h in ("Ticker", "Company", "Announcement", "Date")
    )
    body = []
    # Type is gone. It was model-assigned from a free-text label and was wrong
    # often enough to be misleading: an escrow release and a conference deck
    # both came back as "Capital Raising" on 4 August. The space it freed goes
    # to Announcement, which now carries the best drill intercept in full.
    widths = ["10%", "22%", "52%", "16%"]
    # One row per name, not per announcement. A company that lodges three
    # documents before the open is one line of the day's story, not three: on
    # 10 August 2026 Wia Gold filed a DFS, a resource upgrade and a trading
    # halt, and the table read as three unrelated companies. Every announcement
    # keeps its own link inside the cell, so merging the row loses nothing.
    for i, (ticker, items) in enumerate(_group_by_ticker(rows)):
        bg = GREY if i % 2 == 0 else WHITE
        # The link goes on the announcement, never on the ticker. Underlining
        # both put two rules on every row and four on a name that filed three
        # times, which read as clutter. The ticker is a label; the announcement
        # is the thing you click, and it is also the thing that identifies
        # which document you are opening when a name filed more than once.
        parts = [_link(_text(it.get("announcement")), it.get("url"),
                       weight="normal") for it in items]
        cells = [
            _plain_label(ticker),
            escape(_text(items[0].get("company"))),
            _join_parts(parts),
            escape(_date_span(items)),
        ]
        tds = "".join(
            f'<td width="{w}" style="font-family:{FONT};font-size:12px;'
            f'color:{NAVY};padding:9px 10px;background-color:{bg};'
            f'vertical-align:top;">{c}</td>'
            for c, w in zip(cells, widths)
        )
        body.append(f"<tr>{tds}</tr>")
    return f"""
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             border="0" style="border-collapse:collapse;">
        <tr>{head}</tr>
        {''.join(body)}
      </table>
    </td></tr>
    <tr><td style="height:20px;line-height:20px;font-size:0;">&nbsp;</td></tr>
    """


def _join_parts(parts):
    """Lay several announcements out inside one cell."""
    if len(parts) == 1:
        return parts[0]
    if MULTI_SEPARATOR == "pipe":
        return f'<span style="color:{MUTED};">&nbsp;|&nbsp;</span>'.join(parts)
    return "".join(
        f'<div style="padding-bottom:{0 if n == len(parts) - 1 else 5}px;">{p}</div>'
        for n, p in enumerate(parts))


# Second line of defence against a malformed model payload. summarise.py
# repairs the fields before they are archived; this makes sure that a leak of a
# shape nobody has seen yet cannot reach a reader's inbox, because escape()
# would otherwise render the stray markup as visible text. That is how
# "</body> <parameter name=\"is_restatement\">false" appeared at the end of the
# St Barbara summary on 5 August.
_CUT_AT = re.compile(
    r"</[a-z_][a-z0-9_.\-]*>|<parameter\b|</?(?:function_calls|invoke|tool_use)\b",
    re.I,
)
_STRAY_TAG = re.compile(r"</?[a-z_][a-z0-9_.\-]*(?:\s[^<>]*)?/?>", re.I)


def _text(value):
    """Cut at the first leaked tag, drop anything left, return plain prose."""
    text = str(value or "")
    cut = _CUT_AT.search(text)
    if cut:
        text = text[:cut.start()]
    return _STRAY_TAG.sub("", text).strip()


def _bullets(value):
    """Coerce whatever the model returned into a list of bullet strings.

    watch_items is declared as an array of strings and is normally returned as
    one. Occasionally the model answers the schema in its own tag syntax and
    the whole block arrives as a single string, which a naive `for b in bullets`
    then renders one character per bullet. That is what produced the column of
    single letters in the 4 August email. Summarise normalises the payload
    upstream; this is the second line of defence, so a malformed field can
    never again be rendered letter by letter.
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = re.findall(r"<item>(.*?)</item>", value, re.S | re.I)
        if not items:
            items = re.split(r"\n+|(?:^|\s)[-\u2022]\s+", value)
        value = items
    return [t for t in (_text(item) for item in value) if len(t) > 1]


def _window_label(pack):
    """"24 hours to 08:10 AWST", or 72 on a Monday, read from the real window."""
    start = pack.get("window_start_awst") or ""
    end = pack.get("window_end_awst") or ""
    hours = 24
    try:
        hours = round((datetime.fromisoformat(end)
                       - datetime.fromisoformat(start)).total_seconds() / 3600)
    except (TypeError, ValueError):
        pass
    return f"{hours} hours to {end[11:16]} AWST"


def _card(heading, paragraphs=None, bullets=None, url=None, link_text=None):
    head = escape(_text(heading))
    if url and link_text:
        head = head.replace(escape(link_text),
                            _link(link_text, url, size=15), 1)
    inner = [
        f'<div style="font-family:{FONT};font-size:15px;font-weight:bold;'
        f'color:{NAVY};margin-bottom:8px;">{head}</div>'
    ]
    for para in paragraphs or []:
        inner.append(_p(para, size=14, bottom=10))
    bullets = _bullets(bullets)
    if bullets:
        items = "".join(
            f'<li style="font-family:{FONT};font-size:14px;line-height:1.55;'
            f'color:{NAVY};margin-bottom:7px;">{escape(b)}</li>'
            for b in bullets
        )
        inner.append(f'<ul style="margin:0;padding-left:20px;">{items}</ul>')
    return f"""
    <tr><td style="background-color:{GREY};padding:16px 18px;">
      {''.join(inner)}
    </td></tr>
    <tr><td style="height:14px;line-height:14px;font-size:0;">&nbsp;</td></tr>
    """


def _item_card(ticker, item):
    """One announcement. The ticker labels it, the heading links to the document."""
    heading = (
        f'<div style="font-family:{FONT};font-size:15px;font-weight:bold;'
        f'color:{NAVY};margin-bottom:8px;">{_plain_label(ticker + ":", size=15)} '
        f'{_link(_text(item.get("heading")), item.get("url"), size=15)}</div>'
    )
    return f"""
    <tr><td style="background-color:{GREY};padding:16px 18px;">
      {heading}{_p(item.get("body", ""), size=14, bottom=0)}
    </td></tr>
    <tr><td style="height:14px;line-height:14px;font-size:0;">&nbsp;</td></tr>
    """


def _multi_card(ticker, items):
    """One card for a name that lodged several announcements.

    The paragraphs are the per-announcement summaries exactly as written. They
    are not re-summarised into one: each came from a call that saw only its own
    document, which is what stops a figure crossing between them. Wia Gold's
    1.95Moz Probable Reserve and its 3.78Moz resource are precisely the pair a
    single writer would confuse, so they stay in separate paragraphs written by
    separate calls, and are merged only here on the page.
    """
    # Same rule as everywhere else: the ticker is a plain label and each
    # heading links to its own document.
    inner = [
        f'<div style="font-family:{FONT};font-size:15px;font-weight:bold;'
        f'color:{NAVY};margin-bottom:12px;">{_plain_label(ticker, size=15)}</div>'
    ]
    for n, item in enumerate(items):
        inner.append(
            f'<div style="font-family:{FONT};font-size:14px;font-weight:bold;'
            f'color:{NAVY};margin:{0 if n == 0 else 15}px 0 6px 0;">'
            f'{_link(_text(item.get("heading")), item.get("url"), size=14)}</div>'
        )
        inner.append(_p(item.get("body", ""), size=14, bottom=0))
    return f"""
    <tr><td style="background-color:{GREY};padding:16px 18px;">
      {''.join(inner)}
    </td></tr>
    <tr><td style="height:14px;line-height:14px;font-size:0;">&nbsp;</td></tr>
    """


def _summary_cards(summaries):
    """A card per name. A single announcement keeps the established one-line head."""
    out = []
    for ticker, items in _group_by_ticker(summaries):
        if len(items) == 1:
            out.append(_item_card(ticker, items[0]))
        else:
            out.append(_multi_card(ticker, items))
    return out


def _subheading(text):
    """A quieter divider than the navy band, for secondary sections."""
    return f"""
    <tr><td style="padding:6px 0 10px 0;border-top:1px solid {GOLD};">
      <div style="font-family:{FONT};font-size:13px;font-weight:bold;
                  letter-spacing:1.2px;text-transform:uppercase;
                  color:{MUTED};padding-top:12px;">{escape(text)}</div>
    </td></tr>
    """


def _quarterly(ticker, items):
    """One name as a dense desk line, or several lines if it filed more than once.

        KZR ($35m)  Kalamazoo closed the June quarter with A$2.1m cash ...
                    Kalamazoo ended the June quarter with $2.06m cash ...

    Companies routinely lodge the activities report and the Appendix 5B
    cashflow as two documents. They are complementary, one operational and one
    financial, so both are kept, but under one ticker rather than as two
    apparently unrelated names. On 31 July 2026 that turned eight lines from
    six companies into six.
    """
    cap = items[0].get("cap_label") or ""
    head = f"{ticker} ({cap})" if cap else ticker
    lines = [f'{_link(head, items[0].get("url"))}'
             f'<span style="color:{MUTED};">&nbsp;&nbsp;</span>'
             f'{escape(_text(items[0].get("summary")))}']
    for item in items[1:]:
        lines.append(f'<div style="padding:5px 0 0 16px;">'
                     f'{escape(_text(item.get("summary")))}</div>')
    return f"""
    <tr><td style="font-family:{FONT};font-size:13px;line-height:1.5;
                   color:{NAVY};padding:0 0 11px 0;">
      {''.join(lines)}
    </td></tr>
    """


def _also_lodged(entries):
    """Every announcement collected but not written up, named in one line each.

    Marketing decks and routine filings both land here: Appendix 3B notices,
    director's interest changes, cleansing notices, option exercises. Across the
    archive to date 82 such items were collected, scored, and then rendered
    nowhere at all, alongside 38 suppressed decks. None of it deserves a
    summary and all of it deserves to be visible, so that every announcement the
    collector saw appears somewhere in this email and a filter that throws away
    the wrong thing can be caught by eye the next morning.
    """
    if not entries:
        return ""

    def line(entry):
        return (f'<div style="font-family:{FONT};font-size:12px;line-height:1.5;'
                f'color:{MUTED};padding-bottom:5px;">'
                f'{_plain_label(_text(entry.get("ticker")), size=12)}'
                f'<span style="color:{MUTED};">&nbsp;&nbsp;</span>'
                f'{_link(_text(entry.get("headline")), entry.get("url"), colour=MUTED, weight="normal", size=12)}'
                f"</div>")

    # Sorted by ticker so a name's filings sit together, then split down each
    # column in turn rather than across, which is how a list is read.
    entries = sorted(entries, key=lambda x: (_text(x.get("ticker")), _text(x.get("headline"))))
    # At least three lines to a column, so a short list stays a single list
    # rather than three lonely entries strung across the page.
    n = max(1, min(LODGED_COLUMNS, len(entries) // 3))
    per = -(-len(entries) // n)
    chunks = [entries[i:i + per] for i in range(0, len(entries), per)] or [[]]
    width = f"{100 // len(chunks)}%"
    cells = "".join(
        f'<td class="dcp-lodged" width="{width}" valign="top" '
        f'style="padding-right:18px;">{"".join(line(x) for x in chunk)}</td>'
        for chunk in chunks
    )
    return f"""
    {_subheading("Also Lodged")}
    <tr><td style="padding-bottom:6px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             border="0"><tr>{cells}</tr></table>
    </td></tr>
    <tr><td style="height:14px;line-height:14px;font-size:0;">&nbsp;</td></tr>
    """


def _contacts():
    cells = "".join(
        f'<td class="dcp-contact" width="33%" valign="top" '
        f'style="padding-right:12px;word-break:break-word;overflow-wrap:anywhere;">'
        f'<div style="font-family:{FONT};font-size:14px;font-weight:bold;'
        f'color:{NAVY};">{escape(n)}</div>'
        f'<div style="font-family:{FONT};font-size:12px;font-weight:bold;'
        f'color:{NAVY};padding:2px 0;">{escape(role)}</div>'
        f'<div style="font-family:{FONT};font-size:12px;color:{NAVY};">'
        f'<a href="mailto:{e}" style="color:{NAVY};text-decoration:none;">{escape(e)}</a>'
        f"</div></td>"
        for n, role, e in CONTACTS
    )
    return f"""
    <tr><td style="border-top:2px solid {GOLD};padding-top:16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>{cells}</tr>
      </table>
    </td></tr>
    """


def render(briefing, pack):
    """Return (html, plain_text) for one briefing."""
    date = pack.get("date_awst", "")
    rows = briefing.get("rows") or []
    body = []

    body.append(_band("Confirmed Announcements",
                      f"{_window_label(pack)}, {date}"))
    body.append(f'<tr><td>{_p(briefing.get("lead",""))}</td></tr>')
    body.append(_table(rows))

    body += _summary_cards(briefing.get("summaries") or [])

    # "other" is the current key; "quarterlies" is read too so an archived
    # briefing from before the rename still renders.
    other = briefing.get("other") or briefing.get("quarterlies") or []
    if other:
        body.append(_subheading("Other"))
        for ticker, items in _group_by_ticker(other):
            body.append(_quarterly(ticker, items))

    if briefing.get("watch_items"):
        body.append(_card("Watch items", bullets=briefing["watch_items"]))
    body.append(_band("Day in Brief"))
    body.append(f'<tr><td>{_themed(briefing.get("day_in_brief", ""))}</td></tr>')

    body.append(_also_lodged(briefing.get("also_lodged")
                             or briefing.get("not_summarised") or []))
    body.append(_contacts())
    paras = "".join(
        f'<div style="font-family:{FONT};font-size:10px;line-height:1.5;'
        f'color:{MUTED};padding-bottom:7px;">{escape(b)}</div>'
        for b in DISCLAIMER.split("\n\n")
    )
    body.append(
        f'<tr><td style="padding-top:16px;">'
        f'<div style="font-family:{FONT};font-size:11px;font-weight:bold;'
        f'color:{NAVY};padding-bottom:6px;">{escape(DISCLAIMER_HEADING)}</div>'
        f"{paras}</td></tr>"
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  /* Phones only. Outlook on the desktop ignores embedded styles and is never
     narrow enough to need this. Three contacts side by side cannot fit on a
     390px screen without their email addresses setting a floor under the whole
     layout, which is what pushed the briefing off the right edge on mobile. */
  @media only screen and (max-width:600px) {{
    td.dcp-contact {{ display:block !important; width:100% !important;
                      padding:0 0 12px 0 !important; }}
  }}
  /* Also Lodged runs in three columns so a 20-item list is seven lines deep
     rather than twenty. Two columns on a laptop, one on a phone. Outlook on
     the desktop ignores all of this and keeps three, which is what a window
     that wide should show. */
  @media only screen and (max-width:1000px) {{
    td.dcp-lodged {{ display:inline-block !important; width:47% !important;
                     vertical-align:top !important; }}
  }}
  @media only screen and (max-width:620px) {{
    td.dcp-lodged {{ display:block !important; width:100% !important; }}
  }}
</style>
<title>ASX Watchlist Catch Up, {escape(date)}</title></head>
<body style="margin:0;padding:0;background-color:#F4F5F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#F4F5F6;">
 <tr><td align="center" style="padding:22px 10px;">
  <table role="presentation" {_frame_attr()} cellpadding="0" cellspacing="0" border="0"
         style="width:100%;{_frame_cap()}background-color:{WHITE};">

   <tr><td style="background-color:{NAVY};padding:22px 18px 20px 18px;">
     <img src="cid:dcpmark" width="150" alt="Discovery Capital Partners"
          style="display:block;border:0;margin-bottom:14px;">
     <div style="font-family:{FONT};font-size:25px;font-weight:bold;color:{WHITE};">
       ASX Watchlist Catch Up</div>
     <div style="font-family:{FONT};font-size:14px;color:{WHITE};padding-top:5px;">
       Daily Announcements Briefing, {escape(date)}</div>
   </td></tr>

   <tr><td style="padding:22px 18px 26px 18px;">
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
       {''.join(body)}
     </table>
   </td></tr>

  </table>
 </td></tr>
</table>
</body></html>"""

    return html, _plain(briefing, pack)


def _plain(briefing, pack):
    out = [
        "ASX WATCHLIST CATCH UP",
        f"Daily Announcements Briefing, {pack.get('date_awst','')}",
        "",
        "CONFIRMED ANNOUNCEMENTS",
        _text(briefing.get("lead")),
        "",
    ]
    for ticker, items in _group_by_ticker(briefing.get("rows") or []):
        company, when = _text(items[0].get("company")), _date_span(items)
        if len(items) == 1:
            out.append(f"  {ticker}  {company}  "
                       f"{_text(items[0].get('announcement'))}  ({when})")
        else:
            out.append(f"  {ticker}  {company}  ({when})")
            out += [f"       {_text(i.get('announcement'))}" for i in items]
    out.append("")
    for ticker, items in _group_by_ticker(briefing.get("summaries") or []):
        if len(items) == 1:
            out += [f"{ticker}: {_text(items[0].get('heading'))}",
                    _text(items[0].get("body")), ""]
        else:
            out.append(ticker)
            for s in items:
                out += [f"  {_text(s.get('heading'))}", _text(s.get("body")), ""]
    other_txt = briefing.get("other") or briefing.get("quarterlies") or []
    if other_txt:
        out.append("OTHER")
        for ticker, items in _group_by_ticker(other_txt):
            out.append(f"  {ticker}  {_text(items[0].get('company'))}")
            out += [f"    {_text(q.get('summary'))}" for q in items] + [""]
    watch = _bullets(briefing.get("watch_items"))
    if watch:
        out += ["WATCH ITEMS"] + [f"  - {w}" for w in watch] + [""]
    out += ["DAY IN BRIEF", _text(briefing.get("day_in_brief")), ""]
    lodged = briefing.get("also_lodged") or briefing.get("not_summarised") or []
    if lodged:
        out += ["ALSO LODGED"]
        out += [f"  {_text(e.get('ticker'))}  {_text(e.get('headline'))}"
                for e in lodged] + [""]
    out += [
            DISCLAIMER_HEADING.upper(), DISCLAIMER]
    return "\n".join(out)

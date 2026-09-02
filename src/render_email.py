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
# the briefing fills whatever window it is opened in. 900 was chosen over both
# the original 680 and full bleed: at 680 company names and announcements wrap
# onto second lines and the same briefing runs 4,587px instead of 3,795px, and
# full bleed pulls the table columns so far apart that the eye has to travel to
# connect a ticker to its date.
#
# PROSE is a second, tighter cap that applies only to running text. The table
# genuinely wants the room: company names and drill intercepts were wrapping
# inside a 350px column at the old 680. Paragraphs do not. A line of body text
# stretched across a 1400px monitor runs to about 190 characters, and the eye
# loses its place returning to the left margin. Newspapers set columns, and for
# the same reason. So the frame goes as wide as the window and the prose stays
# at a length that can be read.
WIDTH = 900
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


def _p(text, size=15, colour=NAVY, weight="normal", top=0, bottom=14,
       cls=None, extra=""):
    """Render text as one or more paragraphs, preserving blank-line breaks."""
    blocks = [b.strip() for b in _text(text).split("\n\n") if b.strip()]
    if not blocks:
        return ""
    cap = f"max-width:{PROSE}px;" if PROSE else ""
    klass = f' class="{cls}"' if cls else ""
    return "".join(
        f'<p{klass} style="margin:{top if i == 0 else 0}px 0 {bottom}px 0;font-family:{FONT};'
        f'font-size:{size}px;line-height:1.55;color:{colour};{cap}'
        f'font-weight:{weight};{extra}">{escape(b)}</p>'
        for i, b in enumerate(blocks)
    )


# ------------------------------------------------------- short bodies for phones
#
# A phone gets about half of each summary. Not a second summary from the model:
# the cards are written lead-first, so the first sentence carries the news and
# the rest is the supporting figures and the next step. Measured over the 271
# cards in the archive, cutting at the sentence boundary closest to half the
# body lands at 48% on average, 87% of cards between 35% and 65%. Both versions are in the email; which one shows
# is decided by the client's width. Anything that cannot read the stylesheet
# (Outlook on a desktop, older webmail) sees the full version, because the
# short one is hidden inline and only the media rule below turns it on.
SHORT_TARGET = 0.50
_SENTENCE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"(])')


def _short(text):
    """Whole sentences from the start, cut at the boundary closest to half."""
    text = _text(text).replace("\n\n", " ").strip()
    if not text:
        return ""
    sentences = _SENTENCE.split(text)
    best, best_gap = sentences[0], None
    for n in range(1, len(sentences) + 1):
        candidate = " ".join(sentences[:n])
        gap = abs(len(candidate) / len(text) - SHORT_TARGET)
        if best_gap is None or gap < best_gap:
            best, best_gap = candidate, gap
    return best


def _body(text, size=14, bottom=0):
    """Full body for wide screens, short body for phones, one shown at a time."""
    full = _p(text, size=size, bottom=bottom, cls="dcp-long")
    short = _short(text)
    if not full or not short or short == _text(text).strip():
        return full
    return full + _p(short, size=size, bottom=bottom, cls="dcp-short",
                     extra="display:none;")


# How several announcements from one name are laid out in the Announcement
# cell. "line" gives each its own line, "pipe" runs them together separated by
# a rule. Flip this one word to change it.
MULTI_SEPARATOR = "line"

# How many columns Also Lodged uses, derived from the frame rather than fixed.
# At full bleed three columns are comfortable; at WIDTH 900 they are about
# 285px each and headlines like "Notification regarding unquoted securities -
# BGL" wrap onto a second line, which leaves the block ragged and the last
# column half empty. Two columns at 900 are about 430px and nothing wraps at
# all, for 51px more page. Tying this to WIDTH means it follows automatically
# if the frame is ever changed again.
LODGED_COLUMNS = None            # None derives it; set a number to override


def _lodged_columns():
    if LODGED_COLUMNS:
        return LODGED_COLUMNS
    frame = WIDTH or 1400        # full bleed: assume a normal desktop window
    if frame >= 1200:
        return 3
    if frame >= 760:
        return 2
    return 1


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


def _short_date(text):
    """'25 August 2026' -> '25 Aug'. The year is on the header already."""
    parts = _text(text).split()
    return f"{parts[0]} {parts[1][:3]}" if len(parts) >= 2 else _text(text)


def _when(entries, times, late=frozenset()):
    """'25 Aug 07:36', or a time range, or a date range across days.

    A group made entirely of items recovered by the seven-day lookback is
    prefixed "Late:". A late catch is the program working; a silent late catch
    is what makes a reader stop trusting the dates.

    The time matters: a 06:15 lodgement is pre-open and a 14:30 one landed
    mid-session, and the reader can tell whether the market has had all day to
    absorb it. It is joined on from the evidence pack by document key rather
    than carried through the model, so it cannot be paraphrased.
    """
    prefix = "Late: " if entries and all(
        e.get("document_key") in late for e in entries) else ""
    dates = list(dict.fromkeys(
        d for d in (_short_date(e.get("date")) for e in entries) if d))
    stamps = sorted({t for t in (times.get(e.get("document_key")) for e in entries) if t})
    if len(dates) > 1:
        return prefix + _date_span(entries)
    day = dates[0] if dates else ""
    if not stamps:
        return prefix + day
    if len(stamps) == 1:
        return f"{prefix}{day} {stamps[0]}"
    return f"{prefix}{day} {stamps[0]} to {stamps[-1]}"


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

# Asking for blank lines between themes is not enough. On 26 August 2026 the
# model wrote every theme correctly and then ran them together in one block,
# so only the first was set as a heading and the rest sat mid-paragraph. A
# theme label after a finished sentence is a paragraph break whether or not a
# blank line was typed, so the break is made here rather than requested.
THEME_SPLIT = re.compile(
    r"(?<=[.)\]])\s+(?=[A-Z][A-Za-z][A-Za-z ,&/-]{1,32}:\s+[A-Z0-9])"
)


def _paragraphs(text):
    """Split the closing summary into paragraphs, on blank lines or on themes."""
    out = []
    for block in (b.strip() for b in _text(text).split("\n\n")):
        if not block:
            continue
        out.extend(p.strip() for p in THEME_SPLIT.split(block) if p.strip())
    return out


def _themed_para(block, size=15, colour=NAVY, bottom=14):
    m = THEME.match(block)
    if m:
        body = (f'<span style="font-weight:bold;">{escape(m.group(1))}:</span> '
                f'{escape(m.group(2))}')
    else:
        body = escape(block)
    return (f'<p style="margin:0 0 {bottom}px 0;font-family:{FONT};'
            f'font-size:{size}px;line-height:1.55;color:{colour};">{body}</p>')


def _themed(text, size=15, colour=NAVY, bottom=14):
    """The closing summary in columns, each theme its own paragraph.

    Two columns rather than one long measure. A paragraph set across a full
    desktop window runs to about 190 characters and the eye loses its place
    coming back; in a column it is nearer 90, which reads, and the section
    finishes in half the vertical space. Newspapers reached the same answer.
    """
    blocks = _paragraphs(text)
    if not blocks:
        return ""
    paras = [_themed_para(b, size=size, colour=colour, bottom=bottom) for b in blocks]

    if len(blocks) < 2:
        return f'<div style="max-width:{PROSE}px;">{paras[0]}</div>'

    # Balance the columns by length of text, not by number of paragraphs.
    lengths = [len(b) for b in blocks]
    total, running, cut = sum(lengths), 0, len(blocks) - 1
    for i, n in enumerate(lengths[:-1]):
        running += n
        if running >= total / 2:
            cut = i + 1
            break
    left, right = paras[:cut], paras[cut:]
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td class="dcp-brief" width="50%" valign="top" style="padding-right:26px;">
        {''.join(left)}</td>
      <td class="dcp-brief" width="50%" valign="top">{''.join(right)}</td>
    </tr></table>"""


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


def _table(rows, times=None, label=None, widths=None, late=frozenset()):
    if not rows:
        return ""
    times = times or {}
    head = "".join(
        f'<th class="dcp-th" align="left" style="font-family:{FONT};font-size:12px;'
        f'font-weight:bold;color:{SECTION["headtext"]};'
        f'background-color:{SECTION["head"]};'
        f'padding:9px 10px;">{escape(h)}</th>'
        for h in ("Ticker", "Company", "Announcement", "Date")
    )
    body = []
    # Type is gone. It was model-assigned from a free-text label and was wrong
    # often enough to be misleading: an escrow release and a conference deck
    # both came back as "Capital Raising" on 4 August. The space it freed goes
    # to Announcement, which now carries the best drill intercept in full.
    widths = widths or ["10%", "22%", "52%", "16%"]
    # One row per name, not per announcement. A company that lodges three
    # documents before the open is one line of the day's story, not three: on
    # 10 August 2026 Wia Gold filed a DFS, a resource upgrade and a trading
    # halt, and the table read as three unrelated companies. Every announcement
    # keeps its own link inside the cell, so merging the row loses nothing.
    for i, (ticker, items) in enumerate(_group_by_ticker(rows)):
        bg = SECTION["alt"] if i % 2 == 0 else SECTION["base"]
        # The link goes on the announcement, never on the ticker. Underlining
        # both put two rules on every row and four on a name that filed three
        # times, which read as clutter. The ticker is a label; the announcement
        # is the thing you click, and it is also the thing that identifies
        # which document you are opening when a name filed more than once.
        parts = [_link(_text(it.get("announcement")), it.get("url"),
                       weight="normal") for it in items]
        cells = [
            _plain_label(label(ticker, items) if label else ticker),
            escape(_text(items[0].get("company"))),
            _join_parts(parts),
            escape(_when(items, times, late)),
        ]
        tds = "".join(
            f'<td class="dcp-cell {k}" width="{w}" style="font-family:{FONT};'
            f'font-size:12px;color:{NAVY};padding:9px 10px;background-color:{bg};'
            f'vertical-align:top;">{c}</td>'
            for c, w, k in zip(cells, widths, ("dcp-tk", "dcp-co", "dcp-an", "dcp-dt"))
        )
        # The row carries the colour too, so that when a phone stacks the
        # cells the band runs the full width instead of stopping where the
        # ticker and company end. Same colour as the cells; invisible on desktop.
        body.append(f'<tr style="background-color:{bg};">{tds}</tr>')
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
    # An update run's window is a few hours, and "0 hours to 13:15" is not a
    # label. Under a day it reads as the span it is.
    if hours < 20 and start and end:
        return f"{start[11:16]} to {end[11:16]} AWST"
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
    <tr><td style="background-color:{SECTION['card']};padding:16px 18px;{_edge()}">
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
    <tr><td style="background-color:{SECTION['card']};padding:16px 18px;{_edge()}">
      {heading}{_body(item.get("body", ""), size=14, bottom=0)}
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
        inner.append(_body(item.get("body", ""), size=14, bottom=0))
    return f"""
    <tr><td style="background-color:{SECTION['card']};padding:16px 18px;{_edge()}">
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


# ---------------------------------------------------------------- commodities
#
# The briefing can be split into one panel per commodity. Which panel a name
# belongs to is decided in config/watchlist.txt, never here and never by the
# model: this file only draws what it is told. If the briefing carries no
# commodity information at all (every archived day before the feature, or a
# morning where commodities.txt could not be read) the layout is exactly what
# it was before, so the feature can never be the reason an email looks wrong.
#
# Colours are keyed by the short code used in the config files. The chemical
# symbol is the badge: unambiguous, what the desk already says, and plain text
# so every mail client shows it. Pastel by request: the header row, badge and
# rule are the "solid"; the panel sits on the "wash"; alternate table rows use
# the "zebra". Text on the solid is chosen by luminance so it stays readable
# if a darker colour is ever added.
#                 solid      wash       zebra
COMMODITY = {
    "au": ("#EFDCA6", "#FDFBF3", "#FAF3E2"),     # gold
    "cu": ("#F1C8AE", "#FEF9F5", "#FBF0E8"),     # copper
    "u":  ("#BCDDC6", "#F6FBF8", "#EAF5EE"),     # uranium
    "ag": ("#D5DBE1", "#F9FAFC", "#F0F3F6"),     # silver
    "ni": ("#C8D4DB", "#F8FAFB", "#EDF2F5"),     # nickel
    "li": ("#D3C6E8", "#FBF8FD", "#F3EDFA"),     # lithium
}
DEFAULT_COMMODITY = (NAVY, "#EDEFF2", "#E0E4E9")

# The section being drawn right now. _table and the cards read their colours
# from here so a commodity block is one continuous field of its own hue. It is
# reset in a finally: block by render(), so an exception mid-panel cannot leave
# the house palette themed for whatever is drawn next.
_HOUSE = {"head": NAVY, "headtext": WHITE, "base": WHITE,
          "alt": GREY, "card": GREY, "rule": None}
SECTION = dict(_HOUSE)


def _edge():
    return f'border-left:3px solid {SECTION["rule"]};' if SECTION["rule"] else ""


def _readable_on(hex_colour):
    """Navy on a light fill, white on a dark one."""
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError, IndexError):
        return WHITE
    return NAVY if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 else WHITE


def _palette(code):
    return COMMODITY.get((code or "").lower(), DEFAULT_COMMODITY)


def _theme(code=None):
    """Point SECTION at one commodity's colours, or back at the house palette."""
    if code is None:
        SECTION.update(_HOUSE)
        return
    solid, wash, zebra = _palette(code)
    SECTION.update(head=solid, headtext=_readable_on(solid), base=WHITE,
                   alt=zebra, card=WHITE, rule=solid)


def _commodity_band(code, label, count):
    """The header inside a panel: badge, name, and how many announcements."""
    solid, _wash, _zebra = _palette(code)
    badge = (
        f'<td width="38" valign="middle" style="padding-right:10px;">'
        f'<div style="width:30px;height:30px;background:{solid};'
        f'color:{_readable_on(solid)};border-radius:5px;font-family:{FONT};'
        f'font-size:13px;font-weight:bold;text-align:center;line-height:30px;">'
        f'{escape(code)}</div></td>'
    )
    word = "announcement" if count == 1 else "announcements"
    tally = (f'<td align="right" valign="middle" style="font-family:{FONT};'
             f'font-size:12px;color:{MUTED};white-space:nowrap;">'
             f'{count} {word}</td>')
    return f"""
    <tr><td style="padding:0 0 14px 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>{badge}
          <td valign="middle" style="font-family:{FONT};font-size:20px;
              font-weight:bold;color:{NAVY};letter-spacing:.3px;">{escape(label)}</td>
          {tally}
        </tr>
      </table>
    </td></tr>
    """


def _commodity_panel(code, inner):
    """One commodity as a single tinted field: band, table and cards inside."""
    solid, wash, _zebra = _palette(code)
    return f"""
    <tr><td class="dcp-panel" style="background-color:{wash};border-left:6px solid {solid};
                   padding:14px 16px 4px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             border="0">{inner}</table>
    </td></tr>
    <tr><td style="height:18px;line-height:18px;font-size:0;">&nbsp;</td></tr>
    """


def _quiet_line(labels):
    """'No Copper or Uranium announcements in the window.' One line, never a
    panel: an empty panel is space spent saying nothing, and total silence is
    indistinguishable from a broken feed."""
    if not labels:
        return ""
    if len(labels) == 1:
        names = labels[0]
    else:
        names = ", ".join(labels[:-1]) + " or " + labels[-1]
    return f"""
    <tr><td style="font-family:{FONT};font-size:13px;color:{MUTED};
                   padding:0 0 18px 0;">No {escape(names)} announcements in the window.</td></tr>
    """


def _grouping(briefing, pack):
    """Work out the commodity split, or None to render the classic layout.

    Returns (order, code_of, label_of):
        order     commodity codes in display order, from commodities.txt
        code_of   {ticker: code} for every name on the watchlist
        label_of  {code: label}
    A ticker whose code is not in the declared order goes to the first one, so
    a stale tag can move a row but can never lose it.
    """
    declared = briefing.get("commodities") or pack.get("commodities") or []
    code_of = briefing.get("commodity_of") or pack.get("commodity_of") or {}
    order, label_of = [], {}
    for entry in declared:
        try:
            code, label = entry[0], entry[1]
        except (TypeError, IndexError, KeyError):
            continue
        if code and code not in label_of:
            order.append(code)
            label_of[code] = label or code
    if not order or not code_of:
        return None
    default = order[0]
    code_of = {t: (c if c in label_of else default) for t, c in code_of.items()}
    return order, code_of, label_of


def _grouped_body(rows, summaries, lodged_at, late, grouping):
    """The commodity panels, or None if the split would lose anything.

    The check is the point. Every row and every card handed in must come out
    in exactly one panel. If the counts disagree the caller draws the classic
    layout instead, and prints why, because a shorter email is the one failure
    this program must never produce quietly.
    """
    order, code_of, label_of = grouping
    default = order[0]

    def bucket(entry):
        return code_of.get(entry.get("ticker"), default)

    placed_rows = placed_cards = 0
    body = []
    for code in order:
        grows = [r for r in rows if bucket(r) == code]
        gcards = [c for c in summaries if bucket(c) == code]
        if not grows and not gcards:
            continue
        placed_rows += len(grows)
        placed_cards += len(gcards)
        _theme(code)
        inner = [_commodity_band(code, label_of[code], len(grows))]
        if grows:
            inner.append(_table(grows, lodged_at, late=late))
        inner += _summary_cards(gcards)
        body.append(_commodity_panel(code, "".join(inner)))
    _theme(None)

    if placed_rows != len(rows) or placed_cards != len(summaries):
        print(f"  ! commodity split placed {placed_rows}/{len(rows)} rows and "
              f"{placed_cards}/{len(summaries)} cards. Rendering ungrouped.")
        return None

    # Declared commodities that have names on the watchlist but nothing today.
    # A commodity with no names at all is not mentioned: "No Uranium
    # announcements" every morning before a single uranium name exists is noise.
    on_list = set(code_of.values())
    busy = {bucket(r) for r in rows} | {bucket(c) for c in summaries}
    quiet = [label_of[c] for c in order if c in on_list and c not in busy]
    body.append(_quiet_line(quiet))
    return body


def _cap_label(ticker, items):
    """'GGP ($9.1B)' for the ticker cell, so size is visible without a column."""
    cap = _text(items[0].get("cap_label"))
    return f"{ticker} ({cap})" if cap else ticker


def _other_rows(entries, pack):
    """Turn the recurring filings into table rows.

    The date is joined on from the evidence pack by document key, the same way
    the lodgement times are, because these entries were never given one.
    """
    dates = {a.get("document_key"): _text(a.get("date_awst"))
             for a in (pack.get("announcements") or [])}
    return [{
        "ticker": e.get("ticker"),
        "company": e.get("company"),
        "announcement": e.get("summary"),
        "date": dates.get(e.get("document_key"), ""),
        "document_key": e.get("document_key"),
        "cap_label": e.get("cap_label"),
        "url": e.get("url"),
    } for e in entries]


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
    n = max(1, min(_lodged_columns(), len(entries) // 3))
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
    lodged_at = {a.get("document_key"): _text(a.get("time_awst"))
                 for a in (pack.get("announcements") or [])}
    late = frozenset(a.get("document_key") for a in (pack.get("announcements") or [])
                     if a.get("recovered"))
    summaries = briefing.get("summaries") or []

    grouped = None
    grouping = _grouping(briefing, pack)
    if grouping:
        try:
            grouped = _grouped_body(rows, summaries, lodged_at, late, grouping)
        except Exception as exc:                                  # noqa: BLE001
            print(f"  ! commodity split failed ({exc!r}). Rendering ungrouped.")
            grouped = None
        finally:
            _theme(None)
    if grouped is not None:
        body += grouped
    else:
        body.append(_table(rows, lodged_at, late=late))
        body += _summary_cards(summaries)

    # "other" is the current key; "quarterlies" is read too so an archived
    # briefing from before the rename still renders.
    #
    # Rendered as the same table as the confirmed announcements above, one row
    # per name and one line per filing. It used to be dense prose: on 27 August
    # 2026 a single Ora Banda entry ran to 714 characters and the section was
    # longer than the announcements it was secondary to. These are recurring
    # filings. The row says whether to open the document, and the document says
    # the rest.
    other = briefing.get("other") or briefing.get("quarterlies") or []
    if other:
        body.append(_subheading("Other"))
        # A wider ticker column, because these rows carry the market cap next
        # to the code and "OBM ($3.2B)" does not fit the 10% the main table
        # uses. Without this the cell wraps to two lines and the row stops
        # being one line, which was the whole point.
        body.append(_table(_other_rows(other, pack), lodged_at,
                           label=_cap_label,
                           widths=["14%", "20%", "50%", "16%"], late=late))

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
    td.dcp-lodged {{ display:block !important; width:100% !important;
                     box-sizing:border-box !important; }}
  }}
  @media only screen and (max-width:760px) {{
    td.dcp-brief {{ display:block !important; width:100% !important;
                    padding-right:0 !important; }}
  }}
  /* The announcement tables are four columns, and on a 390px phone the Date
     column wrapped to three lines and the Company column to four. Below 600px
     each row becomes a short stack instead: ticker and company on one line,
     the announcement on the next, the time under it. Desktop is untouched. */
  /* Phones: the briefing runs edge to edge. The grey gutter, the 18px frame
     padding and the panel's coloured left rule together cost 50px of a 390px
     screen; on a phone the panel's wash colour is enough to mark the section,
     so the rule goes and the paddings shrink. Desktop keeps all of them. */
  @media only screen and (max-width:600px) {{
    td.dcp-gutter {{ padding:0 !important; }}
    td.dcp-body {{ padding:14px 10px 20px 10px !important; }}
    td.dcp-panel {{ border-left:0 !important; padding:12px 8px 2px 8px !important; }}
    th.dcp-th {{ display:none !important; }}
    td.dcp-cell {{ display:block !important; width:100% !important;
                   box-sizing:border-box !important; }}
    td.dcp-tk, td.dcp-co {{ display:inline-block !important; width:auto !important;
                            padding:9px 4px 2px 10px !important; }}
    td.dcp-co {{ padding-left:2px !important; font-size:11px !important;
                 color:#585858 !important; }}
    td.dcp-an {{ padding:0 10px 3px 10px !important; }}
    td.dcp-dt {{ padding:0 10px 9px 10px !important; font-size:11px !important;
                 color:#585858 !important; }}
    p.dcp-long {{ display:none !important; }}
    p.dcp-short {{ display:block !important; }}
  }}
</style>
<title>ASX Watchlist Catch Up, {escape(date)}</title></head>
<body style="margin:0;padding:0;background-color:#F4F5F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#F4F5F6;">
 <tr><td class="dcp-gutter" align="center" style="padding:22px 10px;">
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

   <tr><td class="dcp-body" style="padding:22px 18px 26px 18px;">
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
    lodged_at = {a.get("document_key"): _text(a.get("time_awst"))
                 for a in (pack.get("announcements") or [])}
    late = frozenset(a.get("document_key") for a in (pack.get("announcements") or [])
                     if a.get("recovered"))

    def rows_and_cards(rows, summaries):
        for ticker, items in _group_by_ticker(rows):
            company, when = _text(items[0].get("company")), _when(items, lodged_at, late)
            if len(items) == 1:
                out.append(f"  {ticker}  {company}  "
                           f"{_text(items[0].get('announcement'))}  ({when})")
            else:
                out.append(f"  {ticker}  {company}  ({when})")
                out.extend([f"       {_text(i.get('announcement'))}" for i in items])
        out.append("")
        for ticker, items in _group_by_ticker(summaries):
            if len(items) == 1:
                out.extend([f"{ticker}: {_text(items[0].get('heading'))}",
                        _text(items[0].get("body")), ""])
            else:
                out.append(ticker)
                for s in items:
                    out.extend([f"  {_text(s.get('heading'))}", _text(s.get("body")), ""])

    all_rows = briefing.get("rows") or []
    all_cards = briefing.get("summaries") or []
    grouping = _grouping(briefing, pack)
    if grouping:
        order, code_of, label_of = grouping
        default = order[0]
        placed = 0
        for code in order:
            g_rows = [r for r in all_rows if code_of.get(r.get("ticker"), default) == code]
            g_cards = [c for c in all_cards if code_of.get(c.get("ticker"), default) == code]
            if not g_rows and not g_cards:
                continue
            placed += len(g_rows)
            out += [f"--- {label_of[code].upper()} ({len(g_rows)}) ---", ""]
            rows_and_cards(g_rows, g_cards)
        if placed != len(all_rows):
            rows_and_cards(all_rows, all_cards)        # never shorter than the classic
    else:
        rows_and_cards(all_rows, all_cards)
    other_txt = briefing.get("other") or briefing.get("quarterlies") or []
    if other_txt:
        out.append("OTHER")
        for ticker, items in _group_by_ticker(_other_rows(other_txt, pack)):
            company, when = _text(items[0].get("company")), _when(items, lodged_at, late)
            head = _cap_label(ticker, items)
            if len(items) == 1:
                out.append(f"  {head}  {company}  "
                           f"{_text(items[0].get('announcement'))}  ({when})")
            else:
                out.append(f"  {head}  {company}  ({when})")
                out += [f"       {_text(i.get('announcement'))}" for i in items]
        out.append("")
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

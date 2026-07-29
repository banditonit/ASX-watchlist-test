"""Renders the briefing as an HTML email in Discovery house style.

Written for email clients, not browsers. That means tables for layout, inline
styles only, no flexbox, no grid, no external stylesheet and no background
images, because Outlook renders with Word's engine and silently discards most
modern CSS. The DCP mark is attached and referenced by content ID rather than
base64, which Outlook blocks.
"""

from html import escape

NAVY = "#002B56"
GREY = "#E6E7E8"
GOLD = "#BA9C67"
ICE = "#C9E4FF"
MUTED = "#585858"
WHITE = "#FFFFFF"

FONT = "'Open Sans','Segoe UI',Helvetica,Arial,sans-serif"
WIDTH = 680

DISCLAIMER = (
    "This document was prepared by Discovery Capital Partners Pty Ltd exclusively "
    "for the benefit of the recipient. It does not purport to contain all "
    "information that may be required to evaluate the subject matter, and each "
    "recipient should conduct its own independent analysis. The information "
    "reflects prevailing conditions and the views of Discovery as of this date, "
    "which are subject to change. All dollar figures are in AUD unless otherwise "
    "indicated."
)

CONTACTS = [
    ("Adam Miethke", "Managing Director", "am@discoverycapital.com.au"),
    ("Kale Pervan", "Director", "kp@discoverycapital.com.au"),
    ("Darcy Frazer", "Associate", "df@discoverycapital.com.au"),
]


def _p(text, size=15, colour=NAVY, weight="normal", top=0, bottom=14):
    """Render text as one or more paragraphs, preserving blank-line breaks."""
    blocks = [b.strip() for b in (text or "").split("\n\n") if b.strip()]
    if not blocks:
        return ""
    return "".join(
        f'<p style="margin:{top if i == 0 else 0}px 0 {bottom}px 0;font-family:{FONT};'
        f'font-size:{size}px;line-height:1.55;color:{colour};'
        f'font-weight:{weight};">{escape(b)}</p>'
        for i, b in enumerate(blocks)
    )


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
        for h in ("Ticker", "Company", "Announcement", "Type", "Date")
    )
    body = []
    for i, r in enumerate(rows):
        bg = GREY if i % 2 == 0 else WHITE
        cells = [
            r.get("ticker", ""), r.get("company", ""), r.get("announcement", ""),
            r.get("type", ""), r.get("date", ""),
        ]
        widths = ["10%", "23%", "40%", "13%", "14%"]
        tds = "".join(
            f'<td width="{w}" style="font-family:{FONT};font-size:12px;'
            f'color:{NAVY};padding:9px 10px;background-color:{bg};'
            f'vertical-align:top;">{escape(str(c))}</td>'
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


def _card(heading, paragraphs=None, bullets=None):
    inner = [
        f'<div style="font-family:{FONT};font-size:15px;font-weight:bold;'
        f'color:{NAVY};margin-bottom:8px;">{escape(heading)}</div>'
    ]
    for para in paragraphs or []:
        inner.append(_p(para, size=14, bottom=10))
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


def _subheading(text):
    """A quieter divider than the navy band, for secondary sections."""
    return f"""
    <tr><td style="padding:6px 0 10px 0;border-top:1px solid {GOLD};">
      <div style="font-family:{FONT};font-size:13px;font-weight:bold;
                  letter-spacing:1.2px;text-transform:uppercase;
                  color:{MUTED};padding-top:12px;">{escape(text)}</div>
    </td></tr>
    """


def _quarterly(q):
    """One quarterly as a single dense line, desk-note style.

        NST ($29B)  Northern Star sold 433koz gold at AISC $2.7k/oz ...
    """
    cap = q.get("cap_label") or ""
    head = f"{q.get('ticker','')} ({cap})" if cap else q.get("ticker", "")
    return f"""
    <tr><td style="font-family:{FONT};font-size:13px;line-height:1.5;
                   color:{NAVY};padding:0 0 11px 0;">
      <span style="font-weight:bold;">{escape(head)}</span>
      <span style="color:{MUTED};">&nbsp;&nbsp;</span>{escape(q.get('summary',''))}
    </td></tr>
    """


def _contacts():
    cells = "".join(
        f'<td width="33%" valign="top" style="padding-right:12px;">'
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


def render(briefing, pack, pdf_name=None):
    """Return (html, plain_text) for one briefing."""
    date = pack.get("date_awst", "")
    rows = briefing.get("rows") or []
    body = []

    body.append(_band("Confirmed Announcements",
                      f"24 hours to {pack.get('window_end_awst','')[11:16]} AWST, {date}"))
    body.append(f'<tr><td>{_p(briefing.get("lead",""))}</td></tr>')
    body.append(_table(rows))

    for s in briefing.get("summaries") or []:
        body.append(_card(f"{s.get('ticker','')}: {s.get('heading','')}",
                          paragraphs=[s.get("body", "")]))

    quarterlies = briefing.get("quarterlies") or []
    if quarterlies:
        body.append(_subheading("Quarterlies"))
        for q in quarterlies:
            body.append(_quarterly(q))

    if briefing.get("watch_items"):
        body.append(_card("Watch items", bullets=briefing["watch_items"]))
    if briefing.get("unconfirmed"):
        body.append(_card("Unconfirmed and unread", bullets=briefing["unconfirmed"]))

    body.append(_band("Day in Brief"))
    body.append(f'<tr><td>{_p(briefing.get("day_in_brief",""))}</td></tr>')

    if pdf_name:
        body.append(f'<tr><td>{_p("The full briefing is attached as " + pdf_name + ".", size=13, colour=MUTED)}</td></tr>')

    body.append(_contacts())
    body.append(
        f'<tr><td style="padding-top:14px;">'
        f'<div style="font-family:{FONT};font-size:10px;line-height:1.5;color:{MUTED};">'
        f"{escape(DISCLAIMER)}</div></td></tr>"
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ASX Watchlist Catch Up, {escape(date)}</title></head>
<body style="margin:0;padding:0;background-color:#F4F5F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:#F4F5F6;">
 <tr><td align="center" style="padding:22px 10px;">
  <table role="presentation" width="{WIDTH}" cellpadding="0" cellspacing="0" border="0"
         style="width:100%;max-width:{WIDTH}px;background-color:{WHITE};">

   <tr><td style="background-color:{NAVY};padding:22px 18px 20px 18px;">
     <img src="cid:dcpmark" width="150" alt="Discovery Capital Partners"
          style="display:block;border:0;margin-bottom:14px;">
     <div style="font-family:{FONT};font-size:25px;font-weight:bold;color:{WHITE};">
       ASX Watchlist Catch Up</div>
     <div style="font-family:{FONT};font-size:14px;color:{WHITE};padding-top:5px;">
       Daily Announcements Briefing, {escape(date)}</div>
     <div style="font-family:{FONT};font-size:10px;font-weight:bold;color:{ICE};
                 letter-spacing:1px;padding-top:10px;">INTERNAL USE ONLY</div>
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
        "INTERNAL USE ONLY",
        "",
        "CONFIRMED ANNOUNCEMENTS",
        briefing.get("lead", ""),
        "",
    ]
    for r in briefing.get("rows") or []:
        out.append(f"  {r.get('ticker','')}  {r.get('company','')}  "
                   f"{r.get('announcement','')}  ({r.get('type','')}, {r.get('date','')})")
    out.append("")
    for s in briefing.get("summaries") or []:
        out += [f"{s.get('ticker','')}: {s.get('heading','')}", s.get("body", ""), ""]
    if briefing.get("quarterlies"):
        out.append("QUARTERLIES")
        for q in briefing["quarterlies"]:
            out += [f"  {q.get('ticker','')}  {q.get('company','')}  ({q.get('headline','')})",
                    f"    {q.get('summary','')}", ""]
    if briefing.get("watch_items"):
        out += ["WATCH ITEMS"] + [f"  - {w}" for w in briefing["watch_items"]] + [""]
    if briefing.get("unconfirmed"):
        out += ["UNCONFIRMED AND UNREAD"] + [f"  - {u}" for u in briefing["unconfirmed"]] + [""]
    out += ["DAY IN BRIEF", briefing.get("day_in_brief", ""), "", DISCLAIMER]
    return "\n".join(out)

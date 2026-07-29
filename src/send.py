"""Sends the briefing by email.

Recipients come from config/recipients.txt so that adding or removing someone
never requires touching code. Everyone is placed in Bcc, with the sending
address in To, so the mailing list is not exposed in the header.

The MIME structure here is built by hand rather than with EmailMessage's
add_related, and the reason is Outlook. Python's helper nests the image inside
the alternative part:

    multipart/alternative
      text/plain
      multipart/related
        text/html
        image/png

That is valid, and most clients render it, but Outlook frequently will not and
shows "the linked image cannot be displayed" instead. Outlook is reliable when
related is the OUTER container:

    multipart/related
      multipart/alternative
        text/plain
        text/html
      image/png

which is what this builds. The Content-ID is also given the sender's own domain
rather than the localhost default, because some mail servers rewrite or reject
a bare localhost identifier, after which the reference no longer resolves.
"""

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr

from config import env


def _cid_domain(sender):
    """Use the sender's own domain for the Content-ID, never localhost."""
    addr = parseaddr(sender)[1]
    return addr.split("@")[-1] if "@" in addr else "briefing.local"


def build(html, plain, subject, recipients, sender, attachments=None, logo=None):
    """Assemble the message. Kept separate from sending so it can be inspected."""
    domain = _cid_domain(sender)
    root = MIMEMultipart("related")
    root["Subject"] = subject
    root["From"] = f"Discovery Watchlist <{parseaddr(sender)[1]}>"
    root["To"] = f"Discovery Watchlist <{parseaddr(sender)[1]}>"
    root["Bcc"] = ", ".join(recipients)
    root["Date"] = formatdate(localtime=True)
    root["Message-ID"] = make_msgid(domain=domain)
    root["X-Auto-Response-Suppress"] = "All"

    cid = None
    if logo and os.path.exists(logo):
        cid = make_msgid(domain=domain)[1:-1]
        html = html.replace("cid:dcpmark", f"cid:{cid}")
    else:
        # No mark available. Remove the whole tag rather than leaving an <img>
        # with an empty src, which renders as a broken-image icon.
        start = html.find('<img src="cid:dcpmark"')
        if start != -1:
            end = html.find(">", start)
            html = html[:start] + html[end + 1:]

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    root.attach(alt)

    if cid:
        with open(logo, "rb") as fh:
            img = MIMEImage(fh.read(), "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline",
                       filename=os.path.basename(logo))
        root.attach(img)

    for path in attachments or []:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            att = MIMEApplication(fh.read(), _subtype="pdf")
        att.add_header("Content-Disposition", "attachment",
                       filename=os.path.basename(path))
        root.attach(att)

    return root


def send(html, plain, subject, recipients, attachments=None, logo=None,
         dry_run=False):
    host = env("SMTP_HOST", "smtp.gmail.com")
    port = int(env("SMTP_PORT", "587"))
    user = env("SMTP_USER", required=not dry_run)
    password = env("SMTP_PASSWORD", required=not dry_run)
    sender = env("SMTP_FROM", user or "briefing@localhost")

    msg = build(html, plain, subject, recipients, sender,
                attachments=attachments, logo=logo)
    if dry_run:
        return msg

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    return msg

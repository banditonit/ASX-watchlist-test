"""Sends the briefing by email.

Recipients come from config/recipients.txt so that adding or removing someone
never requires touching code. Everyone is placed in Bcc, with the sending
address in To, so the mailing list is not exposed in the header of a document
marked internal.
"""

import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from config import env


def send(html, plain, subject, recipients, attachments=None, logo=None, dry_run=False):
    host = env("SMTP_HOST", "smtp.gmail.com")
    port = int(env("SMTP_PORT", "587"))
    user = env("SMTP_USER", required=not dry_run)
    password = env("SMTP_PASSWORD", required=not dry_run)
    sender = env("SMTP_FROM", user or "briefing@localhost")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Discovery Watchlist <{parseaddr(sender)[1]}>"
    msg["To"] = f"Discovery Watchlist <{parseaddr(sender)[1]}>"
    msg["Bcc"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="discoverycapital.com.au")
    msg["X-Auto-Response-Suppress"] = "All"

    msg.set_content(plain)

    logo_cid = None
    if logo and os.path.exists(logo):
        logo_cid = make_msgid()[1:-1]
        html = html.replace("cid:dcpmark", f"cid:{logo_cid}")
    else:
        # No mark available: drop the img tag rather than show a broken image.
        html = html.replace('<img src="cid:dcpmark"', '<img src="" style="display:none"')

    msg.add_alternative(html, subtype="html")

    if logo_cid:
        with open(logo, "rb") as fh:
            msg.get_payload()[1].add_related(
                fh.read(), maintype="image", subtype="png", cid=f"<{logo_cid}>"
            )

    for path in attachments or []:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            msg.add_attachment(
                fh.read(), maintype="application", subtype="pdf",
                filename=os.path.basename(path),
            )

    if dry_run:
        return msg

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    return msg

"""Decides which announcements are worth a full summary, from their TEXT.

Headline-and-flag filtering is what lets things slip. "Quarterly Activities
Report" is a dull headline that can carry a maiden resource, a guidance
downgrade or the first mention of a strategic review, and the price-sensitive
flag is set by the company rather than to an objective standard. Since the
full text has already been fetched and costs nothing to read locally, the
decision is made on the body of the document.

Everything gets a score. High scorers are summarised in full. Low scorers are
still passed to the model as a short digest, so that a wrong call here degrades
into a thinner summary rather than an invisible announcement.

Conference and investor presentations are the exception to that generosity.
They are marketing decks. They restate economics that were released separately
weeks earlier, and because they restate them in full they score highly on
content: on 4 August 2026 seven of the thirteen confirmed announcements were
Diggers and Dealers decks, every one of them summarised as containing no new
figures. They are now dropped before the model ever sees them, which removes
the noise and the API cost together. A deck whose headline says it carries
news is kept.
"""

import re

# Patterns that state a fact worth reading, weighted by how much it moves a name.
SIGNALS = [
    # corporate events
    (9, "trading halt", re.compile(r"\btrading halt\b", re.I)),
    (9, "suspension", re.compile(r"\bvoluntary suspension\b|\bsuspension from (?:official )?quotation\b", re.I)),
    (10, "scheme of arrangement", re.compile(r"\bscheme of arrangement\b|\bscheme implementation\b", re.I)),
    (10, "takeover / merger", re.compile(r"\btakeover\b|\bmerger\b|\boff-market bid\b|\bbinding (?:offer|proposal)\b", re.I)),
    (8, "acquisition / divestment", re.compile(r"\bacquisi\w+\b|\bdivest\w+\b|\bfarm-?in\b|\bdemerger\b", re.I)),
    (8, "capital raising", re.compile(r"\bcapital raising\b|\bplacement\b|\bentitlement offer\b|\bshare purchase plan\b|\brights issue\b", re.I)),
    (7, "substantial holder", re.compile(r"\bsubstantial (?:holder|shareholder)\b|\bbecoming a substantial\b|\bceasing to be a substantial\b", re.I)),

    # resource and study milestones
    (9, "resource / reserve", re.compile(r"\bmineral resource estimate\b|\bore reserve\b|\bJORC\b|\bmaiden resource\b|\bresource (?:upgrade|update)\b", re.I)),
    (9, "study", re.compile(r"\b(?:scoping|pre-?feasibility|definitive feasibility|bankable)\s+study\b|\bPFS\b|\bDFS\b", re.I)),
    (8, "economics", re.compile(r"\bNPV\b|\bIRR\b|\bAISC\b|\ball-?in sustaining\b|\bpayback period\b|\bmine life\b", re.I)),

    # exploration results, the reason this list exists
    (8, "drill intercept", re.compile(r"\d+(?:\.\d+)?\s*m\s*@\s*\d+(?:\.\d+)?\s*(?:g/t|%|ppm|ppb)", re.I)),
    (6, "assay language", re.compile(r"\bassay\w*\b|\bintercept\w*\b|\bintersect\w+\b|\bdrill(?:ing|hole|ed)?\b", re.I)),
    (7, "discovery", re.compile(r"\bdiscovery\b|\bnew zone\b|\bhigh-?grade\b|\bmineralisation\b", re.I)),

    # operating and guidance
    (8, "guidance", re.compile(r"\bguidance\b|\bupgrade[sd]?\b|\bdowngrade[sd]?\b|\brevised? (?:outlook|forecast)\b", re.I)),
    (7, "production", re.compile(r"\bfirst (?:gold|pour|ore|production|shipment)\b|\bcommercial production\b|\brecord (?:production|quarter)\b", re.I)),
    (6, "permitting", re.compile(r"\bmining licence\b|\bmining lease\b|\benvironmental approval\b|\bpermit\w* granted\b|\bconsent\b", re.I)),
    (7, "offtake / funding", re.compile(r"\boff-?take\b|\bdebt facility\b|\bstreaming agreement\b|\bstrategic investment\b", re.I)),
]

# Recurring calendar filings. Not "material" in the sense a halt is, but never
# noise either: a quarterly carries production, AISC, cash and guidance
# commentary, and a half-year or annual report carries the audited numbers.
# They arrive on a schedule, every company files them, and they mostly restate.
# So they get their own section rather than competing with the day's news.
QUARTERLY = re.compile(
    r"quarterly (?:activities|cash\s?flow|cashflow|report)|"
    r"activities report for the quarter|"
    r"appendix\s*5b|"
    r"(?:december|march|june|september)\s+quarter(?:ly)?\b|"
    r"\bq[1-4]\s*(?:fy)?\s*\d{2,4}\s+(?:report|activities|update)",
    re.I,
)

# The rest of the reporting calendar: half years, full years, and the statutory
# wrappers around them.
PERIODIC = re.compile(
    r"appendix\s*4[cde]\b|"
    r"\b(?:annual|half[- ]?year(?:ly)?|interim|full[- ]?year|preliminary final)\b"
    r"[^.]{0,40}?\b(?:report|accounts|statements?|financials?)\b|"
    r"\bcorporate governance statement\b|"
    r"\bannual report\b|\bpreliminary final report\b",
    re.I,
)

# A results release is the news; the statutory report lodged beside it is the
# filing. On 19 August 2026 Evolution lodged both and each got its own full
# summary of the same result. The words below mark the release. The words in
# STATUTORY mark the filing and win, because "Appendix 4E and FY26 Financial
# Report" is the wrapper even though it covers the same numbers.
PERIODIC_NEWS = re.compile(
    r"\bresults?\b|\bdividend\b|\bguidance\b|\bprofit\b|\bearnings\b|"
    r"\brecord\b|\boutlook\b",
    re.I,
)
STATUTORY = re.compile(
    r"appendix\s*4[cde]\b|\baccounts\b|\bfinancial report\b|"
    r"\bannual report\b|\bcorporate governance\b|\bfinancial statements?\b",
    re.I,
)

# A disclosure that happens to be annual is still a disclosure. Ramelius and
# Matador both lodge "Annual Mineral Resources and Ore Reserves Statement", and
# an earlier draft of PERIODIC swallowed it on the word "Annual ... Statement",
# which is the exact failure this whole round exists to fix. Anything naming a
# resource, a reserve or exploration results stays in the main section however
# regularly it arrives.
NEVER_PERIODIC = re.compile(
    r"\b(?:mineral )?resources?\b|\b(?:ore )?reserves?\b|\bmaiden\b|"
    r"\bexploration (?:results|update|target)\b|\bdrill\w*\b|\bassay\w*\b|"
    r"\bintercept\w*\b|\bfeasibility\b|\bscoping\b|\bJORC\b",
    re.I,
)

# Substantial holder notices: Forms 603, 604 and 605. A prescribed form whose
# contents are set by regulation, so unlike an ordinary routine filing there is
# no version of it that carries news its headline does not. They are capped
# unconditionally, with no content escape, because the escape is exactly what
# let eight of them into the main section on 25 August 2026: "Becoming a
# substantial holder" does match the routine rule, but the Form 603 body scores
# on acquisition and placement language and reached 15, above the threshold at
# which that rule stops applying.
#
# Nothing is lost. The headline carries the holder and the percentage, and it
# is printed in full under Also Lodged, so a stake worth noticing is still one
# click away.
SUBSTANTIAL = re.compile(
    r"\b(?:becoming|ceasing to be) a substantial (?:holder|shareholder)\b|"
    r"\bchange (?:in|of|to) substantial (?:holding|shareholding|interest)\b|"
    r"\bsubstantial (?:holder|shareholder|holding) notice\b|"
    r"\bform 60[3-5]\b|"
    r"\bnotice of (?:initial )?substantial (?:holder|holding)\b",
    re.I,
)

# An ASX-requested addendum to an announcement already made. It restates: the
# standard form attaches the original release in full and adds the JORC Table 1
# or the competent person detail the exchange asked for, so its body scores
# exactly like the original did. AuKing's Tundulu supplementary on 2 September
# 2026 scored 22 on drill language that was all in the 1 September release.
# Like a quarterly, it is demoted on structure, with no content escape: the
# news was reported when it was news, and the addendum is listed under Also
# Lodged with its headline for anyone who wants the tables.
SUPPLEMENTARY = re.compile(
    r"^(?:cancel(?:led|lation)?\s*[-:]\s*)?"
    r"supplementary (?:announcement|information|disclosure|statement|release)\b|"
    r"^addendum to\b",
    re.I,
)

# Diary notes about a result, not the result. These carry nothing at all.
ADMIN_NOTICE = re.compile(
    r"conference call|investor (?:call|webinar|briefing) (?:details|invit)|"
    r"\bwebinar\b|notification of (?:results|reporting date)|"
    r"\bcall (?:details|notification)\b|date of (?:results|release)",
    re.I,
)

# Marketing decks, judged on the headline alone. Deliberately broad: a slide
# pack lodged for a conference, a site visit, a roadshow or a webcast is the
# same document with a different cover. Note this also catches the "Quarterly
# Results Presentation" that companies lodge alongside the quarterly itself,
# which is the right outcome, because the Appendix 5B and activities report
# carry the same numbers and are collected anyway.
PRESENTATION = re.compile(
    r"\bpresentation\b|"
    r"\bslide (?:pack|deck)\b|"
    r"\bwebcast\b|\bwebinar\b|\broadshow\b|"
    r"\binvestor (?:day|briefing|update|overview)\b|"
    r"\bsite (?:visit|tour)\b|"
    r"\b(?:conference|forum) (?:update|address|materials)\b|"
    r"\b(?:chairman|chair|ceo|managing director)'?s? address\b|"
    r"\bcorporate (?:overview|profile)\b",
    re.I,
)

# A deck is also kept when its TITLE BLOCK names a primary disclosure. On
# 25 August 2026 Ramelius lodged its annual resource and reserve update as a
# deck headlined "Value and Growth from the Drill Bit Investor Presentation".
# Nothing in that headline says news, so it was suppressed, and the update
# never reached the briefing. The document's own cover said what it was:
# "Value And Growth From The Drill Bit / Resource & Reserve Update / FY26
# Financial Results".
#
# Only the title block is read, not the body. The body is useless for this,
# because a restatement deck quotes NPV, IRR and every intercept the company
# has ever reported and so reads as material however it is scored: on the
# archive to date the highest-scoring deck of all, at 76, was a Diggers and
# Dealers pitch, one point above the Ramelius update at 75. The cover page is
# where a deck says what it is, and it is read up to the first disclaimer.
PRESENTATION_KEEP = re.compile(
    r"\bmaiden\b|\bore reserve\b|\bresource (?:upgrade|estimate|update)\b|"
    r"\b(?:scoping|feasibility|pre-?feasibility|definitive feasibility)\b|\bPFS\b|\bDFS\b|"
    r"\bacquisition\b|\bacquire\w*\b|\bmerger\b|\btakeover\b|\bscheme\b|\bbid\b|"
    r"\bplacement\b|\bentitlement\b|\bcapital raising\b|\bequity raising\b|"
    r"\bshare purchase plan\b|\bSPP\b|\bIPO\b|\bdemerger\b|"
    r"\bguidance\b|\bdiscovery\b|\bfirst (?:gold|ore|pour|production|shipment)\b|"
    r"\bfinal investment decision\b|\bFID\b|\boff-?take\b|\bstrategic review\b|"
    r"\btrading halt\b|"
    r"\d+(?:\.\d+)?\s*m\s*@\s*\d+(?:\.\d+)?\s*(?:g/t|%|ppm|ppb)|"   # intercept in the headline
    r"\d+(?:\.\d+)?\s*m\s+(?:at|of)\s+\d+(?:\.\d+)?\s*(?:g/t|%)",
    re.I,
)

# Routine filings. Present so they can be listed, never summarised at length.
# Extended 2 September 2026 after the afternoon update promoted two filings
# that carried nothing: NexGen's monthly "Statement of CDIs on issue" and
# AuKing's "Supplementary Announcement", an ASX-requested compliance addendum
# with no assays in it. Same escape as every routine cap: a body that scores
# 14 or more on its own content stays in the main section, so a supplementary
# that actually restates results with new numbers is not demoted on its title.
ROUTINE = re.compile(
    r"^(?:cancel(?:led|lation)?\s*[-:]\s*)?"          # "Cancel - Notification of ..."
    r"(?:appendix\s*(?:2a|3b|3g|3h|3x|3y|3z|4g)|"
    r"change (?:in|of) director'?s? interest|"
    r"(?:initial|final) director'?s? interest notice|"
    r"notification (?:regarding|of) unquoted|"
    r"notification of cessation of securities|"
    r"statement of cdis? on issue|"
    r"expiry of (?:unlisted |listed )?(?:options|performance rights|warrants)|"
    r"application for (?:quotation|admission)|"
    r"cleansing (?:notice|statement)|"
    r"proposed issue of securities|"
    r"notice of (?:annual general |general )?meeting|"
    r"results of meeting|"
    r"becoming a substantial holder|"
    r"daily share buy-?back)",
    re.I,
)

FULL_SUMMARY_AT = 8      # score at or above this gets the full text sent
MIN_TEXT = 400           # below this many characters, treat as a stub


# Where a slide deck stops introducing itself and starts reciting boilerplate.
TITLE_END = re.compile(
    r"important notice|disclaimer|forward[- ]?looking|competent person|"
    r"qualifications|non-?ifrs",
    re.I,
)
TITLE_CHARS = 400

# Phrases that name a disclosure in their own right rather than describing a
# company. Deliberately narrow: "Resource & Reserve Update" qualifies, "2.1Moz
# and Growing: Building a Major ASX-Listed Gold Company" does not.
PRIMARY_DISCLOSURE = re.compile(
    r"\b(?:mineral )?resources?\s*(?:&|and|/|,)\s*(?:ore )?reserves?\b|"
    r"\b(?:ore )?reserve (?:update|statement|estimate)\b|"
    r"\b(?:mineral )?resource (?:update|statement|estimate|upgrade)\b|"
    r"\bmaiden (?:resource|reserve)\b|"
    r"\b(?:definitive |pre-?)?feasibility study\b|\bscoping study\b|"
    r"\bPFS\b|\bDFS\b|\bfinal investment decision\b",
    re.I,
)


def title_block(text):
    """The cover of a deck: everything before the first disclaimer."""
    head = (text or "")[:TITLE_CHARS]
    end = TITLE_END.search(head)
    return head[:end.start()] if end else head


def is_presentation(headline, text=None):
    """True when this is a marketing deck and not a disclosure in deck form."""
    headline = headline or ""
    if not PRESENTATION.search(headline):
        return False
    if PRESENTATION_KEEP.search(headline):
        return False
    return not PRIMARY_DISCLOSURE.search(title_block(text))


def score(record):
    """Attach a score, the reasons behind it, and a tier to one record."""
    text = record.get("text") or ""
    headline = record.get("headline") or ""
    haystack = f"{headline}\n{text}"

    hits, total = [], 0
    for weight, label, pattern in SIGNALS:
        if pattern.search(haystack):
            hits.append(label)
            total += weight

    if record.get("price_sensitive"):
        total += 6
        hits.append("flagged price sensitive")

    # Checked before the quarterly test, so a "Quarterly Results Presentation"
    # is treated as the deck it is rather than duplicating the quarterly.
    record["is_presentation"] = is_presentation(headline, text)
    if record["is_presentation"]:
        hits.append("presentation, suppressed")

    # Quarterlies are identified but not scored up. A quarterly that happens to
    # contain a guidance change or a maiden reserve will clear the materiality
    # threshold on its own content and be promoted to the main section; one that
    # is genuinely a routine quarterly lands in its own section instead.
    record["is_quarterly"] = bool(QUARTERLY.search(headline))
    if record["is_quarterly"]:
        hits.append("quarterly")

    # A half year or full year filing joins them unless the headline is the
    # results release itself rather than the statutory report beside it.
    record["is_periodic"] = record["is_quarterly"] or bool(
        PERIODIC.search(headline)
        and not NEVER_PERIODIC.search(headline)
        and (STATUTORY.search(headline) or not PERIODIC_NEWS.search(headline))
    )
    if record["is_periodic"] and not record["is_quarterly"]:
        hits.append("periodic filing")

    # A notice about when results will be released is not an announcement.
    # Capped the same way a routine filing is, and with the same escape: if the
    # body scores heavily the cap does not apply, because a headline is only
    # ever evidence about a document, never the last word on it. Without this
    # the diary-note rule was the one demotion in this module that no amount of
    # content could overturn.
    if ADMIN_NOTICE.search(headline) and total < 14:
        total = min(total, 4)
        hits.append("diary note")

    if SUBSTANTIAL.search(headline):
        total = min(total, 4)
        hits.append("substantial holder notice")

    if SUPPLEMENTARY.match(headline.strip()):
        total = min(total, 4)
        hits.append("supplementary to an earlier announcement")

    # A routine filing headline caps the score unless the body says otherwise.
    routine = bool(ROUTINE.match(headline.strip()))
    if routine and total < 14:
        total = min(total, 4)
        hits.append("routine filing")

    if not text and record.get("text_status", "").startswith(("unreadable", "download", "extract")):
        # Could not be read. Escalate so a human is told, rather than assuming
        # a document nobody could open was unimportant.
        total = max(total, FULL_SUMMARY_AT)
        hits.append("could not be read, needs manual check")

    record["score"] = total
    record["signals"] = hits
    record["tier"] = _tier(record, total, text)
    return record


def _tier(record, total, text):
    """Presentations are dropped and quarterlies are never promoted.

    A quarterly restates. When a company writes up its DFS, its resource
    upgrade or its FID in a quarterly, that material was almost always released
    separately weeks earlier, so surfacing it as a confirmed announcement
    reports old news as new. Quarterlies belong in the Quarterlies section and
    nowhere else. This is a structural rule, not a judgement the model gets to
    make.

    A presentation restates harder still, and unlike a quarterly it adds no
    operating numbers of its own, so it earns no section at all.
    """
    if record.get("is_presentation"):
        return "presentation"
    if record.get("is_periodic"):
        return "periodic"
    if record.get("text_status", "") != "ok" and not text:
        return "unreadable"
    if total >= FULL_SUMMARY_AT:
        return "full"
    return "digest"


def rank(records):
    """Score everything and split into tiers, most material first.

    Presentations come back in their own bucket. run.py does not read that
    bucket, so they are never summarised and never rendered, but they are
    printed here so the Actions log shows what was dropped and why. Silent
    filtering is how a real announcement disappears without anyone noticing.
    """
    for record in records:
        score(record)
    ordered = sorted(records, key=lambda r: (-r["score"], r["ticker"]))
    suppressed = [r for r in ordered if r["tier"] == "presentation"]
    if suppressed:
        print(f"suppressed {len(suppressed)} presentation(s):")
        for r in suppressed:
            print(f"    {r['ticker']}  {r.get('headline','')[:60]}")
    return {
        "full": [r for r in ordered if r["tier"] in ("full", "unreadable")],
        "periodic": [r for r in ordered if r["tier"] == "periodic"],
        "digest": [r for r in ordered if r["tier"] == "digest"],
        "presentation": suppressed,
        "all": ordered,
    }

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

# Quarterly reporting. Not "material" in the sense a halt is, but never noise:
# a quarterly carries production, AISC, cash and guidance commentary, and it is
# the one routine document that is always worth reading. These get their own
# section rather than being listed with the Appendix filings.
QUARTERLY = re.compile(
    r"quarterly (?:activities|cash\s?flow|cashflow|report)|"
    r"activities report for the quarter|"
    r"appendix\s*5b|"
    r"(?:december|march|june|september)\s+quarter(?:ly)?\b|"
    r"\bq[1-4]\s*(?:fy)?\s*\d{2,4}\s+(?:report|activities|update)",
    re.I,
)

# Routine filings. Present so they can be listed, never summarised at length.
ROUTINE = re.compile(
    r"^(?:appendix\s*(?:2a|3b|3g|3h|3x|3y|3z|4g)|"
    r"change (?:in|of) director'?s? interest|"
    r"notification (?:regarding|of) unquoted|"
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

    # Quarterlies are identified but not scored up. A quarterly that happens to
    # contain a guidance change or a maiden reserve will clear the materiality
    # threshold on its own content and be promoted to the main section; one that
    # is genuinely a routine quarterly lands in its own section instead.
    record["is_quarterly"] = bool(QUARTERLY.search(headline))
    if record["is_quarterly"]:
        hits.append("quarterly")

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
    """Quarterlies are never promoted, whatever their contents.

    A quarterly restates. When a company writes up its DFS, its resource
    upgrade or its FID in a quarterly, that material was almost always released
    separately weeks earlier, so surfacing it as a confirmed announcement
    reports old news as new. Quarterlies belong in the Quarterlies section and
    nowhere else. This is a structural rule, not a judgement the model gets to
    make.
    """
    if record.get("is_quarterly"):
        return "quarterly"
    if record.get("text_status", "") != "ok" and not text:
        return "unreadable"
    if total >= FULL_SUMMARY_AT:
        return "full"
    return "digest"


def rank(records):
    """Score everything and split into tiers, most material first."""
    for record in records:
        score(record)
    ordered = sorted(records, key=lambda r: (-r["score"], r["ticker"]))
    return {
        "full": [r for r in ordered if r["tier"] in ("full", "unreadable")],
        "quarterly": [r for r in ordered if r["tier"] == "quarterly"],
        "digest": [r for r in ordered if r["tier"] == "digest"],
        "all": ordered,
    }

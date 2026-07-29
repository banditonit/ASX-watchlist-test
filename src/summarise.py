"""Turns the evidence pack into briefing copy, via the Claude API.

The model is given the actual announcement text, not headlines, and is told
to quote real figures. A forced tool schema is used so the response is always
structured data rather than prose that has to be parsed.
"""

import json
import os

import anthropic

# Sonnet is the right tier here: the job is pulling figures out of a document
# accurately, which Haiku is measurably weaker at, and which does not need Opus.
# Override with the CLAUDE_MODEL secret if that judgement changes.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
MAX_FULL_CHARS = 45_000       # per announcement, generous for a technical release
MAX_QUARTERLY_CHARS = 22_000  # the numbers that matter sit early in a quarterly
MAX_DIGEST_CHARS = 2_500

SYSTEM = """You write the Discovery Capital Partners daily ASX watchlist briefing.

House writing rules, which are absolute:
- No em dashes anywhere. Use a comma, a colon, parentheses, or a new sentence.
- No en dashes as punctuation. Write "A$1.20 to A$1.40", not a dash range.
- Australian English: mineralisation, analyse, metres, tonnes, licence (noun).
- Third person. Never "we". The firm is "Discovery".
- Currency unspaced and prefixed: A$5.0M in tables and cards, A$71.5 million in
  flowing prose. Pick one and stay with it.
- No space between a number and its unit: 3km, 5m, 0.9% Cu, 4.73g/t Au.
- Drill intercepts in house form: 5m @ 2.4% Cu from 17m (WCRC006), with the
  hole ID in parentheses.
- Dates as "23 July 2026". Never numeric formats.
- Lead with the conclusion. The first sentence of any section is the finding.

Rules on substance, which matter more than style:
- Every figure you write must appear in the source text you were given. If a
  number is not in the text, it does not go in the briefing.
- A trading halt is never reported as a bare "trading halt". Halt notices state
  their purpose. Report that purpose, for example "halted pending an
  announcement regarding a capital raising", and give the expected resumption
  date if the notice states one.
- For exploration results, quote the actual intercepts, the best ones first.
- For a transaction, give consideration, structure and any premium stated.
- Copy each item's document key back exactly as it was given to you. It is used
  to link the briefing to the source, and a key that does not match simply
  becomes no link.
- Report only what you can confirm from the source text in front of you. If an
  item cannot be confirmed, leave it out rather than hedging about it in the
  briefing.

Summaries are short. Two to four sentences, 40 to 70 words, and fewer if the
item is simple. Lead with the fact that moves the name, then the figures that
size it, then the next catalyst if there is one. Stop there.

Cut, every time: adviser, broker, lead manager and underwriter credits; the
settlement date of each tranche; references to Listing Rule 7.1 and 7.1A
capacity; FIRB and other conditions precedent unless the deal turns on them;
every drill hole beyond the best two or three; metallurgical recoveries unless
recovery is the story.

A worked example. This is too long:

  Saturn Metals completed a two-tranche placement raising $100 million (before
  costs) at $0.40 per share, an 11.1% discount to last close of $0.450. Tranche
  One (63.8 million shares, approximately $25 million) proceeds under existing
  7.1 and 7.1A capacity settles 6 August 2026; Tranche Two (186.2 million
  shares, approximately $75 million) requires shareholder approval at a general
  meeting on 17 September 2026. New cornerstone investor Golden Crane Holdings
  has committed approximately $48.6 million for 121.5 million shares, expected
  to hold approximately 15% of the company post-raise... Petra Capital acted as
  sole lead manager, bookrunner and underwriter.

This says the same thing:

  Saturn Metals raised $100m at $0.40, an 11.1% discount, to fund front end
  engineering and construction readiness at Apollo Hill. Golden Crane Holdings
  comes in as a cornerstone at roughly $48.6m for about 15% of the company. The
  $75m second tranche needs shareholder approval on 17 September. Follows a 26%
  resource upgrade to 2.83Moz.

And a short item stays short:

  Pacgold is halted pending an announcement regarding a capital raising, until
  the earlier of normal trading on Friday 31 July 2026 or the announcement.

Quarterlies are handled separately and follow the desk's own one-line note
format. Each goes in the quarterlies array as a SINGLE dense line carrying the
numbers a reader actually wants: production for the quarter, AISC or unit costs,
cash at bank and its quarter-on-quarter movement, progress against guidance, and
any stated catalyst. Worked examples of the exact register and density expected:

  Northern Star sold 433koz gold at AISC $2.7k/oz, lifted cash and bullion to
  $1.2B (+$52m QoQ), and began commissioning Stage 1 of the KCGM Mill Expansion
  for a September tie-in

  Ramelius closed the June quarter with $650m cash and gold (+$43.1m QoQ) after
  producing 53.5koz gold at AISC of $2k/oz

  West African produced 125koz gold at AISC US$1,730/oz, cash plus bullion
  +US$46m QoQ to US$777m; FY26 guidance maintained

Note the shorthand: koz, Moz, kt, Mt, Mwmt, kt SC6, US$/dmt, and thousands as k
so that AISC reads $2.7k/oz. Do not open the line with the ticker or the market
cap, both are added automatically. Do not pad with adjectives. If a quarterly
contains something genuinely market-moving, a guidance revision, a maiden
resource or reserve, an impairment, a funding gap, then it does not belong in
the quarterlies section at all: write it up in summaries with the other material
items and say in the body that it arrived inside a quarterly."""

SCHEMA = {
    "name": "briefing",
    "description": "The written content of one daily watchlist briefing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lead": {
                "type": "string",
                "description": ("Lead paragraph for the Confirmed Announcements page. "
                                "States how many of the watchlist names had a confirmed "
                                "announcement and leads with the most material item, "
                                "including its stated reason and real figures."),
            },
            "rows": {
                "type": "array",
                "description": "One row per material announcement, most material first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "company": {"type": "string"},
                        "announcement": {
                            "type": "string",
                            "description": ("Under 50 characters. Table cells do not wrap. "
                                            "Include the halt reason or headline figure if it fits."),
                        },
                        "type": {"type": "string", "description": "Short label, e.g. Halt, Drilling, M&A."},
                        "date": {"type": "string", "description": "AWST date as '23 July 2026'."},
                        "document_key": {
                            "type": "string",
                            "description": ("The document key of the announcement this "
                                            "refers to, copied exactly from its block "
                                            "header. Used to link to the source."),
                        },
                    },
                    "required": ["ticker", "company", "announcement", "type", "date",
                                 "document_key"],
                },
            },
            "summaries": {
                "type": "array",
                "description": "A paragraph per material announcement, with the real numbers.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "heading": {"type": "string"},
                        "body": {
                            "type": "string",
                            "description": (
                                "Two to four sentences, 40 to 70 words. Lead with the single "
                                "fact that moves the name, then the figures that size it. A "
                                "simple item like a trading halt needs one or two sentences, "
                                "not four. Leave out adviser and broker credits, settlement "
                                "dates, listing-rule capacity references, and hole-by-hole "
                                "listings beyond the best two or three."
                            ),
                        },
                        "document_key": {
                            "type": "string",
                            "description": ("The document key of the announcement this "
                                            "refers to, copied exactly from its block "
                                            "header. Used to link to the source."),
                        },
                    },
                    "required": ["ticker", "heading", "body", "document_key"],
                },
            },
            "quarterlies": {
                "type": "array",
                "description": ("Quarterly activities and cash flow reports, one entry each. "
                                "Secondary to the material items but always included."),
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "company": {"type": "string"},
                        "headline": {"type": "string"},
                        "summary": {
                            "type": "string",
                            "description": (
                                "ONE dense line in house note style. Start with the company name "
                                "and a verb, then the headline figures separated by commas or "
                                "semicolons. No leading ticker and no market cap, those are added "
                                "automatically. Example of the required style: 'Northern Star sold "
                                "433koz gold at AISC $2.7k/oz, lifted cash and bullion to $1.2B "
                                "(+$52m QoQ), and began commissioning Stage 1 of the KCGM Mill "
                                "Expansion for a September tie-in'. Use koz, Moz, kt, Mt, kt SC6, "
                                "US$/dmt, and thousands as k (AISC $2.7k/oz). Give quarter-on-quarter "
                                "cash movement in parentheses as (+$52m QoQ). Every figure must come "
                                "from the source text."
                            ),
                        },
                        "document_key": {
                            "type": "string",
                            "description": ("The document key of the announcement this "
                                            "refers to, copied exactly from its block "
                                            "header. Used to link to the source."),
                        },
                    },
                    "required": ["ticker", "company", "headline", "summary",
                                 "document_key"],
                },
            },
            "watch_items": {
                "type": "array",
                "description": "Live situations with their stated reason and expected resolution timing.",
                "items": {"type": "string"},
            },
            "day_in_brief": {
                "type": "string",
                "description": ("Closing summary. Leads with the most material live situation "
                                "including its stated reason and real figures."),
            },
            "subtitle": {
                "type": "string",
                "description": "One line stating the day's conclusion, for the Coverage Notes page.",
            },
        },
        "required": ["lead", "rows", "summaries", "quarterlies", "watch_items",
                     "day_in_brief", "subtitle"],
    },
}


def _block(record, limit):
    text = (record.get("text") or "").strip()
    body = text[:limit] if text else f"[NOT READABLE: {record.get('text_status')}]"
    return (
        f"### {record['ticker']} | {record['company']}\n"
        f"Headline: {record['headline']}\n"
        f"Lodged: {record['time_awst']} AWST on {record['date_awst']} "
        f"(document key {record['document_key']})\n"
        f"Price sensitive: {'yes' if record['price_sensitive'] else 'no'}\n"
        f"Signals detected: {', '.join(record.get('signals') or []) or 'none'}\n"
        f"--- announcement text ---\n{body}\n"
    )


def build_prompt(pack, ranked):
    parts = [
        f"Window: {pack['window_start_awst']} to {pack['window_end_awst']} (AWST).",
        f"Watchlist size: {pack['tickers_checked']} ticker codes.",
        f"Announcements found in window: {len(pack['announcements'])}.",
        "",
    ]

    if ranked["full"]:
        parts.append("## MATERIAL ITEMS, full text follows. Summarise each with real figures.\n")
        parts += [_block(r, MAX_FULL_CHARS) for r in ranked["full"]]
    else:
        parts.append("## No material items were identified in this window.\n")

    if ranked.get("quarterly"):
        parts.append("\n## QUARTERLIES. Put each of these in the quarterlies array with "
                     "production, unit costs, cash and guidance progress. Promote one to "
                     "the material items instead if its content warrants it.\n")
        parts += [_block(r, MAX_QUARTERLY_CHARS) for r in ranked["quarterly"]]

    if ranked["digest"]:
        parts.append("\n## ROUTINE ITEMS, opening extract only. List them, do not "
                     "write them up at length. If one of these is clearly more "
                     "significant than its headline suggests, say so.\n")
        parts += [_block(r, MAX_DIGEST_CHARS) for r in ranked["digest"]]

    parts.append(
        "\nWrite the briefing. If nothing was confirmed in the window, say so "
        "plainly in the lead and return an empty rows array."
    )
    return "\n".join(parts)


def summarise(pack, ranked, api_key=None, model=None):
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=model or MODEL,
        max_tokens=8000,
        system=SYSTEM,
        tools=[SCHEMA],
        tool_choice={"type": "tool", "name": "briefing"},
        messages=[{"role": "user", "content": build_prompt(pack, ranked)}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "briefing":
            return block.input
    raise RuntimeError(
        "The model did not return a briefing payload. Raw response:\n"
        + json.dumps([b.model_dump() for b in response.content], indent=2)[:2000]
    )

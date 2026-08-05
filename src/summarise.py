"""Writes the briefing, one announcement per API call.

Everything used to go to the model in a single prompt. That is cheaper to write,
and it is how a production figure from one company's quarterly ended up in
another's summary: nothing stopped it, because every number sat in the same
context window. Instructions reduce that risk. They do not remove it.

Each announcement now gets its own call, carrying only its own text. A summary
of Pantoro's quarterly cannot borrow Black Cat's production figure, because the
model writing it has never seen Black Cat. The isolation is structural rather
than instructed, which is the only kind that holds.

Only the closing synthesis sees everything, and it works from the per-item
summaries already written and figure-checked, not from raw announcements.

Cost is close to unchanged. The announcements are the bulk of the tokens and
each is still sent exactly once. What repeats is the system prompt, which adds
roughly a fifth on a normal day.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor

import anthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
QUARTERLY_MODEL = os.environ.get("CLAUDE_QUARTERLY_MODEL", MODEL)

# Caps after boilerplate is stripped. A mining announcement carries its argument
# in the first few pages; what follows is appendices.
MAX_FULL_CHARS = 20_000
MAX_QUARTERLY_CHARS = 12_000
WORKERS = 4

# Everything from here on is regulatory appendix, not content. On a real day
# this is 45% of the text and none of it can inform a summary: JORC Table 1
# runs to dozens of pages of sampling methodology, and tenement schedules and
# forward-looking-statement blocks are pure boilerplate. Stripping it is the
# single largest saving available, and it costs nothing in quality.
BOILERPLATE = re.compile("|".join([
    r"JORC Code,? 2012 Edition\s*[-\u2013\u2014]?\s*Table 1",
    r"\bTable 1\s*[-\u2013\u2014:]?\s*Section 1",
    r"Section 1[:\s]+Sampling Techniques",
    r"Sampling Techniques and Data",
    r"Competent Person'?s?\s+Statement",
    r"Forward[- ]Looking Statements?",
    r"Schedule of (?:Mining )?Tenements",
    r"Tenement Schedule",
    r"Corporate Directory",
    r"Appendix 1[:\s]+JORC",
]), re.I)
MIN_KEEP = 4_000        # never trim a short announcement to nothing


def trim(text, limit):
    """Drop the regulatory appendices, then cap what remains."""
    if not text:
        return "", 0
    match = BOILERPLATE.search(text, MIN_KEEP)
    body = text[:match.start()] if match else text
    return body[:limit], len(text) - len(body[:limit])

HOUSE = """House writing rules, which are absolute:
- No em dashes anywhere. Use a comma, a colon, parentheses, or a new sentence.
- No en dashes as punctuation. Write "A$1.20 to A$1.40", not a dash range.
- Australian English: mineralisation, analyse, metres, tonnes, licence (noun).
- Third person. Never "we".
- Currency unspaced and prefixed: A$5.0M in tables, A$71.5 million in prose.
- No space between a number and its unit: 3km, 5m, 0.9% Cu, 4.73g/t Au.
- Drill intercepts in house form: 5m @ 2.4% Cu from 17m (WCRC006).
- Dates as "23 July 2026". Never numeric formats.

On figures, which matters more than style:
- Every figure must appear in the announcement text given to you below. You have
  been given one announcement and nothing else. If a number is not in that text,
  it does not go in the briefing. Never supply a figure from memory, from what
  you know of the company, or from what a comparable producer reported.
- A trading halt is never reported as a bare "trading halt". Halt notices state
  their purpose. Report that purpose and the expected resumption date."""

BANNED = """Never write that something was reiterated, restated, previously
announced, confirmed, or refer the reader to an earlier announcement. Quarterlies
restate by definition. Saying so is noise, and it wastes the line."""

QUARTERLY_GUIDE = f"""This is a quarterly. Write the quarter's own numbers and
nothing else: production, unit costs or AISC, cash and its quarter-on-quarter
movement, progress against guidance, and any stated catalyst for next quarter.

You are given this company's earlier announcement headlines. Use them only to
recognise what NOT to write about. A quarterly typically recaps a feasibility
study, a resource upgrade or a final investment decision that was released
separately weeks earlier. That material is not what this section is for. Do not
summarise it, do not quote its economics, and do not mention that the quarterly
repeated it. Write the operating numbers.

{BANNED}"""

ITEM_GUIDE = f"""You are given this company's earlier announcement headlines.
Use them to avoid presenting old material as though it broke today. If the
substance of this announcement was already released, keep the entry short and
factual about what is actually new in it.

{BANNED}"""

ITEM_SCHEMA = {
    "name": "item",
    "description": "One announcement, written up.",
    "input_schema": {
        "type": "object",
        "properties": {
            "announcement": {
                "type": "string",
                "description": (
                    "The table cell, under 62 characters, no full stop. If the "
                    "announcement reports drill or assay results this MUST be the "
                    "single best intercept, written in house form and nothing else: "
                    "'5.1m @ 9.68g/t Au from 353m'. Add the hole ID in brackets only "
                    "if it still fits. Choose the intercept the company leads on, "
                    "normally the highest grade-thickness. Do not write 'high-grade "
                    "results', 'multiple intercepts' or 'drilling confirms "
                    "continuity' when a number is available, the number is the "
                    "point. For anything else: the halt reason and resumption date, "
                    "the raise size and price, the resource tonnes and grade, or the "
                    "plainest statement of what happened."
                ),
            },
            "heading": {"type": "string", "description": "One line, states the finding."},
            "body": {
                "type": "string",
                "description": (
                    "Two to four sentences, 40 to 70 words, fewer if the item is "
                    "simple. Lead with the fact that moves the name, then the "
                    "figures that size it, then the next catalyst. Leave out "
                    "adviser credits, tranche settlement dates, listing-rule "
                    "capacity references, and drill holes beyond the best two or "
                    "three."
                ),
            },
            "is_restatement": {
                "type": "boolean",
                "description": "True if this restates something previously announced.",
            },
            "restated_from": {
                "type": "string",
                "description": "The earlier headline and date, or empty.",
            },
        },
        "required": ["announcement", "heading", "body",
                     "is_restatement", "restated_from"],
    },
}

QUARTERLY_SCHEMA = {
    "name": "quarterly",
    "description": "One quarterly, as a single desk-note line.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "ONE dense line. Open with the company name and a verb, then "
                    "production, unit costs, cash and its quarter-on-quarter "
                    "movement, and progress against guidance. Do not open with the "
                    "ticker or the market cap, those are added automatically. Style "
                    "to match: 'Ramelius closed the June quarter with $650m cash and "
                    "gold (+$43.1m QoQ) after producing 53.5koz gold at AISC of "
                    "$2k/oz'. Use koz, Moz, kt, Mt and thousands as k. Operating "
                    "numbers only. Do not summarise a study or resource upgrade "
                    "that was announced separately, and never say anything was "
                    "reiterated, restated or previously announced."
                ),
            },
            "is_restatement": {"type": "boolean"},
            "restated_from": {"type": "string"},
        },
        "required": ["summary", "is_restatement", "restated_from"],
    },
}

BRIEF_SCHEMA = {
    "name": "briefing",
    "description": "The framing around the items, written from their summaries.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lead": {"type": "string",
                     "description": ("Lead paragraph. Use the counts given verbatim and "
                                     "lead with the most material item.")},
            "subtitle": {"type": "string", "description": "One line, the day's conclusion."},
            "watch_items": {"type": "array", "items": {"type": "string"},
                            "description": "Live situations with stated reason and timing."},
            "day_in_brief": {"type": "string",
                             "description": "Closing summary, most material first."},
        },
        "required": ["lead", "subtitle", "watch_items", "day_in_brief"],
    },
}


# The synthesis call returns a JSON tool payload. Once in a while the model
# switches mid-payload into its own tag syntax, and the whole of watch_items,
# day_in_brief and the closing tags arrive as one string in the watch_items
# field. That is what happened on 4 August 2026: the watch items rendered one
# character per bullet and the Day in Brief section came out empty, because the
# key no longer existed. The output is now unpacked and repaired before anyone
# downstream sees it, and the call is retried once if a field is still missing.
TAGGED_ITEM = re.compile(r"<item>(.*?)</item>", re.S | re.I)
LEAKED_FIELD = re.compile(
    r'<parameter\s+name="([a-z_]+)"\s*>(.*?)(?=</parameter>|<parameter\s|\Z)',
    re.S | re.I,
)
STRAY_TAG = re.compile(r"</?[a-z_][a-z0-9_.\-]*(?:\s[^<>]*)?/?>", re.I)

# Where a field's real content stops and the leaked payload begins. A closing
# tag or the start of the next parameter is always the end of the prose, so the
# text is cut there rather than having the tags picked out of it. Stripping
# alone is not enough: it turns "expansion.</body> <parameter
# name="is_restatement">false" into "expansion. false", which reads as prose and
# would go out looking deliberate.
CUT_AT = re.compile(
    r"</[a-z_][a-z0-9_.\-]*>|<parameter\b|</?(?:function_calls|invoke|tool_use)\b",
    re.I,
)

TEXT_FIELDS = ("lead", "subtitle", "day_in_brief")
ITEM_TEXT_FIELDS = ("announcement", "heading", "body", "summary", "restated_from")


def _clean(value):
    """Cut a field at the first leaked tag, then remove anything left over."""
    text = str(value or "")
    cut = CUT_AT.search(text)
    if cut:
        text = text[:cut.start()]
    return STRAY_TAG.sub("", text).strip()


def _watch_list(value):
    """watch_items must be a list of strings. Sometimes it is one long string."""
    if isinstance(value, str):
        value = re.split(r"</watch_items>", value, maxsplit=1, flags=re.I)[0]
        items = TAGGED_ITEM.findall(value)
        if not items:
            items = re.split(r"\n+|(?:^|\s)[-\u2022]\s+", value)
        value = items
    return [s for s in (_clean(v) for v in (value or [])) if len(s) > 1]


def _normalise_item(payload):
    """Repair one item payload the same way the framing is repaired.

    The 5 August briefing carried a St Barbara summary that ended
    "...ahead of an FY28 program expansion.</body> <parameter
    name="is_restatement">false" in the reader's inbox. Same fault as the
    4 August watch items, one level down: the model finished the prose and then
    closed the payload in its own tag syntax instead of JSON, so the trailing
    markup landed inside the body string. Every field the model writes is now
    cut at the leak, and any field that ended up swallowed by another is
    recovered from it.
    """
    out = dict(payload or {})
    blob = "\n".join(str(out.get(k) or "")
                     for k in ITEM_TEXT_FIELDS if isinstance(out.get(k), str))
    for name, value in LEAKED_FIELD.findall(blob):
        if name in ITEM_TEXT_FIELDS and not _clean(out.get(name)):
            out[name] = value
        elif name == "is_restatement" and not isinstance(out.get(name), bool):
            out[name] = _clean(value).lower().startswith("true")
    for key in ITEM_TEXT_FIELDS:
        if isinstance(out.get(key), str):
            out[key] = _clean(out[key])
    if isinstance(out.get("is_restatement"), str):
        out["is_restatement"] = out["is_restatement"].strip().lower() == "true"
    return out


def _normalise_framing(framing):
    """Repair a framing payload, recovering any field that leaked into another."""
    out = dict(framing or {})
    blob = "\n".join(str(out.get(k) or "") for k in ("watch_items",) + TEXT_FIELDS
                      if isinstance(out.get(k), str))
    for name, value in LEAKED_FIELD.findall(blob):
        if name in TEXT_FIELDS and not _clean(out.get(name)):
            out[name] = value
    out["watch_items"] = _watch_list(out.get("watch_items"))
    for key in TEXT_FIELDS:
        out[key] = _clean(out.get(key))
    return out


def _client(api_key=None):
    return anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])


def _call(client, system, prompt, schema, model=None, max_tokens=2000):
    resp = client.messages.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        system=system,
        tools=[schema],
        tool_choice={"type": "tool", "name": schema["name"]},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"no {schema['name']} payload returned")


def _prior(record):
    prior = record.get("prior_announcements") or []
    if not prior:
        return "No earlier announcements were found for this company."
    lines = [f"  {p['date_awst']}: {p['headline']}" for p in prior[:25]]
    return "This company's earlier announcements:\n" + "\n".join(lines)


def _prompt(record, limit):
    text = (record.get("text") or "").strip()
    body, dropped = trim(text, limit)
    record["_chars_dropped"] = dropped
    if not body:
        body = f"[NOT READABLE: {record.get('text_status')}]"
    return (
        f"{record['ticker']}, {record['company']}.\n"
        f"Headline: {record['headline']}\n"
        f"Lodged {record['time_awst']} AWST on {record['date_awst']}.\n"
        f"Price sensitive: {'yes' if record.get('price_sensitive') else 'no'}\n\n"
        f"{_prior(record)}\n\n"
        f"--- the announcement ---\n{body}\n"
    )


def summarise_item(record, client=None, model=None):
    client = client or _client()
    quarterly = record.get("tier") == "quarterly"
    schema = QUARTERLY_SCHEMA if quarterly else ITEM_SCHEMA
    limit = MAX_QUARTERLY_CHARS if quarterly else MAX_FULL_CHARS
    system = (
        "You write one entry for the Discovery Capital Partners daily ASX "
        "watchlist briefing. You are given exactly one announcement and must "
        "write only about that company.\n\n"
        f"{HOUSE}\n\n{QUARTERLY_GUIDE if quarterly else ITEM_GUIDE}"
    )
    prompt = _prompt(record, limit)
    model = model or (QUARTERLY_MODEL if quarterly else MODEL)
    out = _normalise_item(_call(client, system, prompt, schema, model=model))

    # A leak that consumed the prose rather than trailing it leaves nothing to
    # print, so ask once more before publishing an empty card.
    written = "summary" if quarterly else "body"
    if not out.get(written):
        print(f"  ! {record['ticker']} came back with no {written}, retrying once")
        out = _normalise_item(_call(
            client, system,
            prompt + "\n\nReturn every field of the tool schema as proper JSON. "
                     "Write the prose as plain text with no tags of any kind in it.",
            schema, model=model))

    out.update({
        "ticker": record["ticker"],
        "company": record["company"],
        "headline": record["headline"],
        "date": record["date_awst"],
        "document_key": record["document_key"],
        "_source_text": record.get("text") or "",
    })
    return out


def summarise_items(records, client=None, model=None):
    """Each announcement in its own call, a few at a time."""
    client = client or _client()
    out = [None] * len(records)

    def one(i):
        try:
            out[i] = summarise_item(records[i], client=client, model=model)
        except Exception as exc:                               # noqa: BLE001
            print(f"  ! summary failed for {records[i]['ticker']}: {exc}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, range(len(records))))
    return [x for x in out if x]


def synthesise(materials, quarterlies, pack, client=None, model=None):
    """Lead, subtitle, watch items and closing, from the written summaries."""
    client = client or _client()
    names = sorted({a["ticker"] for a in pack["announcements"]})
    parts = [
        f"Window: 24 hours to {pack['window_end_awst']} (AWST).",
        "",
        "COUNTS, use these exact numbers and do not recount:",
        f"  distinct watchlist names that announced: {len(names)}",
        f"  confirmed announcements: {len(materials)}",
        f"  quarterlies: {len(quarterlies)}",
        f"  watchlist size: {pack['tickers_checked']} ticker codes",
        "",
        "CONFIRMED ANNOUNCEMENTS, already written and figure-checked:",
    ]
    for m in materials:
        parts.append(f"  {m['ticker']}: {m.get('heading','')}\n    {m.get('body','')}")
    if quarterlies:
        parts.append("\nQUARTERLIES, secondary. Refer to them as a group rather than "
                     "individually, and never present restated material as news:")
        for q in quarterlies:
            parts.append(f"  {q['ticker']}: {q.get('summary','')}")
    parts.append("\nWrite the framing. Quote only figures that appear above. If "
                 "nothing was confirmed, say so plainly in the lead.")

    system = (
        "You write the framing for the Discovery Capital Partners daily ASX "
        "watchlist briefing, from summaries already written and checked.\n\n"
        f"{HOUSE}\n\n"
        "Quarterlies are secondary. Never lead on a quarterly. Never say that "
        "anything was reiterated, restated or previously announced."
    )
    prompt = "\n".join(parts)
    framing = _normalise_framing(
        _call(client, system, prompt, BRIEF_SCHEMA, model=model, max_tokens=4000))

    missing = [k for k in ("lead", "day_in_brief") if not framing.get(k)] \
        or ([] if framing["watch_items"] else ["watch_items"])
    if missing:
        print(f"  ! framing came back without {', '.join(missing)}, retrying once")
        retry = _normalise_framing(_call(
            client, system,
            prompt + "\n\nReturn every field of the tool schema as proper JSON. "
                     "watch_items is a JSON array of plain strings, with no tags "
                     "of any kind inside it.",
            BRIEF_SCHEMA, model=model, max_tokens=4000))
        for key in TEXT_FIELDS:
            framing[key] = framing.get(key) or retry.get(key, "")
        framing["watch_items"] = framing["watch_items"] or retry["watch_items"]
    return framing

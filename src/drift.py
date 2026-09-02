#!/usr/bin/env python3
"""Weekly hygiene check on the watchlist. Reads the archive, prints, never sends.

  python src/drift.py            report on the last 90 days of archived packs

Two questions, both answered from files already in the repo, so this costs no
requests and no API calls:

  1. Does each name's recent paperwork still match its commodity tag?
     A gold explorer that has pivoted to copper keeps turning up under Gold
     until someone notices. This counts commodity words in the full text of
     every archived announcement per name and flags any whose dominant metal
     is not the one it is tagged with. It only flags when the signal is
     decisive: at least two documents, at least twenty hits, and one metal
     taking three quarters of them. Poly-metallic names (SLS at Au 140 / Cu
     141) sit below that bar on purpose, because for them there is no right
     answer to flag.

  2. Which names have lodged nothing at all in the period?
     Not a fault, but a list worth glancing at once a week: a name that has
     gone quiet for three months may have been taken over, suspended, or
     re-coded, and its line in watchlist.txt is doing nothing.

This never reclassifies anything. It prints what it found and exits 0. The
decision stays with whoever edits the config.
"""

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import load_watchlist, load_commodities, load_watchlist_tags  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "archive")

DAYS = 90
MIN_DOCS = 2
MIN_HITS = 20
DOMINANCE = 0.75

# Keyed by the commodity code used in config/commodities.txt. A commodity with
# no terms here is simply not assessed. The Au patterns avoid the bare token
# "Au" because it appears in "August", "AUD" and "Australia".
TERMS = {
    "au": [r"\bgold\b", r"g/t\s*Au\b", r"\bAISC\b", r"\bdor[eé]\b", r"\bounces?\b"],
    "cu": [r"\bcopper\b", r"%\s*Cu\b", r"\bCu-Au\b", r"\bchalcopyrite\b", r"\bcathode\b"],
    "u":  [r"\buranium\b", r"U3O8", r"U₃O₈", r"\byellowcake\b", r"lb\s*U\b", r"\bISR\b"],
    "ag": [r"\bsilver\b", r"g/t\s*Ag\b", r"\bAgEq\b"],
    "ni": [r"\bnickel\b", r"%\s*Ni\b", r"\bsulphide\b", r"\blaterite\b"],
    "li": [r"\blithium\b", r"\bLi2O\b", r"\bspodumene\b", r"\bLCE\b"],
}


def _packs(archive, days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for path in sorted(glob.glob(os.path.join(archive, "*-pack.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                pack = json.load(fh)
        except (OSError, ValueError):
            continue
        try:
            end = datetime.fromisoformat(pack.get("window_end_utc", ""))
        except (TypeError, ValueError):
            continue
        if end >= cutoff:
            yield pack


def assess(tickers, code_of, commodities, archive=ARCHIVE, days=DAYS):
    """Return (drift, silent, assessed) without printing anything."""
    patterns = {code: [re.compile(x, re.I) for x in TERMS.get(code.lower(), [])]
                for code, _label in commodities}
    hits = defaultdict(Counter)
    docs = Counter()
    for pack in _packs(archive, days):
        for a in pack.get("announcements") or []:
            t = a.get("ticker")
            if t not in code_of:
                continue
            docs[t] += 1
            text = a.get("text") or ""
            if not text:
                continue
            for code, pats in patterns.items():
                hits[t][code] += sum(len(p.findall(text)) for p in pats)

    drift, assessed = [], 0
    for t in tickers:
        c = hits.get(t)
        if not c or docs[t] < MIN_DOCS:
            continue
        total = sum(c.values())
        if total < MIN_HITS:
            continue
        assessed += 1
        top, n = c.most_common(1)[0]
        tagged = code_of.get(t)
        if top != tagged and n / total >= DOMINANCE:
            drift.append((t, tagged, top, round(100 * n / total), docs[t]))
    silent = [t for t in tickers if docs[t] == 0]
    return drift, silent, assessed


def main():
    tickers = load_watchlist()
    commodities, notes = load_commodities()
    code_of, tag_notes = load_watchlist_tags(commodities=commodities)
    for note in notes + tag_notes:
        print(f"  ! {note}")
    if not commodities:
        print("No commodities configured, so there is nothing to check tags against.")
        code_of = {t: None for t in tickers}

    label_of = dict(commodities)
    drift, silent, assessed = assess(tickers, code_of, commodities)

    print(f"Watchlist drift check: {len(tickers)} names, last {DAYS} days of archive, "
          f"{assessed} with enough text to assess.")
    if drift:
        print(f"\n{len(drift)} name(s) whose recent announcements do not read like their tag:")
        for t, tagged, top, pct, n in drift:
            print(f"  drift: {t} tagged {label_of.get(tagged, tagged)}, {pct}% of commodity "
                  f"mentions across {n} documents are {label_of.get(top, top)}")
        print("  Nothing has been changed. Edit the tag in config/watchlist.txt if you agree.")
    else:
        print("No tag drift found.")

    if silent:
        print(f"\n{len(silent)} name(s) lodged nothing in the last {DAYS} days: "
              f"{', '.join(silent)}")
        print("  Not a fault. Worth a glance for takeovers, suspensions or re-codes.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                                   # noqa: BLE001
        # A hygiene report must never look like an outage.
        print(f"drift check could not run: {type(exc).__name__}: {exc}")
        sys.exit(0)

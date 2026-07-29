# ASX Watchlist Briefing

Emails a Discovery-styled briefing on the mining watchlist every morning at
08:00 AWST, at the ASX open. Runs on GitHub's servers, so nothing of yours has
to be switched on.

---

## Adding or removing people from the mailing list

Open **`config/recipients.txt`**, edit it, save. That is the whole job, and it
can be done in the browser without installing anything:

1. Open the file on GitHub
2. Click the pencil icon
3. Add a line with the person's email, or delete their line
4. Click **Commit changes**

The next morning's run picks it up. Put a `#` in front of a line to pause
someone without deleting them, which is useful when somebody is on leave.

**`config/watchlist.txt`** works the same way for ASX codes.

Neither file needs code changes, and a mistake in either one stops the run with
a plain-English message rather than sending a broken briefing.

---

## What it does each morning

1. Pulls every announcement published market-wide from the ASX announcements
   feed, in one request, and keeps the ones on the watchlist inside the last 24
   hours (AWST, computed as UTC+8 from the exchange's own timestamp).
2. Downloads each of those announcements and extracts the full text.
3. Scores them **on the text, not the headline**, so a routine-sounding title
   cannot hide a resource estimate, a guidance change or a transaction.
4. Sends the material ones to Claude with their full text, and the quarterlies
   with theirs, asking for real figures only.
5. Joins market cap onto the quarterlies from the exchange data, so that
   number is never model-written.
6. Builds the PDF, renders the HTML email, sends it, and commits the day's
   evidence to `archive/`.

### Why it reads the text rather than the headline

Filtering on headlines and the price-sensitive flag misses things. "Quarterly
Activities Report" is a dull title that can carry a guidance revision or a
maiden reserve, and the price-sensitive flag is set by the company rather than
to an objective standard. Since the text has already been downloaded and costs
nothing to read locally, the decision is made on the body of the document.

A quarterly is only promoted into the main section when something actually
moved: a revision, a maiden resource or reserve, an impairment, a suspension.
Otherwise it lands in the Quarterlies section, which is always included.

### Nothing is dropped silently

An announcement that exists but could not be parsed, usually a scanned image,
is escalated and named in the briefing rather than omitted. A missing document
is a fact worth stating, not a gap to paper over.

---

## One-time setup

**1. Create a private repository** and push these files to it.

**2. Add the secrets.** Settings, then Secrets and variables, then Actions, then
New repository secret:

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `SMTP_HOST` | `smtp.gmail.com`, or your mail provider's server |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | The sending mailbox |
| `SMTP_PASSWORD` | An **app password**, not the account password |
| `SMTP_FROM` | The address the briefing comes from |

For Gmail you need 2FA on, then an App Password from your Google account
security settings. Office 365 and Fastmail work the same way.

**3. Check permissions.** Settings, Actions, General, Workflow permissions, set
to **Read and write**, so the run can commit the archive.

**4. Test it.** Actions tab, "Daily ASX watchlist briefing", Run workflow. That
button also lets anyone re-run a day by hand.

---

## Running it locally

```bash
pip install -r requirements.txt

python run.py --no-llm      # collect only, writes the evidence pack, no API cost
python run.py --dry-run     # build the PDF and email, do not send
python run.py               # the real thing
python run.py --pack archive/2026-07-29-pack.json   # rebuild, no network
```

`--pack` is the one to reach for when the wording needs changing but the data
does not. It rebuilds from a saved day without touching the network.

---

## What it costs

GitHub Actions is free at this volume, roughly 5% of the monthly allowance.
The only real cost is the Claude API, in the order of cents a day depending on
how many announcements land.

---

## When it breaks

GitHub emails you when a run fails, and the run fails loudly rather than
sending an empty-looking briefing. Common causes:

- **`CONFIG PROBLEM`** — a bad line in `recipients.txt` or `watchlist.txt`. The
  message names the line number.
- **`FeedError`** — the exchange feed changed shape. The run stops rather than
  reporting a quiet day that was not quiet.
- **SMTP authentication failed** — the app password was rotated or revoked.

The evidence pack for every day is committed to `archive/`, so you can always
see exactly what was retrieved, and re-run the wording from it.

---

## Notes

The `dcp/` folder is a copy of the house style report engine, vendored so the
runner can build the PDF without the wider toolchain. Keep it in step if the
house style changes.

The data comes from the ASX's own backing announcements service. It is not a
documented public API, so it can change without notice, which is why the code
fails loudly instead of guessing. If this becomes a firm process rather than
desk tooling, it is worth checking terms and considering a licensed feed.

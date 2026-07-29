# Setting it up

About 25 minutes, once. You do not need to know git or write any code.

Do the steps in this order, because steps 1 and 2 produce the passwords that
step 5 asks for.

---

## 1. Get an Anthropic API key (3 minutes)

This is what writes the briefing. It is a different thing from a Claude
subscription, and it is billed separately by usage.

1. Go to **console.anthropic.com** and sign in
2. **Settings** > **Billing**, add a payment method and put on credit. See the
   cost note at the bottom of this file for how much: $20 is a sensible start
   and lasts roughly five months
3. **Settings** > **API keys** > **Create key**, name it `asx-watchlist`
4. Copy the key somewhere safe now. It is shown once and never again

---

## 2. Create an email app password (5 minutes)

The script signs in to a mailbox to send from. Mail providers will not accept
your normal password for this, so you generate a single-purpose one.

**For Gmail:**

1. Go to **myaccount.google.com** > **Security**
2. Turn on **2-Step Verification** if it is not already on. App passwords do
   not exist as an option until you do
3. Go to **myaccount.google.com/apppasswords**
4. Type a name, `ASX watchlist`, and click **Create**
5. Copy the 16-character password. **Delete the spaces** when you paste it later

**For a Discovery Office 365 mailbox**, ask IT for SMTP credentials or an app
password. The host is usually `smtp.office365.com` on port `587`.

> Worth knowing: sending an internally-branded briefing from a personal Gmail
> works fine for you and one or two others, but if the whole team goes on the
> list it is better to send from a discoverycapital.com.au mailbox. Deliverability
> is better and it looks right in the header. That is only a change to the
> secrets in step 5, not to any code.

---

## 3. Create the repository (5 minutes)

1. Go to **github.com**, sign in or make an account
2. Click **+** top right > **New repository**
3. Name it `asx-watchlist`
4. Choose **Private**
5. Click **Create repository**

---

## 4. Put the files in it (5 minutes)

**The easy and reliable way, using GitHub Desktop:**

1. Install **GitHub Desktop** from desktop.github.com and sign in
2. **File** > **Clone repository**, pick `asx-watchlist`, choose a folder
3. Unzip the files you were sent, and copy everything inside the
   `asx-watchlist` folder into the cloned folder
4. Back in GitHub Desktop you will see all the files listed. Type a message like
   `first commit` and click **Commit to main**, then **Push origin**

**If you would rather not install anything**, you can drag the files into the
browser instead, but note one trap: the folder `.github` starts with a dot,
which means macOS Finder hides it and it will not be included in the drag. If
you take this route, upload everything else first, then create the workflow by
hand:

1. In the repo, click **Add file** > **Upload files**, drag in `run.py`,
   `README.md`, `requirements.txt` and the `config`, `src` and `dcp` folders,
   then **Commit changes**
2. Go to the **Actions** tab > **set up a workflow yourself**
3. Name the file `daily.yml` and paste in the contents of
   `.github/workflows/daily.yml` from the zip
4. **Commit changes**

---

## 5. Add the passwords as secrets (3 minutes)

Secrets are encrypted. Nobody can read them back out, including you, and they
never appear in the code or the logs.

In the repo: **Settings** > **Secrets and variables** > **Actions** >
**New repository secret**. Add these six, one at a time:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | the key from step 1 |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the full email address you are sending from |
| `SMTP_PASSWORD` | the app password from step 2, **no spaces** |
| `SMTP_FROM` | the same address as `SMTP_USER` |

---

## 6. Let it save the archive (1 minute)

**Settings** > **Actions** > **General**, scroll to **Workflow permissions**,
select **Read and write permissions**, **Save**.

Without this the briefing still sends, but the daily evidence cannot be
committed back to the repo.

---

## 7. Check who it is going to (1 minute)

Open `config/recipients.txt` in the repo, click the pencil, and make sure the
right people are on it. Commit.

---

## 8. Run it (3 minutes)

1. **Actions** tab
2. Click **Daily ASX watchlist briefing** on the left
3. Click **Run workflow** > **Run workflow**
4. Wait about a minute, then click into the run to watch it

From tomorrow it runs by itself at 08:00 AWST. That **Run workflow** button
stays available for re-running a day by hand whenever you want.

---

## When the first run fails

Expect this. The collection code has never met the live network, so treat the
first run as a shakedown rather than a launch.

Click into the failed run, open the **Build and send the briefing** step, and
read the bottom of the log. It will say one of:

| What it says | What to do |
|---|---|
| `CONFIG PROBLEM` | A bad line in `recipients.txt` or `watchlist.txt`. The message gives the line number |
| `SMTPAuthenticationError` | The app password is wrong, or has spaces in it. Regenerate and re-paste |
| `FeedError` | The exchange feed changed shape. Send me the message |
| `KeyError` / `TypeError` in `collect.py` | A field is not where the code expects. Send me the log |
| `credit balance is too low` | Top up at console.anthropic.com |
| `model: ... not found` | The model ID has moved on. Add a `CLAUDE_MODEL` secret with the current one from platform.claude.com/docs |

Send me whatever the log says and I will fix it.

---

## Day to day

Once it is running you should never need to touch it. The only two files anyone
edits are:

- **`config/recipients.txt`** to change who gets it
- **`config/watchlist.txt`** to change what it covers

Both are edited in the browser, and a mistake in either stops the run with a
plain message rather than sending something wrong.

GitHub emails you if a run fails, so silence means it worked.

---

## What it will cost to run

The only running cost is the Claude API. GitHub Actions is free at this volume.

On a normal day the briefing reads a handful of announcements in full plus a
dozen short extracts, which is roughly 27,000 tokens in and 1,500 out, about
**10 cents**. During quarterly season, when much of the watchlist reports inside
the same fortnight, a heavy day might read 25 quarterlies and run to 160,000
tokens, about **50 cents**.

Across a year, with four quarterly seasons and around 22 trading days a month,
that lands near **$3 to $4 a month, or roughly $45 a year**.

| Credit | Roughly lasts |
|---|---|
| $5 | 6 weeks, enough to prove it works |
| $20 | 5 to 6 months |
| $50 | a year, with headroom |

Two things that make this an estimate rather than a quote. The announcement
volume across 91 small and mid-cap miners is assumed, not measured, so your
first quarterly season is the real test. And Sonnet 5 is on introductory
pricing until 31 August 2026, so the first month runs about a third cheaper
than the figures above.

Check actual spend under **Usage** in the Anthropic console after the first
week, and again after the first quarterly season. If it comes in higher than
you want, the lever is `MAX_QUARTERLY_CHARS` in `src/summarise.py`, which caps
how much of each quarterly is read. Switching to Haiku via a `CLAUDE_MODEL`
secret would cut it to a third, but it is weaker at pulling figures out
accurately, which is the whole job, so I would lower the cap first.

# Singapore GP ticket monitor

Monitors the official Singapore GP Grandstands page for **Sunday** availability
in the Stamford Grandstand and Padang Grandstand. It only notifies Telegram on
a `sold out → available` transition, so an unchanged available state does not
produce repeat messages.

## One-time setup

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" python -m playwright install chromium
```

Open `.env` locally and paste the BotFather token after `TELEGRAM_BOT_TOKEN=`.
Do not paste it into chat or commit this file.

You already sent the bot `hello`. Find the chat ID with:

```bash
source .venv/bin/activate
python monitor.py --discover-chat
```

Copy the printed number into `.env` after `TELEGRAM_CHAT_ID=`. Then test the
notification:

```bash
python monitor.py --test-telegram
```

## Run it

For one safe status check:

```bash
python monitor.py --once
```

For continuous monitoring every 30 seconds:

```bash
python monitor.py
```

The first time it runs, a Chromium window opens. Keep it open (you can minimise
it): the official site occasionally withholds ticket cards from an invisible
browser session.

The first normal check records the current state without alerting. To receive
an alert immediately if a ticket is already available, use:

```bash
python monitor.py --once --notify-on-first-check
```

`state.json` is local state only. Delete it only if you deliberately want to
reset the monitor's remembered availability.

## Run in GitHub Actions (every five minutes)

GitHub Actions does not offer a reliable 30-second schedule. This included
workflow runs approximately every five minutes, and can sometimes start late
when GitHub is busy. It stores `state.json` in a **private** repository so an
available ticket is notified once, rather than once per scheduled run.

1. Create a new **private** GitHub repository and upload this whole folder.
2. In its GitHub page, open **Settings → Secrets and variables → Actions**.
3. Add two *repository secrets*: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
   Copy their values from your local `.env`; never place them in a committed
   file.
4. Open **Actions → Singapore GP ticket monitor → Run workflow** to make the
   first baseline check. Subsequent checks occur on GitHub's schedule.

The first successful run saves `state.json` without notifying. Future
successful runs alert only when a target changes from sold out to available.

#!/usr/bin/env python3
"""Singapore GP Sunday ticket monitor for Stamford and Padang grandstands."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from playwright.sync_api import Browser, Page, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
# Keep the large Playwright browser download inside this project instead of a
# user-wide cache. This also makes setup work on Macs with a locked-down cache.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BASE_DIR / ".playwright-browsers"))
STATE_FILE = BASE_DIR / "state.json"
TICKETS_URL = "https://singaporegp.sg/en/tickets/general-tickets/grandstands/"
TARGETS = ("Stamford Grandstand", "Padang Grandstand")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"targets": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Cannot read {STATE_FILE.name}: {exc}") from exc


def save_state(state: dict[str, Any]) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_FILE)


def close_quietly(resource: Any) -> None:
    """Close a Playwright page/browser without turning Ctrl+C cleanup into a traceback."""
    try:
        resource.close()
    except BaseException:
        # KeyboardInterrupt derives from BaseException, not Exception.
        pass


def inspect_targets(page: Page) -> dict[str, dict[str, Any]]:
    """Read the card that belongs to each named heading.

    This intentionally does not depend on card order or CSS-module class names.  It
    finds each exact visible heading, then its nearest ancestor containing exactly
    one heading and a Sun day badge.  The official site marks unavailable days with
    the `disabled` class; we retain the raw signal in state for diagnostics.
    """
    result = page.locator("h3").evaluate_all(
        """(headings, names) => Object.fromEntries(names.map(name => {
          const heading = headings.find(h => h.textContent.trim() === name);
          if (!heading) return [name, {found: false}];
          let card = heading.parentElement;
          while (card) {
            const ownHeadings = [...card.querySelectorAll('h3')]
              .filter(h => h.textContent.trim() === name);
            const sun = [...card.querySelectorAll('p')]
              .find(p => p.textContent.trim().toLowerCase() === 'sun');
            if (ownHeadings.length === 1 && sun) break;
            card = card.parentElement;
          }
          if (!card) return [name, {found: false}];
          const sun = [...card.querySelectorAll('p')]
            .find(p => p.textContent.trim().toLowerCase() === 'sun');
          const className = sun?.className || '';
          const disabled = sun?.classList.contains('disabled') ||
            sun?.getAttribute('aria-disabled') === 'true';
          return [name, {
            found: Boolean(sun),
            available: !disabled,
            sun_class: className,
            card_text: card.innerText.replace(/\\s+/g, ' ').trim()
          }];
        }))""",
        list(TARGETS),
    )
    return result


def check_site(browser: Browser) -> dict[str, dict[str, Any]]:
    page = browser.new_page()
    try:
        page.goto(TICKETS_URL, wait_until="domcontentloaded", timeout=45_000)
        page.locator("h3").filter(has_text="Stamford Grandstand").wait_for(
            state="visible", timeout=30_000
        )
        statuses = inspect_targets(page)
        missing = [name for name, status in statuses.items() if not status.get("found")]
        if missing:
            raise RuntimeError(f"Official ticket cards not found: {', '.join(missing)}")
        return statuses
    finally:
        close_quietly(page)


def telegram_request(method: str, payload: dict[str, str]) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram rejected the request: {data}")
    return data


def discover_chat_id() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates", timeout=20
    )
    response.raise_for_status()
    updates = response.json()
    if not updates.get("ok"):
        raise RuntimeError(f"Telegram rejected the request: {updates}")
    for update in reversed(updates.get("result", [])):
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = message.get("chat", {}).get("id")
        if chat_id:
            return str(chat_id)
    raise RuntimeError("No messages found. Open the bot, press Start, then send it 'hello'.")


def send_notification(chat_id: str, grandstand: str, test: bool = False) -> None:
    label = "🧪 Telegram test" if test else "🚨 Singapore GP ticket available"
    text = (
        f"{label}!\n\n"
        f"{grandstand}\n"
        "Sunday (11 October 2026)\n\n"
        f"Buy now: {TICKETS_URL}"
    )
    telegram_request("sendMessage", {"chat_id": chat_id, "text": text})


def run_once(browser: Browser, notify_on_first_check: bool = False) -> None:
    statuses = check_site(browser)
    state = load_state()
    targets = state.setdefault("targets", {})
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    for name, status in statuses.items():
        available = bool(status["available"])
        previous = targets.get(name, {}).get("available")
        logging.info("%s Sunday: %s", name, "AVAILABLE" if available else "sold out")
        should_notify = available and (previous is False or (previous is None and notify_on_first_check))
        if should_notify:
            if not chat_id:
                raise RuntimeError("TELEGRAM_CHAT_ID is empty in .env")
            send_notification(chat_id, name)
            logging.warning("Notification sent: %s", name)
        targets[name] = {**status, "available": available, "checked_at": now()}

    save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Check once, then exit.")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test message, then exit.")
    parser.add_argument("--discover-chat", action="store_true", help="Print the latest private chat ID, then exit.")
    parser.add_argument("--notify-on-first-check", action="store_true", help="Notify if a ticket is already available on the first check.")
    args = parser.parse_args()
    load_dotenv(BASE_DIR / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.discover_chat:
        print(discover_chat_id())
        return
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if args.test_telegram:
        if not chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID is empty in .env")
        send_notification(chat_id, "Singapore GP monitor", test=True)
        logging.info("Telegram test message sent")
        return

    interval = max(10, int(os.environ.get("CHECK_INTERVAL_SECONDS", "30")))
    headless = os.environ.get("HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        logging.info("Monitor started (%s browser)", "hidden" if headless else "visible")
        try:
            while True:
                try:
                    run_once(browser, notify_on_first_check=args.notify_on_first_check)
                except KeyboardInterrupt:
                    logging.info("Monitor stopped by user")
                    break
                except Exception as exc:
                    logging.exception("Check failed; state was left unchanged: %s", exc)
                if args.once:
                    break
                try:
                    time.sleep(interval)
                except KeyboardInterrupt:
                    logging.info("Monitor stopped by user")
                    break
        finally:
            close_quietly(browser)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Covers an interrupt while Playwright is entering or leaving its context.
        logging.info("Monitor stopped by user")

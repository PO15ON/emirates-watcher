# monitor_status.py
"""Monitor Emirates Group Careers application status and email on changes.

Improvements
============
* **Robust cookie‑banner dismissal** – waits up to 8 s for the OneTrust button
  every time we attempt to click the *Log in* button.
* **Reliable login click** – prioritises `#login` selector (unique id) and uses
  lowercase text variant.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from typing import Final, Optional
import time
from datetime import datetime

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STATUS_FILE: Final[Path] = Path("latest_status.txt")
CHECK_TIMEOUT_MS: Final[int] = int(os.getenv("CHECK_TIMEOUT_MS", "60000"))  # 60 s
COOKIE_WAIT_MS: Final[int] = 8000

load_dotenv()

USERNAME = os.getenv("EMIRATES_USER")
PASSWORD = os.getenv("EMIRATES_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# Selector constants
APPLICATION_TAB = (
    "#main-panel > section > div.section__header.section__header--tabs > "
    "div > ul > li:nth-child(2) > a"
)
STATUS_CELL = (
    "#main-panel > section > div.section__content > article > div > div > table > tbody > tr:nth-child(2) > td:nth-child(2)"
)
COOKIE_ACCEPT = "#onetrust-accept-btn-handler"
LOGIN_BUTTON_ID = "#login"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require(var: Optional[str], name: str) -> str:  # noqa: D401
    if not var:
        raise RuntimeError(f"Environment variable {name} is required but missing.")
    return var

USERNAME = _require(USERNAME, "EMIRATES_USER")
PASSWORD = _require(PASSWORD, "EMIRATES_PASS")
EMAIL_FROM = _require(EMAIL_FROM, "EMAIL_FROM")
EMAIL_TO = _require(EMAIL_TO, "EMAIL_TO")
EMAIL_PASSWORD = _require(EMAIL_PASSWORD, "EMAIL_PASSWORD")

# ---------------------------------------------------------------------------
# Lazy Playwright import
# ---------------------------------------------------------------------------

def _async_playwright():
    try:
        from playwright.async_api import async_playwright  # type: ignore
        return async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright not installed. Run 'pip install playwright' and 'playwright install'."
        ) from exc

# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def read_last_status(path: Path = STATUS_FILE) -> str:
    return path.read_text().strip() if path.exists() else ""

def write_last_status(status: str, path: Path = STATUS_FILE) -> None:
    path.write_text(status)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _compose_email(new_status: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Emirates application status updated"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(f"Your application status changed to: {new_status}")
    return msg

def _send_email(new_status: str) -> None:
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ssl.create_default_context()) as s:
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.send_message(_compose_email(new_status))

# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

async def _dismiss_cookies(page) -> None:  # noqa: D401
    try:
        await page.wait_for_selector(COOKIE_ACCEPT, timeout=COOKIE_WAIT_MS)
        await page.click(COOKIE_ACCEPT, force=True)
        await page.wait_for_selector(COOKIE_ACCEPT, state="detached", timeout=5000)
    except Exception:
        # banner didn’t appear – harmless
        pass


async def _click_login(page) -> None:  # noqa: D401
    """Ensure cookie banner gone, then click the Log in button."""
    await _dismiss_cookies(page)

    candidates = [
        LOGIN_BUTTON_ID,
        'button:has-text("Log in")',  # note lowercase "in"
        'text="Log in"',
        'button[value="Log in"]',
        'button[type="submit"]',
    ]
    for sel in candidates:
        if await page.is_visible(sel):
            await page.locator(sel).click(force=True)
            return
    raise RuntimeError("Login button not found – please update selectors.")

# ---------------------------------------------------------------------------
# Core scraping
# ---------------------------------------------------------------------------

async def _fetch_status() -> str:  # noqa: D401
    async_playwright = _async_playwright()
    from playwright.async_api import TimeoutError as PWTimeoutError  # type: ignore

    async with async_playwright() as p:  # type: ignore[attr-defined]
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(
            "https://external.emiratesgroupcareers.com/en_US/"
            "careersmarketplace/ProfileJobApplications",
            wait_until="networkidle",
            timeout=CHECK_TIMEOUT_MS,
        )

        # Login
        if await page.is_visible('input[name="username"]'):
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await _click_login(page)
            await page.wait_for_load_state("networkidle")

        # Applications tab
        if await page.is_visible(APPLICATION_TAB, timeout=CHECK_TIMEOUT_MS):
            await page.click(APPLICATION_TAB)
            await page.wait_for_load_state("networkidle")

        # Status extraction
        try:
            await page.wait_for_selector(STATUS_CELL, timeout=CHECK_TIMEOUT_MS)
            text = await page.text_content(STATUS_CELL, timeout=CHECK_TIMEOUT_MS)
        except PWTimeoutError:
            print(
                f"[warning] Status cell missing after {CHECK_TIMEOUT_MS/1000:.0f}s; returning empty."
            )
            text = None
        return (text or "").strip()

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def _check_once() -> None:  # noqa: D401
    status = await _fetch_status()
    last = read_last_status()
    if status != last:
        write_last_status(status)
        if status:
            print(status)
            _send_email(status)
        print(f"Status changed: {last or '[none]'} → {status or '[empty]'}")
    else:
        print("No change detected.")

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class _HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("_tmp_status.txt")
        self.tmp.unlink(missing_ok=True)

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def test_read_write(self):
        self.assertEqual(read_last_status(self.tmp), "")
        write_last_status("Under Review", self.tmp)
        self.assertEqual(read_last_status(self.tmp), "Under Review")

    def test_compose_mail(self):
        self.assertIn("Offer", _compose_email("Offer").get_content())

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _main() -> None:  # noqa: D401
    while True:
        print("Checking status at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        asyncio.run(_check_once())
        time.sleep(30*60)

if __name__ == "__main__":
    _main()

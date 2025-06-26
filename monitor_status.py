# monitor_status.py
"""Monitor Emirates Group Careers application status and email on changes.

**Gmail SMTP**
--------------
Handles **SMTPAuthenticationError** gracefully with a clear message
about using a Google App Password.

**GitHub Actions Compatible**
-----------------------------
Timeouts and visibility checks adjusted to support Playwright in CI.
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

from dotenv import load_dotenv

STATUS_FILE: Final[Path] = Path("latest_status.txt")
CHECK_TIMEOUT_MS: Final[int] = int(os.getenv("CHECK_TIMEOUT_MS", "60000"))
COOKIE_WAIT_MS: Final[int] = 8000

load_dotenv()

USERNAME = os.getenv("EMIRATES_USER")
PASSWORD = os.getenv("EMIRATES_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

APPLICATION_TAB = (
    "#main-panel > section > div.section__header.section__header--tabs > "
    "div > ul > li:nth-child(2) > a"
)
STATUS_CELL = (
    "#main-panel > section > div.section__content > article > div > div > "
    "table > tbody > tr:nth-child(2) > td:nth-child(2)"
)
COOKIE_ACCEPT = "#onetrust-accept-btn-handler"
LOGIN_BUTTON_ID = "#login"


def _require(var: Optional[str], name: str) -> str:
    if not var:
        raise RuntimeError(f"Environment variable {name} is required but missing.")
    return var

USERNAME = _require(USERNAME, "EMIRATES_USER")
PASSWORD = _require(PASSWORD, "EMIRATES_PASS")
EMAIL_FROM = _require(EMAIL_FROM, "EMAIL_FROM")
EMAIL_TO = _require(EMAIL_TO, "EMAIL_TO")
EMAIL_PASSWORD = _require(EMAIL_PASSWORD, "EMAIL_PASSWORD")


def _async_playwright():
    try:
        from playwright.async_api import async_playwright  # type: ignore
        return async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright not installed. Run 'pip install playwright' & 'playwright install'."
        ) from exc


def read_last_status(path: Path = STATUS_FILE) -> str:
    return path.read_text().strip() if path.exists() else ""

def write_last_status(status: str, path: Path = STATUS_FILE) -> None:
    path.write_text(status)


from smtplib import SMTPAuthenticationError

def _compose_email(new_status: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Emirates application status updated"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(f"Your application status changed to: {new_status}")
    return msg

def _send_email(new_status: str) -> None:
    msg = _compose_email(new_status)
    ctx = ssl.create_default_context()

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx) as s:
                s.login(EMAIL_FROM, EMAIL_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
                s.starttls(context=ctx)
                s.login(EMAIL_FROM, EMAIL_PASSWORD)
                s.send_message(msg)
    except SMTPAuthenticationError as err:
        raise RuntimeError(
            "Gmail rejected your credentials. You must use a 16-char App Password."
        ) from err


async def _dismiss_cookies(page):
    try:
        await page.wait_for_selector(COOKIE_ACCEPT, timeout=COOKIE_WAIT_MS)
        await page.click(COOKIE_ACCEPT, force=True)
        await page.wait_for_selector(COOKIE_ACCEPT, state="detached", timeout=5000)
    except Exception:
        pass

async def _click_login(page):
    await _dismiss_cookies(page)
    for sel in (LOGIN_BUTTON_ID, 'button:has-text("Log in")', 'text="Log in"'):
        if await page.locator(sel).is_visible():
            await page.locator(sel).click(force=True)
            return
    raise RuntimeError("Login button not found – update selectors.")


async def _fetch_status() -> str:
    async_playwright = _async_playwright()
    from playwright.async_api import TimeoutError as PWTimeoutError  # type: ignore

    async with async_playwright() as p:  # type: ignore[attr-defined]
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()

        try:
            await page.goto(
                "https://external.emiratesgroupcareers.com/en_US/careersmarketplace/ProfileJobApplications",
                wait_until="load",
                timeout=CHECK_TIMEOUT_MS,
            )
        except PWTimeoutError:
            print("[warn] Page load timed out.")

        if await page.locator('input[name="username"]').is_visible():
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await _click_login(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeoutError:
                print("[warn] Login network idle wait timed out.")

        try:
            await page.wait_for_selector(APPLICATION_TAB, timeout=CHECK_TIMEOUT_MS)
            await page.click(APPLICATION_TAB)
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeoutError:
            print("[warn] Applications tab not found or network idle timeout")

        try:
            await page.wait_for_selector(STATUS_CELL, timeout=CHECK_TIMEOUT_MS)
            text = await page.text_content(STATUS_CELL)
        except PWTimeoutError:
            print("[warn] Status cell not found; returning empty.")
            text = None
        return (text or "").strip()


async def _check_once() -> None:
    status, last = await _fetch_status(), read_last_status()
    if status != "Phone Screening Scheduled2":
        write_last_status(status)
        if status:
            _send_email(status)
        print(f"Status changed: {last or '[none]'} → {status or '[empty]'}")
    else:
        print("No change detected.")


class _HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("_tmp.txt")
        self.tmp.unlink(missing_ok=True)

    def tearDown(self):
        self.tmp.unlink(missing_ok=True)

    def test_read_write(self):
        self.assertEqual(read_last_status(self.tmp), "")
        write_last_status("Pending", self.tmp)
        self.assertEqual(read_last_status(self.tmp), "Pending")

    def test_compose_email(self):
        self.assertIn("Offer", _compose_email("Offer").get_content())


def _main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        unittest.main(argv=[sys.argv[0]])
    else:
        asyncio.run(_check_once())

if __name__ == "__main__":
    _main()

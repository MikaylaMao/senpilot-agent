"""Email-driven Nova Scotia UARB filing retrieval agent."""
from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import logging
import os
import re
import shutil
import smtplib
import tempfile
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as BrowserTimeout, sync_playwright

LOGGER = logging.getLogger(__name__)
TYPES = ("Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings")
TYPE_RE = re.compile(r"\b(Other\s+Documents|Key\s+Documents|Exhibits|Transcripts|Recordings)\b", re.I)
MATTER_RE = re.compile(r"\bM\d{5}\b", re.I)
MAX_FILES = 10


@dataclass
class Matter:
    number: str
    title: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def parse_request(subject: str, body: str) -> tuple[str, str]:
    content = subject + "\n" + body
    numbers = {m.upper() for m in MATTER_RE.findall(content)}
    kinds = {m.group(1).casefold(): next(t for t in TYPES if t.casefold() == m.group(1).casefold())
             for m in TYPE_RE.finditer(content)}
    if len(numbers) != 1 or len(kinds) != 1:
        raise ValueError("Please specify exactly one matter number (M followed by five digits) and one document type: " + ", ".join(TYPES))
    return numbers.pop(), next(iter(kinds.values()))


def _text(page: Page, label: str) -> str:
    """Read the displayed value adjacent to a label, accounting for FileMaker markup."""
    return page.evaluate("""label => {
      const all = [...document.querySelectorAll('body *')];
      const node = all.find(e => e.children.length === 0 && e.textContent.trim().toLowerCase() === label.toLowerCase());
      if (!node) return '';
      const parent = node.parentElement;
      const siblings = [...parent.children].filter(e => e !== node).map(e => e.textContent.trim()).filter(Boolean);
      return siblings[0] || parent.nextElementSibling?.textContent.trim() || '';
    }""", label)


def _counts(page: Page) -> dict[str, int]:
    text = page.locator("body").inner_text()
    counts = {}
    for kind in TYPES:
        match = re.search(re.escape(kind) + r"\s*[-–:]?\s*(\d+)\b", text, re.I)
        if match:
            counts[kind] = int(match.group(1))
    if not counts:
        raise RuntimeError("Could not determine document counts from matter tabs")
    return counts


def _matter_details(text: str, number: str) -> tuple[str, dict[str, str]]:
    """Parse the visible FileMaker matter header, whose values precede labels."""
    header = text.split("Back to Search Results", 1)[0]
    lines = [line.strip() for line in header.splitlines() if line.strip()]
    try:
        start = lines.index(number)
    except ValueError:
        return "", {}
    values = lines[start + 1:]
    if len(values) < 6 or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", values[3]):
        return "", {}
    category, status, title, received, finalized, matter_type = values[:6]
    return title, {
        "Type": matter_type,
        "Category": category,
        "Status": status,
        "Date Received": received,
        "Decision Date": finalized,
    }


def _button(page: Page, pattern: str):
    return page.get_by_role("button", name=re.compile(pattern, re.I)).first


def fetch_matter(number: str, kind: str, destination: Path, url: str) -> tuple[Matter, list[Path]]:
    destination.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=os.getenv("HEADLESS", "true").lower() != "false")
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # WebDirect can display its error dialog after DOMContentLoaded, inside an iframe.
            deadline = time.monotonic() + 20
            field = None
            while time.monotonic() < deadline:
                for frame in page.frames:
                    if frame.get_by_text("Database not available.", exact=False).count():
                        raise RuntimeError("The UARB FileMaker site says 'Database not available'; try again when its database returns")
                    candidate = frame.locator(".fm-textarea:has(.placeholder:text-is('eg M01234'))").first
                    if candidate.count() and candidate.is_visible():
                        field = candidate
                        break
                if field:
                    break
                page.wait_for_timeout(250)
            if field is None:
                raise RuntimeError("The UARB matter search page did not load; current URL: " + page.url)
            field_id = field.get_attribute("id")
            editor = page.locator(f'[id="{field_id}"] .text')
            editor.click()
            active_editor = page.locator(f'[id="{field_id}"] [contenteditable="true"]')
            active_editor.fill(number)
            active_editor.press("Tab")
            # The page has three Search buttons; use the one beside this field.
            page.locator("button").filter(has_text=re.compile(r"^Search$")).last.click()
            # WebDirect sometimes moves to a second search layout before opening
            # the matter. Its field still contains the number on that layout.
            tabs = page.get_by_role("button", name=re.compile(r"^Exhibits\s*[-–:]\s*\d+", re.I))
            deadline = time.monotonic() + 30
            while not tabs.count() and time.monotonic() < deadline:
                if page.get_by_text(number, exact=True).count():
                    searches = page.get_by_role("button", name="Search", exact=True)
                    if searches.count() >= 2:
                        searches.last.click()
                page.wait_for_timeout(500)
            if not tabs.count():
                raise RuntimeError(f"Matter {number} did not open after search")
            counts = _counts(page)
            title, metadata = _matter_details(page.locator("body").inner_text(), number)
            matter = Matter(number, title, metadata, counts)
            page.get_by_text(re.compile(r"^" + re.escape(kind) + r"\s*[-–:]?\s*\d+", re.I)).first.click()
            paths = []
            # FileMaker renders only the rows in the scrollable viewport.
            buttons = page.get_by_role("button", name=re.compile(r"^GO GET IT$", re.I))
            if counts.get(kind, 0):
                buttons.first.wait_for(state="visible", timeout=30000)
            seen: set[str] = set()
            stalls = 0
            target = min(MAX_FILES, counts.get(kind, 0))
            while len(paths) < target:
                rows = page.get_by_role("row").filter(has=buttons)
                selected = None
                for row in rows.all():
                    match = re.search(r"\b\d{5,7}\b", row.inner_text())
                    if match and match.group() not in seen:
                        selected = (row, match.group())
                        break
                if selected is None:
                    grid = page.locator(".v-grid-tablewrapper")
                    before = grid.evaluate("el => el.scrollTop")
                    grid.evaluate("el => { el.scrollTop += Math.max(250, el.clientHeight * 0.7) }")
                    page.wait_for_timeout(350)
                    after = grid.evaluate("el => el.scrollTop")
                    stalls = stalls + 1 if after == before else 0
                    if stalls >= 2:
                        raise RuntimeError(f"Only {len(paths)} of {target} documents could be found in the scrollable list")
                    continue
                row, doc_number = selected
                row.get_by_role("button", name=re.compile(r"^GO GET IT$", re.I)).click()
                # GO GET IT opens a FileMaker dialog; its filename button starts
                # the actual browser download.
                dialog = page.get_by_role("dialog", name="Download Files")
                dialog.wait_for(state="visible", timeout=30000)
                file_button = dialog.get_by_role("button", name=re.compile(r"\.[A-Za-z0-9]{2,5}$"))
                file_button.first.wait_for(state="visible", timeout=30000)
                with page.expect_download(timeout=60000) as event:
                    file_button.first.click()
                download = event.value
                suffix = Path(download.suggested_filename).suffix or ".bin"
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(download.suggested_filename).stem)[:90]
                path = destination / f"{len(paths) + 1:02d}_{safe_name}{suffix}"
                download.save_as(path)
                if path.stat().st_size == 0:
                    raise RuntimeError(f"Empty document downloaded: {path.name}")
                paths.append(path)
                seen.add(doc_number)
                dialog.get_by_role("button", name="Close").click()
            return matter, paths
        except BrowserTimeout as exc:
            raise RuntimeError("Timed out while navigating the filing database; inspect selectors or site availability") from exc
        finally:
            context.close()
            browser.close()


def package(paths: list[Path], output: Path) -> None:
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.name)


def reply_text(matter: Matter, kind: str, downloaded: int) -> str:
    details = [f"{matter.number}" + (f" concerns {matter.title}" if matter.title else "")]
    details.extend(f"{key}: {value}" for key, value in matter.metadata.items())
    inventory = ", ".join(f"{matter.counts.get(t, 0)} {t}" for t in TYPES)
    return ("Hi,\n\n" + ". ".join(details) + ".\n\n"
            + f"This matter contains {matter.total} files in total ({inventory}). "
            + f"I downloaded {downloaded} of {matter.counts.get(kind, 0)} {kind} files and attached them as a ZIP.\n")


def send_reply(recipient: str, subject: str, body: str, attachment: Path | None = None) -> None:
    message = EmailMessage()
    message["From"] = os.environ.get("MAIL_FROM", os.environ["MAIL_USERNAME"])
    message["To"] = recipient
    message["Subject"] = "Re: " + subject.removeprefix("Re: ")
    message.set_content(body)
    if attachment:
        message.add_attachment(attachment.read_bytes(), maintype="application", subtype="zip", filename=attachment.name)
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465"))) as smtp:
        smtp.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        smtp.send_message(message)


def process(sender: str, subject: str, body: str, *, send: bool = True) -> tuple[str, Path | None]:
    try:
        number, kind = parse_request(subject, body)
    except ValueError as exc:
        response = f"Hi,\n\n{exc}\n"
        if send:
            send_reply(sender, subject, response)
        return response, None
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        try:
            matter, files = fetch_matter(number, kind, directory, os.getenv("MATTER_URL", "https://uarb.novascotia.ca/fmi/webd/UARB15"))
            archive = directory / f"{number}_{kind.lower().replace(' ', '_')}.zip"
            package(files, archive)
            response = reply_text(matter, kind, len(files))
            if send:
                send_reply(sender, subject, response, archive)
                return response, None
            saved = Path.cwd() / archive.name
            shutil.copy2(archive, saved)
            return response, saved
        except Exception:
            LOGGER.exception("Failed to process %s", number)
            response = f"Hi,\n\nI couldn't retrieve {kind} for {number} right now. Please try again later.\n"
            if send:
                send_reply(sender, subject, response)
            return response, None


def _body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        return "\n".join(part.get_content() for part in msg.walk()
                         if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment")
    return msg.get_content() if msg.get_content_type() == "text/plain" else ""


def poll_once() -> int:
    processed = 0
    allowed = {a.strip().casefold() for a in os.getenv("ALLOWED_SENDERS", "").split(",") if a.strip()}
    with imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.getenv("IMAP_PORT", "993"))) as inbox:
        inbox.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        inbox.select("INBOX")
        _, result = inbox.uid("search", None, "UNSEEN")
        for uid in result[0].split():
            status, data = inbox.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
            sender = email.utils.parseaddr(msg["From"] or "")[1]
            if not sender or (allowed and sender.casefold() not in allowed):
                LOGGER.warning("Skipping unapproved sender %r", sender)
                continue
            subject = str(msg["Subject"] or "")
            process(sender, subject, _body(msg))
            inbox.uid("store", uid, "+FLAGS", "(\\Seen)")
            processed += 1
    return processed


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process unread messages once")
    parser.add_argument("--demo", metavar="REQUEST", help="Run one request locally without sending email")
    args = parser.parse_args()
    if args.demo:
        response, path = process("demo@example.com", "Demo request", args.demo, send=False)
        print(response)
        if path:
            print("ZIP:", path)
    elif args.once:
        print(f"Processed {poll_once()} messages")
    else:
        while True:
            try:
                poll_once()
            except Exception:
                LOGGER.exception("Mail polling failed; retrying")
            time.sleep(int(os.getenv("POLL_SECONDS", "30")))


if __name__ == "__main__":
    main()

"""CLS-3 and NFR-PRIV-2: reduce an email to what the model may see.

Subject, sender, date, and the first 2,000 characters of plain text, after
HTML, quoted replies, signatures and footers are removed (OQ-24). The rules
are deliberately simple; the week 6 check (labeled date or role after the
cut) shows whether they cut too much.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

BODY_LIMIT = 2000


@dataclass(frozen=True)
class TrimmedEmail:
    subject: str
    sender: str
    date: str
    body: str


# HTML


_SKIP_TAGS = {"script", "style", "head", "title", "template", "noscript", "blockquote"}
_QUOTE_CLASSES = ("gmail_quote", "yahoo_quoted", "moz-cite-prefix")
_BLOCK_TAGS = {
    "address", "article", "br", "div", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "ol", "p", "section", "table", "td", "th", "tr", "ul",
}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[tuple[str, bool]] = []  # (tag, started a skipped region)
        self._skipping = 0

    def _starts_skip(self, tag: str, attrs: dict) -> bool:
        classes = (attrs.get("class") or "").lower()
        return (
            tag in _SKIP_TAGS
            or any(name in classes for name in _QUOTE_CLASSES)
            or "hidden" in attrs
            or bool(_HIDDEN_STYLE.search(attrs.get("style") or ""))
        )

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS and not self._skipping:
            self.parts.append("\n")
        if tag in _VOID_TAGS:
            return
        skip = self._starts_skip(tag, dict(attrs))
        self._open.append((tag, skip))
        self._skipping += skip

    def handle_endtag(self, tag):
        if tag not in (name for name, _ in self._open):
            return
        while self._open:
            name, skip = self._open.pop()
            self._skipping -= skip
            if name == tag:
                break
        if tag in _BLOCK_TAGS and not self._skipping:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


# Plain text

_INVISIBLE = re.compile(r"[­​-‏ -‮⁠-⁤﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# A line (or two joined lines) that opens a quoted reply. Everything from it on goes.
_REPLY_HEADERS = [
    re.compile(r"^on\b.{0,300}\bwrote:$", re.I),  # Gmail, Apple Mail (English)
    re.compile(r"^.{0,300}\b(şunu yazdı|yazdı):$", re.I),  # Gmail (Turkish)
    re.compile(r"^-{2,}\s*(original message|orijinal ileti|özgün ileti)\s*-{2,}$", re.I),  # Outlook
]
_OUTLOOK_FROM = re.compile(r"^(from|kimden):\s", re.I)
_OUTLOOK_SENT = re.compile(r"^(sent|date|gönderildi|gönderilme tarihi|tarih):\s", re.I)

_SIGNATURE_DELIMITER = re.compile(r"^--\s*$")
_SIGN_OFF = re.compile(
    r"^(best|kind|warm|many thanks and)?\s*(regards|wishes)[,.!]?$|^(best|thanks|thank you|cheers|sincerely)[,.!]?$|"
    r"^(saygılarımla|saygılarımızla|iyi çalışmalar|teşekkürler|sevgiler)[,.!]?$",
    re.I,
)
_SIGNATURE_MAX_LINES = 8  # a sign-off followed by more than this is body text, not a signature
_MOBILE_SIGNATURE = re.compile(r"^(sent from my \w+|get outlook for \w+|iphone'umdan gönderildi)\.?$", re.I)

_FOOTER = re.compile(
    r"unsubscribe|abonelikten çık|aboneliğinizi|manage (your )?(email |e-mail )?(preferences|notification)|"
    r"e-posta tercihleri|you are receiving this|you received this|this (e-?mail|message) (was sent to|is intended)|"
    r"bu (e-?posta|ileti|mesaj).{0,40}(gönderilmiştir|gizli)|view (this email )?in (your )?browser|"
    r"privacy policy|gizlilik politikası|all rights reserved|tüm hakları saklıdır|©",
    re.I,
)
# A footer line in the last part of the text starts the footer; earlier, only that line goes.
_FOOTER_TAIL = 0.6


def _cut_quoted_reply(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        joined = f"{line} {lines[i + 1]}" if i + 1 < len(lines) else line
        if any(p.match(line) or p.match(joined) for p in _REPLY_HEADERS):
            return lines[:i]
        if _OUTLOOK_FROM.match(line) and any(_OUTLOOK_SENT.match(next_line) for next_line in lines[i + 1 : i + 4]):
            return lines[:i]
    return lines


def _cut_signature(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if _SIGNATURE_DELIMITER.match(line):
            return lines[:i]
    for i in range(len(lines) - 1, -1, -1):
        if _SIGN_OFF.match(lines[i]):
            if sum(1 for rest in lines[i + 1 :] if rest) <= _SIGNATURE_MAX_LINES:
                return lines[: i + 1]
            break
    return lines


def _cut_footer(lines: list[str]) -> list[str]:
    total = sum(len(line) + 1 for line in lines)
    kept, position = [], 0
    for line in lines:
        if _FOOTER.search(line):
            if total and position / total >= _FOOTER_TAIL:
                break
            position += len(line) + 1
            continue
        kept.append(line)
        position += len(line) + 1
    return kept


def clean_text(text: str) -> str:
    text = _CONTROL.sub(" ", _INVISIBLE.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    lines = _cut_quoted_reply(lines)
    lines = [line for line in lines if not line.startswith(">") and not _MOBILE_SIGNATURE.match(line)]
    lines = _cut_footer(_cut_signature(lines))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _header(value: str) -> str:
    return " ".join(_CONTROL.sub(" ", _INVISIBLE.sub("", value)).split())


def trim_email(*, subject: str, sender: str, date: datetime, text: str | None = None, html: str | None = None) -> TrimmedEmail:
    """The plain-text part if there is one, else the HTML part as text."""
    body = text if text and text.strip() else html_to_text(html or "")
    return TrimmedEmail(
        subject=_header(subject),
        sender=_header(sender),
        date=date.isoformat(),
        body=clean_text(body)[:BODY_LIMIT].rstrip(),
    )

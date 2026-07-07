"""Extract subject + plain-text body from a parsed email and build SMS text."""
import re
from email.message import Message

import html2text

SMS_MAX_LEN = 160
EMPTY_BODY_PLACEHOLDER = "[empty alert body]"

_h2t = html2text.HTML2Text()
_h2t.ignore_links = True
_h2t.ignore_images = True
_h2t.ignore_emphasis = True
_h2t.body_width = 0


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_body(msg: Message) -> str:
    """Prefer text/plain; fall back to HTML converted to text; else empty."""
    plain, html = None, None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode_part(part)
        elif ctype == "text/html" and html is None:
            html = _decode_part(part)
    if plain and plain.strip():
        return plain
    if html and html.strip():
        return _h2t.handle(html)
    return ""


def build_sms_text(subject: str, body: str) -> str:
    """'<subject>: <body>', whitespace collapsed, truncated to one SMS segment."""
    subject = subject.strip()
    text = f"{subject}: {body}" if subject else body
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = EMPTY_BODY_PLACEHOLDER
    return text[:SMS_MAX_LEN]

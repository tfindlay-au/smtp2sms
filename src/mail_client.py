"""Outbound SMTP relay client (STARTTLS + AUTH). Provider-agnostic:
works with smtp.gmail.com or smtp.office365.com alike.

The From: header is rewritten to the authenticated account (both Gmail and
Exchange reject mismatched senders); the original is kept in X-Original-From.
"""
import asyncio
from email.message import Message

import aiosmtplib


class MailRelayError(Exception):
    pass


class SmtpRelayClient:
    def __init__(self, host: str, port: int, username: str, password: str,
                 timeout: float = 15.0):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout

    def prepare(self, msg: Message, source_ip: str) -> Message:
        original_from = msg.get("From", "")
        if "X-Original-From" in msg:
            del msg["X-Original-From"]
        msg["X-Original-From"] = original_from
        if "From" in msg:
            del msg["From"]
        msg["From"] = self._username
        if "X-smtp2sms-source" in msg:
            del msg["X-smtp2sms-source"]
        msg["X-smtp2sms-source"] = source_ip
        return msg

    async def send(self, msg: Message, recipient: str) -> None:
        """Relay one message inside the overall time budget. Raises MailRelayError."""
        try:
            async with asyncio.timeout(self._timeout):
                await aiosmtplib.send(
                    msg,
                    sender=self._username,
                    recipients=[recipient],
                    hostname=self._host,
                    port=self._port,
                    start_tls=True,
                    username=self._username,
                    password=self._password,
                    timeout=self._timeout,
                )
        except (aiosmtplib.SMTPException, asyncio.TimeoutError, OSError) as e:
            raise MailRelayError(f"relay error: {e!r}") from e

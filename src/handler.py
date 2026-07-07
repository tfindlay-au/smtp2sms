"""aiosmtpd handler: IP whitelist at HELO/EHLO, per-recipient routing at DATA.

Always returns 250 after DATA (F6): downstream failures are logged, never
surfaced to the alerting device.
"""
import logging
from email import message_from_bytes
from email.policy import default as default_policy

from . import extractor, router
from .mail_client import MailRelayError, SmtpRelayClient
from .sms_client import RutSmsClient, SmsSendError

log = logging.getLogger("smtp2sms")

BODY_PREVIEW_LEN = 40


class Smtp2SmsHandler:
    def __init__(self, allowed_ips: frozenset[str],
                 sms_client: RutSmsClient,
                 mail_client: SmtpRelayClient | None):
        self.allowed_ips = allowed_ips
        self.sms_client = sms_client
        self.mail_client = mail_client

    def _check_peer(self, session) -> bool:
        peer_ip = session.peer[0]
        if peer_ip in self.allowed_ips:
            return True
        log.warning({"event": "connection_rejected", "source_ip": peer_ip})
        return False

    async def handle_EHLO(self, server, session, envelope, hostname, responses):
        if not self._check_peer(session):
            return ["550 sender not permitted"]
        session.host_name = hostname
        log.info({"event": "connection_accepted", "source_ip": session.peer[0]})
        return responses

    async def handle_HELO(self, server, session, envelope, hostname):
        if not self._check_peer(session):
            return "550 sender not permitted"
        session.host_name = hostname
        log.info({"event": "connection_accepted", "source_ip": session.peer[0]})
        return "250 {}".format(server.hostname)

    async def handle_DATA(self, server, session, envelope):
        source_ip = session.peer[0]
        sender = envelope.mail_from
        msg = message_from_bytes(envelope.content, policy=default_policy)
        subject = msg.get("Subject", "")
        body = extractor.extract_body(msg)
        log.info({
            "event": "mail_received", "source_ip": source_ip, "sender": sender,
            "rcpt_to": envelope.rcpt_tos, "subject": subject,
            "body_len": len(body), "body_preview": body[:BODY_PREVIEW_LEN],
        })

        for rcpt in envelope.rcpt_tos:
            try:
                kind, dest = router.route(rcpt)
            except ValueError as e:
                log.warning({"event": "rcpt_dropped", "source_ip": source_ip,
                             "sender": sender, "rcpt_to": rcpt, "error": str(e)})
                continue
            log.info({"event": "routing_decision", "rcpt_to": rcpt,
                      "route": kind, "dest": dest})
            if kind == "sms":
                await self._send_sms(subject, body, dest, source_ip, sender, rcpt)
            else:
                await self._send_email(msg, dest, source_ip, sender, rcpt)

        return "250 Message accepted for delivery"

    async def _send_sms(self, subject, body, dest, source_ip, sender, rcpt):
        text = extractor.build_sms_text(subject, body)
        base = {"event": "sms_result", "source_ip": source_ip, "sender": sender,
                "rcpt_to": rcpt, "route": "sms", "dest": dest,
                "subject": subject, "body_len": len(text)}
        try:
            await self.sms_client.send(dest, text)
            log.info({**base, "outcome": "success"})
        except SmsSendError as e:
            log.error({**base, "outcome": "failure", "error": str(e)})

    async def _send_email(self, msg, dest, source_ip, sender, rcpt):
        base = {"event": "email_result", "source_ip": source_ip, "sender": sender,
                "rcpt_to": rcpt, "route": "email", "dest": dest,
                "subject": msg.get("Subject", "")}
        if self.mail_client is None:
            log.warning({**base, "outcome": "skipped",
                         "error": "email relay disabled (no RELAY_USER/RELAY_PASSWORD)"})
            return
        try:
            prepared = self.mail_client.prepare(msg, source_ip)
            await self.mail_client.send(prepared, dest)
            log.info({**base, "outcome": "success"})
        except MailRelayError as e:
            log.error({**base, "outcome": "failure", "error": str(e)})

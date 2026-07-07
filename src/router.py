"""RCPT TO -> ("sms", "+614...") or ("email", "person@example.com")."""
import re

E164_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def route(rcpt_to: str) -> tuple[str, str]:
    """Decide the output path for one envelope recipient.

    Raises ValueError if the recipient is unroutable; callers log and drop
    (the SMTP transaction still succeeds, per fire-and-forget).
    """
    rcpt_to = rcpt_to.strip().strip("<>")
    if "@" not in rcpt_to:
        raise ValueError("no @ in rcpt")
    local, domain = rcpt_to.rsplit("@", 1)
    if not local:
        raise ValueError("empty local-part")
    if E164_RE.match(local):
        return ("sms", local if local.startswith("+") else f"+{local}")
    if domain and "." in domain:
        return ("email", rcpt_to)
    raise ValueError(f"unroutable rcpt: {rcpt_to}")

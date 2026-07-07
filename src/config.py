"""Environment-driven configuration. Fails closed on missing required values."""
import ipaddress
import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    allowed_source_ips: frozenset[str]
    rut_host: str
    rut_username: str
    rut_password: str
    rut_modem: str
    relay_host: str
    relay_port: int
    relay_user: str | None
    relay_password: str | None
    smtp_listen_host: str
    smtp_listen_port: int
    health_listen_port: int
    log_level: str
    sms_timeout: float = 10.0
    relay_timeout: float = 15.0
    max_message_bytes: int = 1_000_000

    @property
    def relay_enabled(self) -> bool:
        return bool(self.relay_user and self.relay_password)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"required env var {name} is missing or empty")
    return value


def _parse_allowed_ips(raw: str) -> frozenset[str]:
    ips = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ips.add(str(ipaddress.ip_address(part)))
        except ValueError:
            raise ConfigError(f"ALLOWED_SOURCE_IPS contains invalid IP: {part!r}")
    if not ips:
        raise ConfigError("ALLOWED_SOURCE_IPS resolved to an empty list")
    return frozenset(ips)


def load_config() -> Config:
    return Config(
        allowed_source_ips=_parse_allowed_ips(_require("ALLOWED_SOURCE_IPS")),
        rut_host=os.environ.get("RUT_HOST", "10.2.10.1"),
        rut_username=_require("RUT_SMS_USERNAME"),
        rut_password=_require("RUT_SMS_PASSWORD"),
        rut_modem=os.environ.get("RUT_MODEM", "1-1.4"),
        relay_host=os.environ.get("RELAY_HOST", "smtp.gmail.com"),
        relay_port=int(os.environ.get("RELAY_PORT", "587")),
        relay_user=os.environ.get("RELAY_USER") or None,
        relay_password=os.environ.get("RELAY_PASSWORD") or None,
        smtp_listen_host=os.environ.get("SMTP_LISTEN_HOST", "0.0.0.0"),
        smtp_listen_port=int(os.environ.get("SMTP_LISTEN_PORT", "25")),
        health_listen_port=int(os.environ.get("HEALTH_LISTEN_PORT", "8080")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )

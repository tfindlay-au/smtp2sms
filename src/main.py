"""Entrypoint: SMTP listener + health endpoint."""
import asyncio
import sys

from aiohttp import web
from aiosmtpd.smtp import SMTP

from .config import ConfigError, load_config
from .handler import Smtp2SmsHandler
from .logging_setup import setup_logging
from .mail_client import SmtpRelayClient
from .sms_client import RutSmsClient


async def run() -> None:
    try:
        cfg = load_config()
    except ConfigError as e:
        # Fail closed before logging is configured; make the reason obvious.
        print(f'{{"level": "ERROR", "event": "config_error", "error": "{e}"}}',
              file=sys.stderr)
        raise SystemExit(1)

    log = setup_logging(cfg.log_level)

    sms_client = RutSmsClient(cfg.rut_host, cfg.rut_username, cfg.rut_password,
                              cfg.rut_modem, cfg.sms_timeout)
    mail_client = (SmtpRelayClient(cfg.relay_host, cfg.relay_port,
                                   cfg.relay_user, cfg.relay_password,
                                   cfg.relay_timeout)
                   if cfg.relay_enabled else None)

    handler = Smtp2SmsHandler(cfg.allowed_source_ips, sms_client, mail_client)
    loop = asyncio.get_running_loop()
    smtp_server = await loop.create_server(
        lambda: SMTP(handler, data_size_limit=cfg.max_message_bytes,
                     enable_SMTPUTF8=True),
        host=cfg.smtp_listen_host,
        port=cfg.smtp_listen_port,
    )

    async def health(_request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.smtp_listen_host, cfg.health_listen_port)
    await site.start()

    log.info({"event": "startup",
              "msg": f"SMTP listener ready on {cfg.smtp_listen_host}:{cfg.smtp_listen_port}",
              "allowed_source_ips": sorted(cfg.allowed_source_ips),
              "relay_enabled": cfg.relay_enabled,
              "rut_host": cfg.rut_host, "rut_modem": cfg.rut_modem})

    try:
        await asyncio.Event().wait()
    finally:
        smtp_server.close()
        await sms_client.aclose()
        await runner.cleanup()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

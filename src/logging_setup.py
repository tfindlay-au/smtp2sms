"""Single-line JSON logging to stdout. Log records may carry a dict payload."""
import datetime
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
        }
        if isinstance(record.msg, dict):
            entry.update(record.msg)
        else:
            entry["event"] = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            entry.setdefault("error", repr(record.exc_info[1]))
        return json.dumps(entry, default=str)


def setup_logging(level: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    # aiosmtpd/asyncio internals are noisy at DEBUG; keep them at WARNING
    for name in ("mail.log", "aiosmtpd", "asyncio", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return logging.getLogger("smtp2sms")

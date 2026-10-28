import logging
import re

_PATH = re.compile(r"(/(?:reset-password|confirm|unsubscribe)/)[A-Za-z0-9_\-]+")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_QUOTED = re.compile(
    r"(?i)([\"'](?:password|new_password|current_password|token|reset_token|"
    r"csrf_token|smtp_password|authorization|secret)[\"']\s*:\s*[\"'])(.*?)([\"'])"
)
_ASSIGN = re.compile(
    r"(?i)((?:password|new_password|current_password|token|reset_token|"
    r"csrf_token|smtp_password|authorization|secret)\s*[:=]\s*)(\S+)"
)


def redact(text: str) -> str:
    text = _PATH.sub(r"\1[redacted]", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _QUOTED.sub(r"\1[redacted]\3", text)
    text = _ASSIGN.sub(r"\1[redacted]", text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # Uvicorn's access formatter reads the original argument tuple. Redact
        # those values in place instead of collapsing the record.
        if isinstance(args, tuple) and args and all(not isinstance(item, (dict, tuple, list)) for item in args):
            record.args = tuple(redact(item) if isinstance(item, str) else item for item in args)
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            return True
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact(rendered)
        record.args = ()
        return True


def install_redaction() -> None:
    filt = RedactFilter()
    loggers = [logging.getLogger()]
    loggers.extend(logging.getLogger(name) for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "adp"))
    for logger in loggers:
        if not any(isinstance(item, RedactFilter) for item in logger.filters):
            logger.addFilter(filt)

"""Logging hygiene: keep credentials and customer data out of log files.

The rule that matters operationally: a web-server access log records the full
request line, query string included. The Drive OAuth callback puts a bearer
credential in the URL (`/gdrive/callback?code=...&state=...`), so the uvicorn
loggers get a filter that rewrites credential-shaped parameters to
`[REDACTED]` before the line is ever written.
"""

import logging
import re

# Query parameters that must never appear in a log line. `code` and `state`
# are the Drive OAuth callback; the rest are defensive, for any future flow
# that puts a token in a URL.
_SENSITIVE_PARAMS = re.compile(
    r"([?&](?:code|state|token|id_token|access_token|refresh_token|api_key|key)=)"
    r"[^&\s\"']+",
    re.IGNORECASE,
)

_REDACTED = r"\1[REDACTED]"


def _redact(text: str) -> str:
    return _SENSITIVE_PARAMS.sub(_REDACTED, text)


class RedactQueryParamsFilter(logging.Filter):
    """Rewrite credential-shaped query parameters in a log record.

    Uvicorn's access formatter builds its line from `record.args` (client,
    method, path-with-query, HTTP version, status), so the substitution runs
    over the message template and every string argument.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: _redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    _redact(value) if isinstance(value, str) else value
                    for value in record.args
                )
        return True


def install_access_log_redaction() -> None:
    """Attach the redaction filter to the web-server loggers.

    Called at import time in app.main, before the server starts, so every
    access-log line passes through it regardless of which handler uvicorn
    configured. `uvicorn.error` is covered too: request lines can appear in
    its error messages.
    """
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactQueryParamsFilter) for f in logger.filters):
            logger.addFilter(RedactQueryParamsFilter())

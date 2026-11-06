"""Per-request context shared with non-request code (e.g. the SQL slow-query
logger in database.py), so log lines can say which endpoint ran a query."""
from contextvars import ContextVar

current_request_path: ContextVar[str] = ContextVar("current_request_path", default="-")

import logging
import time

import pyodbc
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import config
from request_context import current_request_path

logger = logging.getLogger("sql.slow")

evo_connection_string = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={config.SERVER};"
    f"DATABASE={config.COMPANY_DATABASE};"
    f"UID={config.DB_USERNAME};"
    f"PWD={config.DB_PASSWORD};"
    f"TrustServerCertificate=yes;"
    f"Connection Timeout=5;"
)


def _connect():
    conn = pyodbc.connect(evo_connection_string)
    # Safety net: SQL Server cancels any single statement that runs longer
    # than this. Without it, a bad query can run for minutes and pile up
    # behind the next request (2026-11-06: 145 s queries pinned the CPU).
    # The request fails loudly instead of the whole server going slow.
    conn.timeout = config.DB_QUERY_TIMEOUT_SECONDS
    return conn


engine = create_engine(
    "mssql+pyodbc://",
    creator=_connect,
    echo=False,
)


# ---- Slow-query log ---------------------------------------------------------
# Every statement slower than DB_SLOW_QUERY_SECONDS is written to the backend
# log (IIS stdout log: backend\logs\stdout_*.log) with the API path that ran
# it, so an expensive query shows up the first day instead of via IT.

@event.listens_for(engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    starts = conn.info.get("query_start")
    if not starts:
        return
    elapsed = time.perf_counter() - starts.pop()
    if elapsed >= config.DB_SLOW_QUERY_SECONDS:
        logger.warning(
            "SLOW SQL %.2fs on %s :: %s",
            elapsed,
            current_request_path.get(),
            " ".join(statement.split())[:300],
        )


@event.listens_for(engine, "handle_error")
def _handle_error(exception_context):
    # Keep the start-time stack balanced when a statement fails / times out.
    conn = exception_context.connection
    if conn is not None and conn.info.get("query_start"):
        conn.info["query_start"].pop()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

"""Static guards against the patterns that pinned SQL Server on 2026-11-06.

These tests don't touch the database — they scan the source. A failure means
"a risky pattern was added; review it against PERFORMANCE.md". If the new
code is genuinely fine, add it to the allowlist below WITH a comment saying
why (expected cost, how often it runs).

Run:  cd backend && python -m pytest tests/test_performance_guards.py -q
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND_SRC = ROOT / "frontend" / "src"


def _backend_py_files():
    for p in BACKEND.rglob("*.py"):
        parts = set(p.parts)
        if {"tests", "venv", ".venv", "__pycache__", "scripts"} & parts:
            continue
        yield p


def _frontend_files():
    for ext in ("*.ts", "*.tsx"):
        yield from FRONTEND_SRC.rglob(ext)


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


# --- Backend SQL -------------------------------------------------------------

# `JOIN ... ON (a = x OR b = x)` — the exact shape of the incident. An OR in
# a join condition stops SQL Server using the index, so it scans the table
# for every outer row. Split into UNION ALL branches instead.
OR_IN_JOIN = re.compile(r"\bON\s*\((?:[^()]|\([^()]*\))*?\bOR\b", re.IGNORECASE | re.DOTALL)

# `CAST(t.SomeDate AS DATE) >= / <= / = / BETWEEN` in a filter. Use a plain
# half-open range instead: `col >= :from AND col < :to_plus_one_day`.
CAST_DATE_FILTER = re.compile(
    r"CAST\(\s*\w+\.[\w{}]+\s+AS\s+DATE\s*\)\s*(?:>=|<=|=|<|>|BETWEEN)", re.IGNORECASE
)


def test_no_or_inside_join_conditions():
    hits = []
    for p in _backend_py_files():
        for m in OR_IN_JOIN.finditer(p.read_text(encoding="utf-8")):
            line = p.read_text(encoding="utf-8")[: m.start()].count("\n") + 1
            hits.append(f"{_rel(p)}:{line}")
    assert not hits, "OR inside JOIN ... ON (...) — split into UNION ALL (PERFORMANCE.md):\n" + "\n".join(hits)


def test_no_cast_on_date_columns_in_filters():
    hits = []
    for p in _backend_py_files():
        text = p.read_text(encoding="utf-8")
        for m in CAST_DATE_FILTER.finditer(text):
            hits.append(f"{_rel(p)}:{text[: m.start()].count(chr(10)) + 1}")
    assert not hits, "CAST(col AS DATE) in a filter — use col >= :from AND col < :to+1 (PERFORMANCE.md):\n" + "\n".join(hits)


# --- Frontend timers ---------------------------------------------------------

# Every repeating timer that can reach the backend, with its reason. A NEW
# setInterval anywhere else fails the test until it's reviewed and listed.
ALLOWED_INTERVAL_FILES = {
    # Fetches the static /version.json only, every 2 min — no DB.
    "frontend/src/utils/autoReload.ts",
    # Customer credit/terms status, every 5 s while visible. ~20 ms query on
    # a single customer row — reviewed 2026-11-06.
    "frontend/src/utils/useCustomerAutoRefresh.ts",
    # Performance strip safety-net poll, every 10 min + single in-flight.
    "frontend/src/components/PerformanceStrip.tsx",
    # Pending import-review badge, supervisors / admins only: COUNT on the
    # indexed status column of the tiny _tb_SOImportReview table, every 2 min
    # while visible + single in-flight — reviewed 2026-11-10.
    "frontend/src/utils/usePendingReviewCount.ts",
}

# Raw window "focus" listeners. New code must use useFocusRefresh (throttled).
ALLOWED_FOCUS_LISTENER_FILES = {
    "frontend/src/utils/useFocusRefresh.ts",
    "frontend/src/utils/autoReload.ts",
    # Single-customer credit status — cheap, and the rep expects an instant
    # update after alt-tabbing back from unblocking a customer in Evolution.
    "frontend/src/utils/useCustomerAutoRefresh.ts",
}

MIN_POLL_MS = 60_000


def test_no_unreviewed_set_interval():
    hits = [
        _rel(p) for p in _frontend_files()
        if "setInterval(" in p.read_text(encoding="utf-8")
        and _rel(p) not in ALLOWED_INTERVAL_FILES
    ]
    assert not hits, (
        "New setInterval — check the query cost x every open session x frequency, "
        "then add to ALLOWED_INTERVAL_FILES with a reason (PERFORMANCE.md):\n" + "\n".join(hits)
    )


def test_no_raw_focus_listeners():
    pat = re.compile(r"addEventListener\(\s*[\"']focus[\"']")
    hits = [
        _rel(p) for p in _frontend_files()
        if pat.search(p.read_text(encoding="utf-8"))
        and _rel(p) not in ALLOWED_FOCUS_LISTENER_FILES
    ]
    assert not hits, "Raw window focus listener — use useFocusRefresh (PERFORMANCE.md):\n" + "\n".join(hits)


def test_poll_constants_are_not_aggressive():
    """Any *_POLL_MS / POLL_INTERVAL_MS constant below 60 s must be allowlisted."""
    pat = re.compile(r"(\w*POLL\w*_MS)\s*=\s*([\d_]+(?:\s*\*\s*[\d_]+)*)")
    too_fast = []
    for p in _frontend_files():
        for name, expr in pat.findall(p.read_text(encoding="utf-8")):
            value = eval(expr.replace("_", ""), {"__builtins__": {}})
            if value < MIN_POLL_MS and _rel(p) != "frontend/src/utils/useCustomerAutoRefresh.ts":
                too_fast.append(f"{_rel(p)}: {name} = {value} ms")
    assert not too_fast, "Poll faster than 60 s (PERFORMANCE.md):\n" + "\n".join(too_fast)

"""Week math -- ISO-8601 weeks, Monday to Sunday.

Decided 2026-11-06 (replaces the original Sunday-aligned convention):
a Mon-Sat delivery week sits inside one week, and week numbers match
Excel ISOWEEKNUM / SQL DATEPART(ISO_WEEK). Every dWeekStart DATE in the
forecasting schema is a Monday.

Every week calculation in the app goes through `week_start` (Python) or
`WEEK_START_SQL` (T-SQL) so the convention lives in exactly one place.
"""

from datetime import date, timedelta


# T-SQL week-start expression for a date column. @@DATEFIRST-invariant:
# 1900-01-01 was a Monday, so (DATEDIFF days mod 7) = days since Monday.
# The +7 %7 keeps it non-negative for dates before 1900 (defensive).
def week_start_sql(col: str) -> str:
    return (f"CAST(DATEADD(day, -((DATEDIFF(day, '19000101', {col}) % 7 + 7) % 7), "
            f"CAST({col} AS date)) AS date)")


def week_start(d: date) -> date:
    """Return the Monday on or before `d`.

        >>> week_start(date(2026, 11, 9))   # Monday
        datetime.date(2026, 11, 9)
        >>> week_start(date(2026, 11, 15))  # Sunday -> same week's Monday
        datetime.date(2026, 11, 9)
        >>> week_start(date(2026, 11, 12))  # Thursday
        datetime.date(2026, 11, 9)
    """
    return d - timedelta(days=d.weekday())   # weekday(): Mon=0 .. Sun=6


def week_end(d: date) -> date:
    """Return the Sunday that closes `d`'s week.

        >>> week_end(date(2026, 11, 10))    # Tuesday -> Sunday
        datetime.date(2026, 11, 15)
    """
    return week_start(d) + timedelta(days=6)


def iso_week_number(d: date) -> int:
    """ISO week number (1-53) of `d`'s week -- what users see as 'Wk 46'."""
    return d.isocalendar()[1]


def weeks_between(start: date, end: date) -> list[date]:
    """Return every week start (Monday) between `start` and `end` inclusive.

    Both endpoints are normalised to their week first.

        >>> weeks_between(date(2026, 10, 21), date(2026, 11, 4))
        [datetime.date(2026, 10, 19), datetime.date(2026, 10, 26), datetime.date(2026, 11, 2)]
    """
    cur = week_start(start)
    stop = week_start(end)
    out: list[date] = []
    while cur <= stop:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def previous_completed_weeks(as_of_week: date, n: int) -> list[date]:
    """Return the `n` weeks strictly before `as_of_week`, oldest first.

        >>> previous_completed_weeks(date(2026, 11, 2), 3)
        [datetime.date(2026, 10, 12), datetime.date(2026, 10, 19), datetime.date(2026, 10, 26)]
    """
    anchor = week_start(as_of_week)
    return [anchor - timedelta(days=7 * (n - i)) for i in range(n)]

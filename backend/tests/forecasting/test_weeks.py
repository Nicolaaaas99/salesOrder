"""Unit tests for weeks.py -- ISO weeks, Monday to Sunday."""
from datetime import date, timedelta

from services.forecasting import weeks


class TestWeekStart:
    def test_monday_returns_itself(self):
        assert weeks.week_start(date(2026, 11, 9)) == date(2026, 11, 9)

    def test_sunday_belongs_to_the_week_that_started_monday(self):
        assert weeks.week_start(date(2026, 11, 15)) == date(2026, 11, 9)

    def test_saturday_rolls_back_to_monday(self):
        assert weeks.week_start(date(2026, 11, 14)) == date(2026, 11, 9)

    def test_always_a_monday(self):
        d = date(2026, 1, 1)
        for i in range(400):
            assert weeks.week_start(d + timedelta(days=i)).weekday() == 0

    def test_matches_python_isocalendar(self):
        """Same week as Python's ISO calendar for every day of two years."""
        d = date(2026, 1, 1)
        for i in range(730):
            day = d + timedelta(days=i)
            y, w, _ = day.isocalendar()
            assert weeks.week_start(day) == date.fromisocalendar(y, w, 1)


class TestWeekEnd:
    def test_week_end_is_the_sunday(self):
        assert weeks.week_end(date(2026, 11, 10)) == date(2026, 11, 15)


class TestIsoWeekNumber:
    def test_known_weeks(self):
        assert weeks.iso_week_number(date(2026, 11, 9)) == 46
        assert weeks.iso_week_number(date(2026, 1, 1)) == 1
        assert weeks.iso_week_number(date(2027, 1, 1)) == 53   # Fri, still 2026-W53


class TestWeeksBetween:
    def test_three_consecutive_weeks(self):
        got = weeks.weeks_between(date(2026, 10, 21), date(2026, 11, 4))
        assert got == [date(2026, 10, 19), date(2026, 10, 26), date(2026, 11, 2)]

    def test_same_week_returns_one(self):
        got = weeks.weeks_between(date(2026, 11, 2), date(2026, 11, 8))
        assert got == [date(2026, 11, 2)]


class TestPreviousCompletedWeeks:
    def test_six_weeks_before(self):
        got = weeks.previous_completed_weeks(date(2026, 10, 19), 6)
        assert got == [
            date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21),
            date(2026, 9, 28), date(2026, 10, 5), date(2026, 10, 12),
        ]

    def test_result_is_oldest_first(self):
        got = weeks.previous_completed_weeks(date(2026, 11, 9), 3)
        assert got[0] < got[1] < got[2]


class TestWeekStartSql:
    def test_anchor_is_a_monday(self):
        """The SQL expression counts days from 1900-01-01; that must be a
        Monday or every SQL-bucketed week drifts from the Python one."""
        assert date(1900, 1, 1).weekday() == 0
        assert "'19000101'" in weeks.week_start_sql("TxDate")

    def test_sql_formula_matches_python(self):
        """Replay the T-SQL arithmetic in Python for a range of dates."""
        base = date(1900, 1, 1)
        d = date(2025, 12, 1)
        for i in range(120):
            day = d + timedelta(days=i)
            days_since_monday = ((day - base).days % 7 + 7) % 7
            assert day - timedelta(days=days_since_monday) == weeks.week_start(day)

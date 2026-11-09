"""Answer checker for FORECASTING_TUTORIAL.md.

Do a lesson's exercise on paper, then run this to see the REAL code's
answer next to the answer printed in the tutorial:

    cd c:\\Dev\\salesOrder\\backend
    .\\.venv\\Scripts\\python.exe scripts\\tutorial_check.py        # every lesson
    .\\.venv\\Scripts\\python.exe scripts\\tutorial_check.py 2      # just lesson 2

No database is used. "OK" means the code agrees with the tutorial.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.forecasting import classifier, weeks  # noqa: E402
from services.forecasting.engine import run_model  # noqa: E402
from services.forecasting.fallback import own_rate_fallback  # noqa: E402
from services.forecasting.reasonableness import (  # noqa: E402
    check_above_historical_p95, check_below_pack_size_minimum, check_new_product, percentile,
)
from services.forecasting.scenarios import ForecastLeaf, apply_effects, compute_lever_effects  # noqa: E402
from services.forecasting.suggestions import PresentedSuggestion, classify_decisions, remaining_week_qty  # noqa: E402
from services.forecasting.workbench import WorkbenchLeaf, rollup  # noqa: E402

W0 = date(2026, 11, 2)                  # "this week" (Monday)
W1 = W0 + timedelta(weeks=1)            # next week
results: list[bool] = []


def show(label, tutorial_answer, code_answer):
    ok = tutorial_answer == code_answer
    results.append(ok)
    print(f"  {label:<52} tutorial: {str(tutorial_answer):<14} code: {str(code_answer):<14} {'OK' if ok else '<-- DIFFERENT'}")


def last_weeks(values):
    """values = the last N completed weeks, oldest first."""
    n = len(values)
    return {W0 - timedelta(weeks=n - i): float(v) for i, v in enumerate(values) if v}


def base(values):
    return run_model("BASE_MEDIAN_6W", last_weeks(values), W1, W0)


def lesson1():
    print("\nLesson 1 - weeks run Monday to Sunday")
    for d, ans in ((date(2026, 11, 4), date(2026, 11, 2)), (date(2026, 11, 7), date(2026, 11, 2)),
                   (date(2026, 11, 8), date(2026, 11, 2)), (date(2026, 11, 9), date(2026, 11, 9))):
        show(f"week of {d:%a %d %b}", ans, weeks.week_start(d))


def lesson2():
    print("\nLesson 2 - the usual weekly quantity (6-week median)")
    show("example [10, 0, 12, 8, 0, 10]", 10.0, base([10, 0, 12, 8, 0, 10]))
    show("example [0, 0, 5, 0, 0, 7]  (too few weeks)", None, base([0, 0, 5, 0, 0, 7]))
    show("exercise a [20, 22, 0, 18, 21, 0]", 21.0, base([20, 22, 0, 18, 21, 0]))
    show("exercise b [6, 9, 0, 3, 0, 0]", 6.0, base([6, 9, 0, 3, 0, 0]))


def own_rate(values):
    start = W0 - timedelta(weeks=len(values))           # data starts with the first value
    fb = own_rate_fallback(last_weeks(values), W0, 52, start)
    return fb.value if fb else None


def lesson3():
    print("\nLesson 3 - not enough recent sales: the customer's own weekly rate")
    show("example: 30 twice in 12 weeks", 5.0, own_rate([30, 0, 0, 0, 0, 0, 30, 0, 0, 0, 0, 0]))
    show("exercise: 24 three times in 12 weeks", 6.0, own_rate([24, 0, 0, 0, 24, 0, 0, 0, 24, 0, 0, 0]))


def lesson4():
    print("\nLesson 4 - (optional) the pattern label and grade")
    def label(values):
        start = W0 - timedelta(weeks=len(values))
        c = classifier.classify(last_weeks(values), W0, history_start=start)
        return c.pattern, round(c.adi, 2)
    show("example: 10 every other week for 12 weeks", ("INTERMITTENT", 2.0), label([10, 0] * 6))
    show("exercise: 10 in 11 of 12 weeks", ("SMOOTH", 1.09), label([10] * 5 + [0] + [10] * 6))
    show("grade SMOOTH, own model", "A", classifier.confidence_grade("SMOOTH", False))
    show("grade INTERMITTENT, own model", "C", classifier.confidence_grade("INTERMITTENT", False))
    show("grade INTERMITTENT, fallback", "D", classifier.confidence_grade("INTERMITTENT", True))


def lever(kind, value, forecasts):
    leaves = [ForecastLeaf(f"C{i}", "P", W1, i, float(f)) for i, f in enumerate(forecasts)]
    eff, _ = compute_lever_effects(1, kind, value, leaves)
    return [apply_effects(l.forecast_qty, [eff[l.key]]) for l in leaves]


def lesson5():
    print("\nLesson 5 - a % scenario")
    show("example: forecast 10, +20%", [12.0], lever("PERCENT", 20, [10]))
    show("exercise a: forecast 25, +10%", [27.5], lever("PERCENT", 10, [25]))
    show("exercise b: forecast 40, -25%", [30.0], lever("PERCENT", -25, [40]))


def lesson6():
    print("\nLesson 6 - spreading an amount across customers")
    show("example: 30 & 10, add 20 per week", [45.0, 15.0], lever("ABSOLUTE_DELTA", 20, [30, 10]))
    show("example: 30 & 10, weekly total 60", [45.0, 15.0], lever("FIXED_QTY", 60, [30, 10]))
    show("exercise a: 20, 20, 10, add 10", [24.0, 24.0, 12.0], lever("ABSOLUTE_DELTA", 10, [20, 20, 10]))
    show("exercise b: 20, 20, 10, weekly total 100", [40.0, 40.0, 20.0], lever("FIXED_QTY", 100, [20, 20, 10]))


def flagged_p95(target, sales):
    h = last_weeks(sales)
    return check_above_historical_p95(target, h, W0) is not None


def lesson7():
    print("\nLesson 7 - warning flags")
    show("example: usual max of [10,10,10,10,10]", 10.0, percentile([10.0] * 5, 0.95))
    show("  target 12 flagged?", True, flagged_p95(12, [10] * 5))
    show("example: usual max of [8, 10, 12]", 11.8, round(percentile([8.0, 10.0, 12.0], 0.95), 4))
    show("  target 11 flagged?", False, flagged_p95(11, [8, 10, 12]))
    show("  target 12 flagged?", True, flagged_p95(12, [8, 10, 12]))
    show("exercise a: usual max of [5, 5, 10]", 9.5, round(percentile([5.0, 5.0, 10.0], 0.95), 4))
    show("  target 9 flagged?", False, flagged_p95(9, [5, 5, 10]))
    show("  target 10 flagged?", True, flagged_p95(10, [5, 5, 10]))
    show("exercise b: pack 6, target 4 flagged?", True, check_below_pack_size_minimum(4, 6) is not None)
    show("exercise c: pack 6, target 0 flagged?", False, check_below_pack_size_minimum(0, 6) is not None)
    show("exercise d: first invoice 5 weeks ago, changed", True,
         check_new_product(W0 - timedelta(weeks=5), W0, 12, 10, date(2026, 8, 1)) is not None)
    show("exercise e: first invoice 20 weeks ago, changed", False,
         check_new_product(W0 - timedelta(weeks=20), W0, 12, 10, date(2026, 1, 1)) is not None)


def lesson8():
    print("\nLesson 8 - the quantity the rep is offered")
    show("example: target 12, ordered 0, pack 10", 20.0, remaining_week_qty(12, 0, 10, round_up=True))
    show("example: no target, usual 12, pack 10", 10.0, remaining_week_qty(12, 0, 10, round_up=False))
    show("exercise a: target 30, ordered 12, pack 6", 18.0, remaining_week_qty(30, 12, 6, round_up=True))
    show("exercise b: target 31, ordered 12, pack 6", 24.0, remaining_week_qty(31, 12, 6, round_up=True))
    show("exercise c: no target, usual 31, ordered 12, pack 6", 18.0, remaining_week_qty(31, 12, 6, round_up=False))
    show("exercise d: target 30, ordered 36, pack 6", 0.0, remaining_week_qty(30, 36, 6, round_up=True))


def lesson9():
    print("\nLesson 9 - what is recorded when the order is placed")
    shown = [PresentedSuggestion(1, "A", 20), PresentedSuggestion(2, "B", 10), PresentedSuggestion(3, "C", 6),
             PresentedSuggestion(4, "E", 18)]
    d, manual = classify_decisions(shown, {"A": 20, "B": 5, "D": 12, "E": 24})
    got = {x.id: x.decision for x in d}
    show("example: shown 20, ordered 20", "ACCEPTED", got[1])
    show("example: shown 10, ordered 5", "MODIFIED", got[2])
    show("example: shown 6, not ordered", "REJECTED", got[3])
    show("example: not shown, ordered 12", {"D": 12.0}, manual)
    show("exercise: shown 18, ordered 24", "MODIFIED", got[4])


def lesson10():
    print("\nLesson 10 - the Workbench totals")
    leaves = [WorkbenchLeaf("X", "A", "DC", "R", "Juice", forecast=10, target_id=1, target=12, source="SCENARIO",
                            delivered=0, on_order=20),
              WorkbenchLeaf("X", "B", "DC", "R", "Juice", forecast=30, delivered=15)]
    total, _ = rollup(leaves, "dist_centre", {})
    show("forecast total", 40.0, total.forecast)
    show("target total", 42.0, total.target)
    show("committed total", 35.0, total.committed)
    show("committed % of target (rounded)", 83, round(total.committed / total.target * 100))


LESSONS = [lesson1, lesson2, lesson3, lesson4, lesson5, lesson6, lesson7, lesson8, lesson9, lesson10]

if __name__ == "__main__":
    pick = [int(a) for a in sys.argv[1:]] or range(1, len(LESSONS) + 1)
    for n in pick:
        LESSONS[n - 1]()
    print(f"\n{sum(results)} of {len(results)} answers agree with the tutorial.")
    sys.exit(0 if all(results) else 1)

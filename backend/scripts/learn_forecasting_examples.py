"""Worked examples for FORECASTING_GUIDE.md -- computed by the REAL code.

Every example in the guide is reproduced here with the same inputs. Work
the example by hand first, then run this to check your answer:

    cd c:\\Dev\\salesOrder\\backend
    .\\.venv\\Scripts\\python.exe scripts\\learn_forecasting_examples.py          # all
    .\\.venv\\Scripts\\python.exe scripts\\learn_forecasting_examples.py 3        # just example 3

No database is touched. Each example ends with a CHECK line comparing the
hand answer printed in the guide with the code's answer.
"""

from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.forecasting import classifier, weeks  # noqa: E402
from services.forecasting.engine import fallback_owner, run_model  # noqa: E402
from services.forecasting.fallback import magnitude_from_aggregates, own_rate_fallback  # noqa: E402
from services.forecasting.models import base_median, seasonal_naive_yoy  # noqa: E402
from services.forecasting import delivery_pattern  # noqa: E402
from services.forecasting.reasonableness import evaluate_flags, percentile  # noqa: E402
from services.forecasting.resolver import GoalSpec, resolve_week  # noqa: E402
from services.forecasting.scenarios import (  # noqa: E402
    FlagInputs, ForecastLeaf, ScenarioScope, apply_effects, build_cascade, compute_lever_effects,
)
from services.forecasting.suggestions import (  # noqa: E402
    PresentedSuggestion, classify_decisions, remaining_week_qty,
)
from services.forecasting.workbench import WorkbenchLeaf, rollup  # noqa: E402

# ---------------------------------------------------------------------------
# Shared calendar (matches the guide)
# ---------------------------------------------------------------------------
AS_OF = date(2026, 11, 3)            # "today", a Tuesday
W0 = weeks.week_start(AS_OF)         # 2026-11-02, this week's Monday
W1 = W0 + timedelta(weeks=1)         # 2026-11-09, next week (the forecast week)
HISTORY_START = date(2026, 8, 1)     # first invoice in PostST (a Saturday)


def wk(n: int) -> date:
    """W-n: the Monday n weeks before this week."""
    return W0 - timedelta(weeks=n)


def history(values_oldest_first: list[float]) -> dict[date, float]:
    """Values for W-13 .. W-1 (13 weeks), oldest first."""
    n = len(values_oldest_first)
    return {wk(n - i): v for i, v in enumerate(values_oldest_first) if v}


# The two customers used throughout the guide (product: Milk 2L, pack of 6)
STEADY = [40, 38, 42, 40, 36, 40, 0, 44, 40, 38, 42, 41, 40]          # buys most weeks
OCCASIONAL = [60, 0, 0, 55, 0, 0, 0, 65, 0, 0, 60, 0, 0]               # buys every few weeks

results: list[tuple[str, bool]] = []


def check(label: str, hand, code, tol=1e-6) -> None:
    if isinstance(hand, float) or isinstance(code, float):
        ok = code is not None and abs(float(hand) - float(code)) <= tol
    else:
        ok = hand == code
    results.append((label, ok))
    print(f"   CHECK {label}: hand = {hand}   code = {code}   {'OK' if ok else '*** MISMATCH ***'}")


def title(n: int, text: str) -> None:
    print(f"\n{'=' * 72}\nExample {n}: {text}\n{'=' * 72}")


# ---------------------------------------------------------------------------
def ex1():
    title(1, "Weeks run Monday to Sunday (ISO)")
    print(f"   today {AS_OF} ({AS_OF:%A}) -> this week starts {W0} ({W0:%A})")
    print(f"   next week W+1 = {W1};  W-1 = {wk(1)};  W-13 = {wk(13)}")
    first_full = classifier.observation_window(AS_OF, 52, HISTORY_START)[0]
    print(f"   invoices start {HISTORY_START} ({HISTORY_START:%A}) -> first FULL week {first_full}")
    check("week of Tue 2026-11-03", date(2026, 11, 2), weeks.week_start(AS_OF))
    check("first full week of data", date(2026, 8, 3), first_full)
    check("weeks of data in the 52-week window", 13,
          len(classifier.observation_window(AS_OF, 52, HISTORY_START)))


def ex2():
    title(2, "CV-squared by hand (size variability)")
    for label, xs, hand in (("even sizes [10, 30, 10, 30]", [10, 30, 10, 30], 0.25),
                            ("uneven sizes [5, 40, 5, 30]", [5, 40, 5, 30], 0.59375)):
        mu = sum(xs) / len(xs)
        var = sum((x - mu) ** 2 for x in xs) / len(xs)
        print(f"   {label}: mean {mu}, variance {var}, CV2 = {var}/{mu}^2 = {var / mu ** 2}")
        check(f"CV2 {label}", hand, var / mu ** 2)


def ex3():
    title(3, "Classify the STEADY customer")
    h = history(STEADY)
    c = classifier.classify(h, AS_OF, history_start=HISTORY_START)
    print(f"   13 weeks observed, {c.non_zero_weeks} with sales -> ADI = 13/{c.non_zero_weeks} = {c.adi:.3f}")
    print(f"   CV2 = {c.cv_squared:.4f}   pattern {c.pattern}   recommended {c.recommended_model}"
          f"   fallback_reason {c.fallback_reason}")
    check("ADI", round(13 / 12, 4), round(c.adi, 4))
    check("pattern", "SMOOTH", c.pattern)
    check("CV2 (4 dp)", 0.0025, round(c.cv_squared, 4), tol=5e-5)


def ex4():
    title(4, "Classify the OCCASIONAL customer")
    h = history(OCCASIONAL)
    c = classifier.classify(h, AS_OF, history_start=HISTORY_START)
    print(f"   ADI = 13/{c.non_zero_weeks} = {c.adi}   CV2 = {c.cv_squared:.4f}   pattern {c.pattern}")
    print(f"   recommended {c.recommended_model} (not enabled) -> fallback owner "
          f"{fallback_owner({'BASE_MEDIAN_6W': 1}, c)}")
    unclipped = classifier.classify(h, AS_OF)
    print(f"   (old bug: counting all 52 weeks gives ADI {unclipped.adi})")
    check("ADI", 3.25, c.adi)
    check("pattern", "INTERMITTENT", c.pattern)
    check("fallback reason", "SPARSE_HISTORY", c.fallback_reason)


def ex5():
    title(5, "Base model (6-week median) for the STEADY customer")
    h = history(STEADY)
    last6 = [h.get(wk(n), 0) for n in range(6, 0, -1)]
    nz = sorted(x for x in last6 if x > 0)
    print(f"   last 6 weeks W-6..W-1: {last6}  -> non-zero sorted {nz}")
    print(f"   median = ({nz[2]} + {nz[3]}) / 2 = {median(nz)} -> half-up -> {int(median(nz) + 0.5)}")
    got = [run_model("BASE_MEDIAN_6W", h, W1 + timedelta(weeks=k), W0) for k in range(4)]
    print(f"   engine forecast for W+1..W+4 (same level every week): {got}")
    check("forecast for W+1", 41.0, got[0])


def ex6():
    title(6, "Base model declines for the OCCASIONAL customer")
    h = history(OCCASIONAL)
    last6 = [h.get(wk(n), 0) for n in range(6, 0, -1)]
    print(f"   last 6 weeks: {last6} -> only {sum(1 for x in last6 if x)} weeks with sales (< 3)")
    check("base model result", None, base_median.forecast(h, W1))


def ex7():
    title(7, "Own-rate fallback for the OCCASIONAL customer")
    h = history(OCCASIONAL)
    total = sum(OCCASIONAL)
    fb = own_rate_fallback(h, AS_OF, 52, HISTORY_START)
    print(f"   total {total} over 13 observed weeks = {total / 13:.4f} per week")
    print(f"   grade: INTERMITTENT + fallback -> {classifier.confidence_grade('INTERMITTENT', True)}")
    check("fallback per week", round(240 / 13, 4), fb.value)


def ex8():
    title(8, "Peer x size fallback (a customer with no sales of this product)")
    peer_weeks = [20, 20, 30, 10, 20]          # every customer-week the product sold, any customer
    peer_avg = sum(peer_weeks) / len(peer_weeks)
    cat_totals = {("A", "Milk"): 300.0, ("B", "Milk"): 600.0, ("C", "Milk"): 600.0,
                  ("NEW", "Milk"): 1200.0, ("E", "Milk"): 2000.0}
    med = median(cat_totals.values())
    mag = magnitude_from_aggregates("NEW", "Milk", cat_totals, {"Milk": med})
    print(f"   peer average {peer_avg};  customer's Milk total 1200 / median customer {med} = {mag}")
    print(f"   fallback = {peer_avg} x {mag} = {peer_avg * mag}")
    check("magnitude", 2.0, mag)
    check("fallback", 40.0, peer_avg * mag)


def ex9():
    title(9, "Seasonal naive + trend (model exists, not switched on)")
    h = {W1 - timedelta(weeks=52): 100.0}
    for n in range(1, 17):                     # recent 16 weeks: 110/wk, same weeks last year: 100/wk
        h[W1 - timedelta(weeks=n)] = 110.0
        h[W1 - timedelta(weeks=n + 52)] = 100.0
    f = seasonal_naive_yoy.forecast(h, W1)
    print(f"   same week last year 100; last 16 wks {16 * 110} vs same 16 last year {16 * 100}"
          f" -> ratio {f.yoy_ratio} -> {f.seasonal_baseline} x {f.yoy_ratio} = {f.predicted_qty}")
    check("forecast", 110.0, f.predicted_qty)


def ex10():
    title(10, "Delivery day pattern (recorded, not yet used for suggestions)")
    dels = []
    for n in range(1, 11):                      # 10 weeks of Mon 40 / Wed 30 / Fri 30
        mon = W0 - timedelta(weeks=n)               # weeks start on Monday
        dels += [(mon, 40.0), (mon + timedelta(days=2), 30.0), (mon + timedelta(days=4), 30.0)]
    p = delivery_pattern.learn(dels, AS_OF)
    print(f"   Mon {p.mon_share}  Wed {p.wed_share}  Fri {p.fri_share}  ({p.weeks_sampled} weeks -> {p.confidence})")
    check("Monday share", 0.4, p.mon_share)
    check("confidence", "MED", p.confidence)


def ex11():
    title(11, "Percent scenario +10% on the STEADY customer, and its flag")
    lf = ForecastLeaf("STEADY", "MILK2L", W1, 1, 41.0)
    inputs = FlagInputs(history={("STEADY", "MILK2L"): history(STEADY)},
                        sku_first_sale={"MILK2L": date(2026, 8, 1)}, pack_size={"MILK2L": 6},
                        history_start=HISTORY_START)
    nz = sorted(v for v in STEADY if v)
    p95 = percentile([float(v) for v in nz], 0.95)
    print(f"   non-zero weeks sorted {nz}")
    print(f"   P95 position = 0.95 x (12-1) = 10.45 -> {nz[10]} + 0.45 x ({nz[11]} - {nz[10]}) = {p95}")
    for pct in (10, 5, 4):
        r = build_cascade(scenario_id=1, lever_type="PERCENT", lever_value=pct, leaves=[lf],
                          other_effects={}, existing={}, flag_inputs=inputs, as_of_week=W0,
                          weeks_in_period=[W1])
        t = r.targets[0]
        print(f"   +{pct}%: 41 x {1 + pct / 100} = {t.target_qty}  flags {t.flag_codes or 'none'}")
        if pct == 10:
            check("target +10%", 45.1, t.target_qty)
            check("flag +10%", ["above_historical_p95"], t.flag_codes)
        if pct == 4:
            check("flags +4%", [], t.flag_codes)
    check("P95", 42.9, round(p95, 4))


def ex12():
    title(12, "Add-per-week and weekly-total scenarios (split pro rata)")
    leaves = [ForecastLeaf("A", "MILK2L", W1, 1, 60.0), ForecastLeaf("B", "MILK2L", W1, 2, 30.0),
              ForecastLeaf("C", "MILK2L", W1, 3, 10.0)]
    for lt, v, hand in (("ABSOLUTE_DELTA", 30.0, [78.0, 39.0, 13.0]), ("FIXED_QTY", 150.0, [90.0, 45.0, 15.0])):
        eff, _ = compute_lever_effects(1, lt, v, leaves)
        got = [apply_effects(l.forecast_qty, [eff[l.key]]) for l in leaves]
        print(f"   {lt} {v}: shares 0.6 / 0.3 / 0.1 -> {got}")
        check(lt, hand, got)


def ex13():
    title(13, "A target inside another target (inner fixed, outer total holds)")
    leaves = [ForecastLeaf("A", "MILK2L", W1, 1, 200.0, dist_centre="Makhado"),
              ForecastLeaf("B", "MILK2L", W1, 2, 500.0, dist_centre="Makhado"),
              ForecastLeaf("C", "MILK2L", W1, 3, 300.0, dist_centre="Makhado")]
    dc = GoalSpec(1, ScenarioScope.from_dict({"distCentre": ["Makhado"]}), "FIXED_QTY", 1200.0)
    a = GoalSpec(2, ScenarioScope.from_dict({"customer": ["A"]}), "FIXED_QTY", 300.0)
    alone = resolve_week(W1, leaves, [dc])
    both = resolve_week(W1, leaves, [dc, a])
    got_alone = [alone.values[l.key].qty for l in leaves]
    got = [both.values[l.key].qty for l in leaves]
    print(f"   Makhado = 1200 alone: {got_alone}")
    print(f"   + A = 300 inside it:  {got}  (total {sum(got)})")
    check("DC alone", [240.0, 600.0, 360.0], got_alone)
    check("nested", [300.0, 562.5, 337.5], got)
    clash = resolve_week(W1, leaves, [dc, GoalSpec(2, a.scope, "FIXED_QTY", 1300.0)])
    check("conflict when inner > outer", 1, len(clash.conflicts))


def ex14():
    title(14, "The other three flags")
    hist = {W1 - timedelta(weeks=52): 100.0, wk(1): 200.0}
    yoy = evaluate_flags(target_qty=130.0, forecast_qty=110.0, history=hist, forecast_week=W1,
                         as_of_week=W0, pack_size=6, sku_first_sale=date(2020, 1, 1))
    print(f"   target 130 vs 100 same week last year = +30% -> {[f.code for f in yoy]}")
    new = evaluate_flags(target_qty=12.0, forecast_qty=10.0, history={}, forecast_week=W1, as_of_week=W0,
                         pack_size=6, sku_first_sale=date(2026, 9, 9), history_start=HISTORY_START)
    print(f"   first invoice 2026-09-09 = {(W0 - date(2026, 9, 9)).days / 7:.1f} weeks ago (< 13) -> {[f.code for f in new]}")
    old = evaluate_flags(target_qty=12.0, forecast_qty=10.0, history={}, forecast_week=W1, as_of_week=W0,
                         pack_size=6, sku_first_sale=date(2026, 8, 3), history_start=HISTORY_START)
    print(f"   first invoice 2026-08-03, within 4 weeks of records starting -> {[f.code for f in old] or 'none'}")
    pack = evaluate_flags(target_qty=4.0, forecast_qty=3.0, history={}, forecast_week=W1, as_of_week=W0,
                          pack_size=6, sku_first_sale=date(2020, 1, 1))
    print(f"   target 4 with a pack of 6 -> {[f.code for f in pack]}")
    check("YoY", ["above_yoy_growth_limit"], [f.code for f in yoy])
    check("new product", ["new_product_requires_review"], [f.code for f in new])
    check("established", [], [f.code for f in old])
    check("pack", ["below_pack_size_minimum"], [f.code for f in pack])


def ex15():
    title(15, "From weekly number to suggested order quantity")
    cases = [
        ("no target: median 41, 12 already on order", 41.0, 12.0, False, 30.0),
        ("target 45.1, nothing ordered yet", 45.1, 0.0, True, 48.0),
        ("target 45.1, 15 already covered", 45.1, 15.0, True, 36.0),
        ("same, if it rounded to NEAREST pack", 45.1, 15.0, False, 30.0),
        ("target 45.1, Monday already took 36", 45.1, 36.0, True, 12.0),
    ]
    for label, basis, covered, up, hand in cases:
        got = remaining_week_qty(basis, covered, 6, round_up=up)
        packs = (basis - covered) / 6
        print(f"   {label}: ({basis} - {covered}) / 6 = {packs:.3f} packs -> {got}")
        check(label, hand, got)


def ex16():
    title(16, "What the rep did with the suggestions")
    shown = [PresentedSuggestion(1, "MILK2L", 36.0), PresentedSuggestion(2, "MAAS1L", 12.0),
             PresentedSuggestion(3, "JUICE", 6.0)]
    ordered = {"MILK2L": 36.0, "MAAS1L": 18.0, "YOG500": 12.0}
    decisions, manual = classify_decisions(shown, ordered)
    for d in decisions:
        print(f"   suggestion {d.id}: ordered {d.accepted_qty} -> {d.decision}")
    print(f"   not suggested but ordered: {manual} -> MANUAL_ADD")
    check("decisions", ["ACCEPTED", "MODIFIED", "REJECTED"], [d.decision for d in sorted(decisions, key=lambda d: d.id)])
    check("manual add", {"YOG500": 12.0}, manual)


def ex17():
    title(17, "Target Workbench numbers")
    leaves = [
        WorkbenchLeaf("STEADY", "MILK2L", "Polokwane", "R1", "Milk", forecast=41, target_id=1, target=45.1,
                      source="SCENARIO", delivered=0, on_order=36),
        WorkbenchLeaf("OCCASIONAL", "MILK2L", "Polokwane", "R1", "Milk", forecast=18.4615),
    ]
    total, rows = rollup(leaves, "dist_centre", {})
    print(f"   forecast {total.forecast}  target {total.target}  committed {total.committed}"
          f"  -> {total.committed / total.target:.0%} of target")
    check("target total", 63.5615, round(total.target, 4))
    check("committed", 36.0, total.committed)


EXAMPLES = [ex1, ex2, ex3, ex4, ex5, ex6, ex7, ex8, ex9, ex10, ex11, ex12, ex13, ex14, ex15, ex16, ex17]

if __name__ == "__main__":
    pick = [int(a) for a in sys.argv[1:]] or range(1, len(EXAMPLES) + 1)
    for n in pick:
        EXAMPLES[n - 1]()
    bad = [l for l, ok in results if not ok]
    print(f"\n{len(results) - len(bad)} of {len(results)} checks match the guide."
          + (f"  MISMATCHES: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)

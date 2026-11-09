"""Target resolver (resolver.py): targets set on any node, nested targets
pinned, the parent total holds, everything adds up at every level."""
from datetime import date

import pytest

from services.forecasting.resolver import GoalSpec, resolve_week
from services.forecasting.scenarios import ForecastLeaf, ScenarioScope, apply_effects, compute_lever_effects

WK = date(2026, 11, 9)


def lf(cust, sku, qty, route="R1", dc="MAK", cat="Milk"):
    return ForecastLeaf(cust, sku, WK, None, float(qty), route=route, dist_centre=dc, category=cat)


def goal(sid, lever, value, measure="UNITS", **scope):
    return GoalSpec(sid, ScenarioScope.from_dict(scope), lever, float(value), measure)


def total(res, keys=None, pred=None):
    return round(sum(v.qty for k, v in res.values.items()
                     if (keys is None or k in keys) and (pred is None or pred(k))), 4)


# A small company: 2 DCs, 3 routes, 5 customers, Milk + Juice.
LEAVES = [
    lf("C1", "MILK2L", 100, route="R1"), lf("C1", "JUICE", 50, route="R1", cat="Juice"),
    lf("C2", "MILK2L", 300, route="R1"), lf("C2", "JUICE", 50, route="R1", cat="Juice"),
    lf("C3", "MILK2L", 200, route="R2"),
    lf("C4", "MILK2L", 80, route="R3", dc="LOU"), lf("C5", "JUICE", 20, route="R3", dc="LOU", cat="Juice"),
]
MAK = {l.key for l in LEAVES if l.dist_centre == "MAK"}
C1 = {l.key for l in LEAVES if l.customer == "C1"}


class TestSingleScenarioMatchesTheOldMaths:
    """With one scenario and nothing nested, the resolver gives exactly the
    numbers the guide teaches (compute_lever_effects + apply_effects)."""

    @pytest.mark.parametrize("lever,value", [("PERCENT", 20), ("ABSOLUTE_DELTA", 70), ("FIXED_QTY", 1000)])
    def test_same_as_old_cascade(self, lever, value):
        scoped = [l for l in LEAVES if l.dist_centre == "MAK"]
        eff, _ = compute_lever_effects(1, lever, value, scoped)
        old = {l.key: apply_effects(l.forecast_qty, [eff[l.key]]) for l in scoped}
        res = resolve_week(WK, LEAVES, [goal(1, lever, value, distCentre=["MAK"])])
        for k, q in old.items():
            assert res.values[k].qty == pytest.approx(q, abs=1e-3)

    def test_outside_scope_stays_at_forecast(self):
        res = resolve_week(WK, LEAVES, [goal(1, "PERCENT", 20, distCentre=["MAK"])])
        assert res.values[("C4", "MILK2L", WK)].qty == 80
        assert res.values[("C4", "MILK2L", WK)].scenario_ids == []
        assert res.values[("C1", "MILK2L", WK)].scenario_ids == [1]


class TestNestedTargetsKeepTheParentTotal:
    def test_customer_inside_dc_is_pinned_and_dc_still_adds_up(self):
        res = resolve_week(WK, LEAVES, [
            goal(1, "FIXED_QTY", 1000, distCentre=["MAK"]),   # MAK forecast 700 -> 1000
            goal(2, "FIXED_QTY", 300, customer=["C1"]),       # C1 forecast 150 -> 300
        ])
        assert total(res, MAK) == pytest.approx(1000)
        assert total(res, C1) == pytest.approx(300)
        # C1 keeps its own milk/juice split (2:1)
        assert res.values[("C1", "MILK2L", WK)].qty == pytest.approx(200)
        # the other 700 is shared by the rest of MAK by forecast (300:50:200 of 550)
        assert res.values[("C2", "MILK2L", WK)].qty == pytest.approx(700 * 300 / 550, abs=1e-3)
        assert res.values[("C1", "JUICE", WK)].scenario_ids == [1, 2]
        assert not res.conflicts

    def test_order_of_creation_does_not_matter_for_nesting(self):
        a = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 1000, distCentre=["MAK"]),
                                      goal(2, "FIXED_QTY", 300, customer=["C1"])])
        b = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 300, customer=["C1"]),
                                      goal(2, "FIXED_QTY", 1000, distCentre=["MAK"])])
        assert {k: v.qty for k, v in a.values.items()} == pytest.approx({k: v.qty for k, v in b.values.items()})

    def test_percent_child_is_relative_to_its_own_forecast(self):
        res = resolve_week(WK, LEAVES, [goal(1, "PERCENT", 10, distCentre=["MAK"]),   # 700 -> 770
                                        goal(2, "PERCENT", 100, customer=["C1"])])   # 150 -> 300
        assert total(res, C1) == pytest.approx(300)
        assert total(res, MAK) == pytest.approx(770)

    def test_three_levels_dc_route_customer(self):
        r1 = {l.key for l in LEAVES if l.route == "R1"}
        res = resolve_week(WK, LEAVES, [
            goal(1, "FIXED_QTY", 1400, distCentre=["MAK"]),
            goal(2, "FIXED_QTY", 1000, route=["R1"]),
            goal(3, "FIXED_QTY", 100, customer=["C1"]),
        ])
        assert total(res, MAK) == pytest.approx(1400)
        assert total(res, r1) == pytest.approx(1000)
        assert total(res, C1) == pytest.approx(100)
        assert res.values[("C3", "MILK2L", WK)].qty == pytest.approx(400)   # all that's left in MAK

    def test_category_across_a_dc(self):
        milk_mak = {l.key for l in LEAVES if l.dist_centre == "MAK" and l.category == "Milk"}
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 900, distCentre=["MAK"]),
                                        goal(2, "FIXED_QTY", 800, distCentre=["MAK"], category=["Milk"])])
        assert total(res, milk_mak) == pytest.approx(800)
        assert total(res, MAK) == pytest.approx(900)

    def test_inner_targets_bigger_than_the_parent_is_a_conflict(self):
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 500, distCentre=["MAK"]),
                                        goal(2, "FIXED_QTY", 600, customer=["C2"])])
        assert len(res.conflicts) == 1
        c = res.conflicts[0]
        assert c.scenario_id == 1 and c.goal == 500 and c.inner_total == pytest.approx(600)
        assert "more than its own target" in c.message

    def test_children_covering_the_whole_parent_must_match_it(self):
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 1000, distCentre=["LOU"]),
                                        goal(2, "FIXED_QTY", 100, customer=["C4"]),
                                        goal(3, "FIXED_QTY", 100, customer=["C5"])])
        assert [c.scenario_id for c in res.conflicts] == [1]


class TestPinnedCustomerProducts:
    def test_override_is_pinned_and_siblings_rebalance(self):
        key = ("C2", "MILK2L", WK)
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 1000, distCentre=["MAK"])],
                           pinned={key: 500})
        assert res.values[key].qty == 500
        assert res.values[key].pinned
        assert total(res, MAK) == pytest.approx(1000)

    def test_override_outside_any_scenario_just_sets_the_value(self):
        key = ("C4", "MILK2L", WK)
        res = resolve_week(WK, LEAVES, [], pinned={key: 12})
        assert res.values[key].qty == 12

    def test_locked_rows_keep_their_value(self):
        key = ("C3", "MILK2L", WK)
        res = resolve_week(WK, LEAVES, [goal(1, "PERCENT", 50, distCentre=["MAK"])], pinned={key: 200})
        assert res.values[key].qty == 200
        assert total(res, MAK) == pytest.approx(1050)


class TestLitres:
    SIZES = {"MILK2L": 2.0, "JUICE": 1.0}

    def test_litre_goal_adds_up_in_litres(self):
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 2000, "LITRES", distCentre=["MAK"])],
                           litre_factor=self.SIZES)
        litres = sum(v.qty * self.SIZES[k[1]] for k, v in res.values.items() if k in MAK)
        assert litres == pytest.approx(2000)
        # MAK forecast litres = 600*2 + 100*1 = 1300 -> rate 2000/1300
        assert res.values[("C2", "MILK2L", WK)].qty == pytest.approx(300 * 2000 / 1300, abs=1e-3)

    def test_products_without_a_size_keep_their_forecast(self):
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 1200, "LITRES", distCentre=["MAK"])],
                           litre_factor={"MILK2L": 2.0})
        assert res.values[("C1", "JUICE", WK)].qty == 50
        assert any("no litre size" in w for w in res.warnings)

    def test_unit_child_inside_litre_parent(self):
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 2000, "LITRES", distCentre=["MAK"]),
                                        goal(2, "FIXED_QTY", 150, customer=["C1"])],
                           litre_factor=self.SIZES)
        litres = sum(v.qty * self.SIZES[k[1]] for k, v in res.values.items() if k in MAK)
        assert litres == pytest.approx(2000)
        assert total(res, C1) == pytest.approx(150)


class TestOverlaps:
    def test_partial_overlap_newer_wins_on_shared_part(self):
        # Scenario 1: route R1 (C1, C2) +100%.  Scenario 2: Milk in MAK = 1000 (C1,C2,C3 milk).
        res = resolve_week(WK, LEAVES, [goal(1, "PERCENT", 100, route=["R1"]),
                                        goal(2, "FIXED_QTY", 1000, distCentre=["MAK"], category=["Milk"])])
        milk_mak = {l.key for l in LEAVES if l.dist_centre == "MAK" and l.category == "Milk"}
        assert total(res, milk_mak) == pytest.approx(1000)
        # R1 juice (not shared) keeps scenario 1's +100%
        assert res.values[("C1", "JUICE", WK)].qty == pytest.approx(100)
        assert any("overlap" in w for w in res.warnings)

    def test_identical_scope_newer_replaces_older(self):
        res = resolve_week(WK, LEAVES, [goal(1, "FIXED_QTY", 1000, distCentre=["MAK"]),
                                        goal(2, "FIXED_QTY", 800, distCentre=["MAK"])])
        assert total(res, MAK) == pytest.approx(800)
        assert res.values[("C1", "MILK2L", WK)].scenario_ids == [2]


class TestEverythingAddsUp:
    def test_rollups_at_every_level_equal_the_sum_of_their_parts(self):
        res = resolve_week(WK, LEAVES, [
            goal(1, "FIXED_QTY", 1500, distCentre=["MAK"]),
            goal(2, "PERCENT", 25, customer=["C2"]),
            goal(3, "FIXED_QTY", 60, customer=["C1"], category=["Juice"]),
        ])
        by = lambda attr: {getattr(l, attr) for l in LEAVES}
        company = total(res)
        assert company == pytest.approx(sum(total(res, pred=lambda k, d=d: next(
            l for l in LEAVES if l.key == k).dist_centre == d) for d in by("dist_centre")))
        assert total(res, MAK) == pytest.approx(1500)
        assert total(res, {l.key for l in LEAVES if l.customer == "C2"}) == pytest.approx(350 * 1.25)
        assert res.values[("C1", "JUICE", WK)].qty == pytest.approx(60)

    def test_zero_forecast_remainder_is_warned(self):
        leaves = [lf("C1", "A", 0), lf("C1", "B", 0)]
        res = resolve_week(WK, leaves, [goal(1, "FIXED_QTY", 100, customer=["C1"])])
        assert all(v.qty == 0 for v in res.values.values())
        assert any("could not be shared out" in w for w in res.warnings)

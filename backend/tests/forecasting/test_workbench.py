"""Phase 5 Target Workbench rollup (pure)."""

import pytest

from services.forecasting.workbench import WorkbenchLeaf, rollup


def leaf(cust, sku, dc="WC", route="R1", forecast=100.0, target=None, delivered=0.0,
         on_order=0.0, source=None, flagged=False, override=None, tid=None):
    return WorkbenchLeaf(customer=cust, sku=sku, dist_centre=dc, route=route, category="Milk",
                         forecast=forecast, target_id=tid if tid is not None else (1 if target is not None else None),
                         target=target, override=override, source=source, is_flagged=flagged,
                         delivered=delivered, on_order=on_order)


LEAVES = [
    leaf("C1", "S1", forecast=100, target=120, delivered=50, on_order=40, source="SCENARIO", flagged=True),
    leaf("C1", "S2", forecast=50, delivered=60),
    leaf("C2", "S1", route="R2", forecast=200, target=150, override=150, source="OVERRIDE"),
    leaf("C3", "S1", dc="KZN", forecast=10),
]


def test_company_level_groups_by_dc():
    total, rows = rollup(LEAVES, "dist_centre", {})
    assert [r.key for r in rows] == ["WC", "KZN"]
    wc = rows[0]
    assert wc.forecast == 350 and wc.target == 120 + 50 + 150   # untargeted leaf: target = forecast
    assert wc.delivered == 110 and wc.on_order == 40 and wc.committed == 150
    assert (wc.leaf_count, wc.targeted_count, wc.flagged_count, wc.override_count) == (3, 2, 1, 1)
    assert total.leaf_count == 4 and total.target == 330


def test_drill_path_filters_case_insensitively():
    total, rows = rollup(LEAVES, "customer", {"dist_centre": "wc", "route": "R1"})
    assert [r.key for r in rows] == ["C1"]
    assert total.target == 170


def test_sku_level_exposes_target_row():
    _, rows = rollup(LEAVES, "sku", {"dist_centre": "WC", "route": "R2", "customer": "C2"})
    assert len(rows) == 1
    r = rows[0]
    assert r.target_id == 1 and r.override == 150 and r.source == "OVERRIDE"


def test_forecast_sourced_target_row_is_not_counted_as_targeted():
    _, rows = rollup([leaf("C1", "S1", target=100, source="FORECAST")], "sku", {})
    assert rows[0].targeted_count == 0


def test_litre_factors_scale_every_measure():
    total, _ = rollup([leaf("C1", "S1", forecast=10, target=20, delivered=5)], "dist_centre", {},
                      {"S1": 2.0})
    assert (total.forecast, total.target, total.delivered) == (20, 40, 10)


def test_bad_level_rejected():
    with pytest.raises(ValueError):
        rollup(LEAVES, "region", {})


def test_category_level_between_customer_and_product():
    leaves = [leaf("C1", "S1", forecast=10), leaf("C1", "S2", forecast=5)]
    leaves[1].category = "Juice"
    _, rows = rollup(leaves, "category", {"customer": "C1"})
    assert sorted((r.key, r.forecast) for r in rows) == [("Juice", 5), ("Milk", 10)]


def test_category_filter_works_at_company_level():
    leaves = [leaf("C1", "S1", forecast=10), leaf("C2", "S2", dc="KZN", forecast=5)]
    leaves[1].category = "Juice"
    total, rows = rollup(leaves, "dist_centre", {"category": "milk"})
    assert [r.key for r in rows] == ["WC"] and total.forecast == 10

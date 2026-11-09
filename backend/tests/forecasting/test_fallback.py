"""Unit tests for the FallbackResolver's pure magnitude math.

The SQL-backed loaders are integration-tested in test_engine_live.py
against a real DB. Here we only check the pure helper, which takes
pre-computed aggregates and returns a magnitude.
"""
from services.forecasting.fallback import magnitude_from_aggregates


class TestMagnitudeFromAggregates:
    def test_shoprite_sized_customer_gets_magnitude_above_one(self):
        """A customer whose category total is 10x the peer median should
        get magnitude 10 -- their new-SKU fallback starts proportionally
        larger."""
        customer_cat = {("SR_DBV", "Milk"): 100_000.0}
        cat_median = {"Milk": 10_000.0}

        magnitude = magnitude_from_aggregates(
            "SR_DBV", "Milk", customer_cat, cat_median
        )

        assert magnitude == 10.0

    def test_tuckshop_sized_customer_gets_magnitude_well_below_one(self):
        """Small customer -> magnitude 0.01 -> new-SKU fallback starts
        tiny -> pack-size snap will likely floor it to 0."""
        customer_cat = {("TUK_STE", "Cheese"): 100.0}
        cat_median = {"Cheese": 10_000.0}

        magnitude = magnitude_from_aggregates(
            "TUK_STE", "Cheese", customer_cat, cat_median
        )

        assert magnitude == 0.01

    def test_customer_at_median_gets_magnitude_one(self):
        customer_cat = {("CUST", "Milk"): 10_000.0}
        cat_median = {"Milk": 10_000.0}

        magnitude = magnitude_from_aggregates(
            "CUST", "Milk", customer_cat, cat_median
        )

        assert magnitude == 1.0

    def test_customer_with_no_category_history_defaults_to_one(self):
        """Customer never bought the category -> can't scale -> use raw
        peer avg unmodified."""
        customer_cat: dict = {}
        cat_median = {"Milk": 10_000.0}

        magnitude = magnitude_from_aggregates(
            "NEW_CUST", "Milk", customer_cat, cat_median
        )

        assert magnitude == 1.0

    def test_missing_category_median_defaults_to_one(self):
        customer_cat = {("CUST", "RareCategory"): 500.0}
        cat_median: dict = {}

        magnitude = magnitude_from_aggregates(
            "CUST", "RareCategory", customer_cat, cat_median
        )

        assert magnitude == 1.0

    def test_zero_peer_median_defaults_to_one(self):
        customer_cat = {("CUST", "Milk"): 100.0}
        cat_median = {"Milk": 0.0}

        magnitude = magnitude_from_aggregates(
            "CUST", "Milk", customer_cat, cat_median
        )

        assert magnitude == 1.0

    def test_none_category_defaults_to_one(self):
        """SKU with no category assigned -- can't look up a median."""
        magnitude = magnitude_from_aggregates(
            "CUST", None, {}, {}
        )
        assert magnitude == 1.0

    def test_scenario_c_illustration(self):
        """Scenario C's tuckshop with the Phase 1 seed-style numbers.

        Peer SKU avg for TEST_CHZ_CHE_1KG: say 25 L/wk (seeded).
        Tuckshop's trailing Cheese total: say 220 L (2 non-zero weeks
        across 10, qtys 120 + 100).
        Peer-median Cheese customer: say 2000 L trailing year.

        Magnitude = 220 / 2000 = 0.11
        Fallback value = 25 * 0.11 = 2.75 L

        That's tiny but mathematically consistent -- a tuckshop with
        little recent Cheese activity should start small for a new
        cheese SKU. Downstream pack-size snap decides whether to show
        a suggestion at all.
        """
        customer_cat = {("TEST_C_TUCKSHOP_STE", "Cheese"): 220.0}
        cat_median = {"Cheese": 2000.0}

        magnitude = magnitude_from_aggregates(
            "TEST_C_TUCKSHOP_STE", "Cheese", customer_cat, cat_median
        )

        assert abs(magnitude - 0.11) < 1e-9
        # Peer avg 25 * magnitude 0.11 = 2.75 L -- in line with the
        # intuition that this leaf shouldn't be forecast to buy much.
        assert abs(25.0 * magnitude - 2.75) < 1e-9

"""Phase 4: target-aware suggestion maths + decision capture."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from services.forecasting import suggestions as sugg
from services.forecasting.suggestions import (
    DECISION_ACCEPTED,
    DECISION_MODIFIED,
    DECISION_REJECTED,
    PresentedSuggestion,
    classify_decisions,
    remaining_week_qty,
)


class TestRemainingWeekQty:
    def test_matches_legacy_snap(self):
        # Legacy: max(0, median - covered), nearest pack half-up.
        assert remaining_week_qty(100.0, 0.0, 12) == 96.0     # 8.33 packs -> 8
        assert remaining_week_qty(100.0, 0.0, 8) == 104.0     # 12.5 packs -> 13 (half-up)

    def test_subtracts_covered(self):
        assert remaining_week_qty(100.0, 60.0, 10) == 40.0

    def test_never_negative(self):
        assert remaining_week_qty(100.0, 150.0, 10) == 0.0

    def test_user_decision_example(self):
        # Target 100/wk, Mon took 60 -> Wed suggests the 40 shortfall.
        assert remaining_week_qty(100.0, 0.0, 1) == 100.0
        assert remaining_week_qty(100.0, 60.0, 1) == 40.0

    def test_target_rounds_up_to_meet_the_ask(self):
        # Smoke test SPA227 x 323: target 12, pack 10 -> nearest gave 10.
        assert remaining_week_qty(12.0, 0.0, 10) == 10.0
        assert remaining_week_qty(12.0, 0.0, 10, round_up=True) == 20.0

    def test_round_up_exact_multiple_adds_nothing(self):
        assert remaining_week_qty(24.0, 0.0, 12, round_up=True) == 24.0
        assert remaining_week_qty(24.0000001, 0.0, 12, round_up=True) == 24.0   # float noise

    def test_round_up_after_coverage(self):
        assert remaining_week_qty(100.0, 85.0, 10, round_up=True) == 20.0
        assert remaining_week_qty(100.0, 100.0, 10, round_up=True) == 0.0
        assert remaining_week_qty(100.0, 130.0, 10, round_up=True) == 0.0

    @pytest.mark.parametrize("pack", [0, None, -3])
    def test_bad_pack_size_treated_as_one(self, pack):
        assert remaining_week_qty(7.4, 0.0, pack) == 7.0


class TestClassifyDecisions:
    def test_accept_modify_reject_and_manual(self):
        presented = [
            PresentedSuggestion(1, "A", 24.0),
            PresentedSuggestion(2, "B", 12.0),
            PresentedSuggestion(3, "C", 6.0),
        ]
        decisions, manual = classify_decisions(presented, {"A": 24.0, "b": 18.0, "Z": 5.0})
        got = {d.id: (d.decision, d.accepted_qty) for d in decisions}
        assert got == {
            1: (DECISION_ACCEPTED, 24.0),
            2: (DECISION_MODIFIED, 18.0),     # case-insensitive stock match
            3: (DECISION_REJECTED, 0.0),
        }
        assert manual == {"Z": 5.0}

    def test_lines_for_same_stock_are_summed(self):
        decisions, _ = classify_decisions([PresentedSuggestion(1, "A", 24.0)], {"A": 12.0, "a ": 12.0})
        assert decisions[0].decision == DECISION_ACCEPTED

    def test_ordering_less_is_modified_not_rejected(self):
        decisions, _ = classify_decisions([PresentedSuggestion(1, "A", 24.0)], {"A": 6.0})
        assert decisions[0].decision == DECISION_MODIFIED

    def test_only_latest_presentation_per_stock_is_decided(self):
        presented = [PresentedSuggestion(5, "A", 24.0), PresentedSuggestion(9, "A", 12.0)]
        decisions, _ = classify_decisions(presented, {"A": 12.0})
        assert [(d.id, d.decision) for d in decisions] == [(9, DECISION_ACCEPTED)]

    def test_no_suggestions_everything_manual(self):
        decisions, manual = classify_decisions([], {"A": 3.0, "B": 0.0})
        assert decisions == [] and manual == {"A": 3.0}


class TestPersistenceIsBestEffort:
    def test_presentation_failure_returns_empty_and_rolls_back(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError("table missing")
        ids = sugg.record_presentation(
            db, customer="C1", delivery_date=date(2026, 11, 9), username="rep",
            rows=[sugg.PresentationRow("A", 24.0, sugg.SOURCE_BASE_MODEL)])
        assert ids == {}
        db.rollback.assert_called_once()

    def test_decision_failure_returns_none_and_rolls_back(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError("deadlock")
        assert sugg.record_order_decisions(
            db, suggestion_ids=[1], customer="C1", username="rep", order_no="SO1",
            delivery_date=date(2026, 11, 9), lines=[("A", 24.0)]) is None
        db.rollback.assert_called_once()

    def test_presentation_insert_batches_under_param_limit(self, monkeypatch):
        db = MagicMock()
        db.execute.return_value.mappings.return_value.all.return_value = []
        monkeypatch.setattr(sugg, "resolve_agent_id", lambda *a: 7)
        rows = [sugg.PresentationRow(f"S{i}", 1.0, sugg.SOURCE_BASE_MODEL) for i in range(400)]
        sugg.record_presentation(db, customer="C1", delivery_date=date(2026, 11, 9),
                                 username="rep", rows=rows)
        inserts = [c for c in db.execute.call_args_list if "INSERT INTO" in str(c.args[0])]
        assert len(inserts) == 3
        assert all(len(c.args[1]) < 2100 for c in inserts)
        db.commit.assert_called_once()

    def test_decisions_only_touch_this_customers_undecided_rows(self, monkeypatch):
        db = MagicMock()
        db.execute.return_value.mappings.return_value = iter([])
        monkeypatch.setattr(sugg, "resolve_agent_id", lambda *a: None)
        sugg.record_order_decisions(db, suggestion_ids=[1, 2], customer="C1", username="rep",
                                    order_no="SO1", delivery_date=date(2026, 11, 9), lines=[])
        select_sql = str(db.execute.call_args_list[0].args[0])
        params = db.execute.call_args_list[0].args[1]
        assert "cDecision IS NULL" in select_sql and "cUsername = :u" in select_sql
        assert params["c"] == "C1" and params["u"] == "rep"

"""Scenario cascade: scope, lever maths, stacking, approval rules."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from services.forecasting import scenarios as sc
from services.forecasting.reasonableness import FLAG_NEW_PRODUCT, FLAG_P95
from services.forecasting.scenarios import (
    Effect,
    ExistingTarget,
    FlagInputs,
    ForecastLeaf,
    ScenarioError,
    ScenarioScope,
    apply_effects,
    build_cascade,
    compute_lever_effects,
    validate_definition,
    validate_reviews,
)

W0 = date(2026, 11, 2)
W1 = date(2026, 11, 9)
W2 = date(2026, 11, 16)


def leaf(cust="C1", sku="S1", week=W1, qty=100.0, **dims) -> ForecastLeaf:
    return ForecastLeaf(customer=cust, sku=sku, week=week, forecast_id=1, forecast_qty=qty, **dims)


def stable_inputs(*leaves: ForecastLeaf, level=1000.0) -> FlagInputs:
    """History high enough that nothing flags, old SKUs, pack size 1."""
    hist = {W0 - timedelta(weeks=i): level for i in range(1, 60)}
    return FlagInputs(
        history={(lf.customer, lf.sku): dict(hist) for lf in leaves},
        sku_first_sale={lf.sku: date(2015, 1, 1) for lf in leaves},
        pack_size={lf.sku: 1 for lf in leaves},
    )


def cascade(leaves, lever_type="PERCENT", value=10.0, *, other=None, existing=None,
            inputs=None, weeks=None, scenario_id=5):
    return build_cascade(
        scenario_id=scenario_id, lever_type=lever_type, lever_value=value, leaves=leaves,
        other_effects=other or {}, existing=existing or {},
        flag_inputs=inputs or stable_inputs(*leaves), as_of_week=W0,
        weeks_in_period=weeks or [W1],
    )


# --- scope ---------------------------------------------------------------------

class TestScenarioScope:
    def test_empty_scope_matches_everything(self):
        assert ScenarioScope().matches(leaf())

    def test_and_across_dims_or_within(self):
        scope = ScenarioScope(dist_centre=("WC",), category=("Milk", "Juice"))
        assert scope.matches(leaf(dist_centre="WC", category="Juice"))
        assert not scope.matches(leaf(dist_centre="WC", category="Yoghurt"))
        assert not scope.matches(leaf(dist_centre="KZN", category="Milk"))

    def test_customer_and_sku_dims(self):
        scope = ScenarioScope(customer=("C1",), sku=("S1",))
        assert scope.matches(leaf("C1", "S1"))
        assert not scope.matches(leaf("C1", "S2"))

    def test_match_is_case_and_space_insensitive_like_sql_server(self):
        assert ScenarioScope(route=("rt 101",)).matches(leaf(route="RT 101  "))

    def test_excluded_leaf_does_not_match(self):
        scope = ScenarioScope(exclude_leaves=(("C1", "S1"),))
        assert not scope.matches(leaf("C1", "S1"))
        assert scope.matches(leaf("C1", "S2"))

    def test_json_roundtrip_matches_seed_format(self):
        raw = '{"distCentre":["WC"],"category":["Milk","Juice"]}'
        scope = ScenarioScope.from_json(raw)
        assert scope.dist_centre == ("WC",)
        assert scope.category == ("Milk", "Juice")
        assert ScenarioScope.from_json(scope.to_json()) == scope
        assert scope.to_json() == raw

    def test_exclusions_roundtrip(self):
        scope = ScenarioScope(sku=("S1",)).with_exclusions([("C1", "S1"), ("c1", "s1")])
        assert scope.exclude_leaves == (("C1", "S1"),)        # deduped case-insensitively
        assert ScenarioScope.from_json(scope.to_json()) == scope

    def test_strict_rejects_unknown_keys(self):
        with pytest.raises(ScenarioError):
            ScenarioScope.from_dict({"agentId": [1]}, strict=True)

    def test_dim_filters_map_to_forecast_columns(self):
        f = ScenarioScope(dist_centre=("WC",), sales_rep=("Vincent",)).dim_filters()
        assert f == {"cDistCentre": ("WC",), "cSalesRep": ("Vincent",)}


# --- lever maths ---------------------------------------------------------------

class TestLeverEffects:
    def test_percent_is_in_percent_units(self):
        effects, _ = compute_lever_effects(1, "PERCENT", 5.0, [leaf()])
        assert apply_effects(400.0, effects.values()) == 420.0      # walkthrough Scenario A

    def test_absolute_delta_split_pro_rata_per_week(self):
        leaves = [leaf("C1", qty=100.0), leaf("C2", qty=300.0), leaf("C1", week=W2, qty=50.0)]
        effects, warnings = compute_lever_effects(1, "ABSOLUTE_DELTA", 40.0, leaves)
        assert warnings == []
        assert effects[("C1", "S1", W1)].amount == pytest.approx(10.0)
        assert effects[("C2", "S1", W1)].amount == pytest.approx(30.0)
        assert effects[("C1", "S1", W2)].amount == pytest.approx(40.0)   # sole leaf that week

    def test_fixed_qty_sets_scope_total(self):
        leaves = [leaf("C1", qty=100.0), leaf("C2", qty=300.0)]
        effects, _ = compute_lever_effects(1, "FIXED_QTY", 800.0, leaves)
        totals = [apply_effects(lf.forecast_qty, [effects[lf.key]]) for lf in leaves]
        assert totals == [200.0, 600.0]

    def test_zero_forecast_week_cannot_be_split(self):
        effects, warnings = compute_lever_effects(1, "ABSOLUTE_DELTA", 50.0, [leaf(qty=0.0)])
        assert effects == {}
        assert len(warnings) == 1 and "could not be split" in warnings[0]

    def test_unknown_lever_rejected(self):
        with pytest.raises(ScenarioError):
            compute_lever_effects(1, "MAGIC", 1.0, [leaf()])


class TestApplyEffects:
    def test_rounds_to_storage_precision(self):
        # 100 * 1.1 == 110.00000000000001 in floats.
        assert apply_effects(100.0, [Effect(1, "MULT", 1.1)]) == 110.0

    def test_stacks_in_scenario_id_order(self):
        effects = [Effect(2, "ADD", 10.0), Effect(1, "MULT", 2.0)]
        assert apply_effects(100.0, effects) == 210.0     # (100*2)+10, not (100+10)*2

    def test_set_replaces_earlier_effects(self):
        assert apply_effects(100.0, [Effect(1, "MULT", 3.0), Effect(2, "SET", 50.0)]) == 50.0

    def test_never_negative(self):
        assert apply_effects(100.0, [Effect(1, "ADD", -500.0)]) == 0.0

    def test_no_effects_is_forecast(self):
        assert apply_effects(57.0, []) == 57.0


class TestValidateDefinition:
    def test_ok(self):
        validate_definition("PERCENT", 5.0, W1, W2)

    @pytest.mark.parametrize("lt,lv,pf,pt", [
        ("PERCENT", -100.0, W1, W2),
        ("FIXED_QTY", -1.0, W1, W2),
        ("BOGUS", 1.0, W1, W2),
        ("PERCENT", 5.0, W2, W1),
        ("PERCENT", 5.0, W1, W1 + timedelta(weeks=60)),
    ])
    def test_rejects(self, lt, lv, pf, pt):
        with pytest.raises(ScenarioError):
            validate_definition(lt, lv, pf, pt)


# --- build_cascade -------------------------------------------------------------

class TestBuildCascade:
    def test_basic_percent(self):
        r = cascade([leaf(qty=100.0)], value=10.0)
        assert len(r.targets) == 1
        t = r.targets[0]
        assert t.target_qty == 110.0
        assert t.effective_target_qty == 110.0
        assert t.scenario_ids == [5]
        assert t.flags == []
        assert r.total_forecast_qty == 100.0 and r.total_target_qty == 110.0
        assert r.leaf_count == 1

    def test_multiple_weeks_and_leaves(self):
        leaves = [leaf("C1", week=W1, qty=100.0), leaf("C1", week=W2, qty=120.0),
                  leaf("C2", "S2", week=W1, qty=200.0)]
        r = cascade(leaves, value=10.0, weeks=[W1, W2])
        got = {(t.leaf.customer, t.leaf.week): t.target_qty for t in r.targets}
        assert got == {("C1", W1): 110.0, ("C2", W1): 220.0, ("C1", W2): 132.0}
        assert r.leaf_count == 2
        assert r.total_target_qty == 462.0

    def test_walkthrough_scenario_d(self):
        lf = leaf("TEST_D_CAFE_ARDERNE", "TEST_YOG_1L_SBE", qty=57.0)
        inputs = FlagInputs(
            history={(lf.customer, lf.sku): {W0 - timedelta(weeks=9 - i): q for i, q in
                                             enumerate([30, 35, 40, 50, 55, 55, 60, 58, 60])}},
            sku_first_sale={lf.sku: date(2026, 8, 31)},   # first invoice = W-9
            pack_size={lf.sku: 1},
        )
        r = cascade([lf], value=50.0, inputs=inputs)
        t = r.targets[0]
        assert t.target_qty == 85.5
        assert t.flag_codes == [FLAG_P95, FLAG_NEW_PRODUCT]
        assert r.flagged_leaf_keys == {(lf.customer, lf.sku)}
        fl = r.flagged_leaves[0]
        assert fl.weeks == [W1] and fl.flag_codes == [FLAG_P95, FLAG_NEW_PRODUCT]

    def test_stacks_on_other_active_scenarios(self):
        lf = leaf(qty=100.0)
        r = cascade([lf], value=10.0, other={lf.key: [Effect(2, "ADD", 20.0)]}, scenario_id=5)
        # id 2 first: 100+20 = 120, then ours (+10%) = 132
        assert r.targets[0].target_qty == 132.0
        assert r.targets[0].scenario_ids == [2, 5]

    def test_own_effect_is_not_double_counted_when_previewing_active(self):
        lf = leaf(qty=100.0)
        r = cascade([lf], value=10.0, other={lf.key: [Effect(5, "MULT", 1.1)]}, scenario_id=5)
        assert r.targets[0].target_qty == 110.0

    def test_locked_rows_are_skipped_and_reported(self):
        lf = leaf()
        ex = ExistingTarget(id=9, override_qty=None, is_locked=True, scenario_ids=[1],
                            forecast_qty=100.0, forecast_id=1)
        r = cascade([lf], existing={lf.key: ex})
        assert r.targets == []
        assert r.locked_skipped == 1
        assert any("locked" in w for w in r.warnings)

    def test_existing_override_is_preserved_as_effective_target(self):
        lf = leaf(qty=100.0)
        ex = ExistingTarget(id=9, override_qty=150.0, is_locked=False, scenario_ids=[],
                            forecast_qty=100.0, forecast_id=1)
        t = cascade([lf], existing={lf.key: ex}).targets[0]
        assert t.target_qty == 110.0
        assert t.effective_target_qty == 150.0
        assert t.existing_id == 9

    def test_past_and_missing_weeks_are_warned(self):
        past = W0 - timedelta(weeks=1)
        r = cascade([leaf(week=W1)], weeks=[past, W0, W1])
        assert r.weeks == [W0, W1]
        assert any("in the past" in w for w in r.warnings)
        assert any("No active forecasts" in w and W0.isoformat() in w for w in r.warnings)

    def test_flat_history_lift_is_flagged(self):
        # Decision #6 as specified: a +10% ask on a perfectly flat leaf
        # exceeds its P95 and must be reviewed.
        lf = leaf(qty=100.0)
        inputs = stable_inputs(lf, level=100.0)
        assert cascade([lf], value=10.0, inputs=inputs).targets[0].flag_codes == [FLAG_P95]


class TestValidateReviews:
    def test_missing_reviews_listed_sorted(self):
        missing = validate_reviews({("C2", "S1"), ("C1", "S1")}, {("C2", "S1"): "ACCEPT"})
        assert missing == [("C1", "S1")]

    def test_review_keys_case_insensitive(self):
        assert validate_reviews({("C1", "S1")}, {("c1 ", "s1"): "EXCLUDE"}) == []


# --- ScenarioService.approve rules (DB stubbed) -----------------------------------

def _scenario(status="PREVIEW", scope=None) -> sc.Scenario:
    return sc.Scenario(
        id=7, name="t", description=None, scope=scope or ScenarioScope(),
        lever_type="PERCENT", lever_value=50.0, period_from=W1, period_to=W1,
        status=status, created_by="mgr", created_at=datetime(2026, 11, 1),
        approved_by=None, approved_at=None, cancel_reason=None, cancelled_at=None,
    )


def _flagged_result(*keys) -> sc.CascadeResult:
    r = sc.CascadeResult(scenario_id=7, as_of_week=W0, weeks=[W1])
    from services.forecasting.reasonableness import ReasonablenessFlag
    for c, s in keys:
        r.targets.append(sc.CascadedTarget(leaf(c, s), 150.0, [7],
                                           [ReasonablenessFlag(FLAG_P95, "m", 150.0, 100.0)]))
    return r


@pytest.fixture
def service(monkeypatch):
    svc = sc.ScenarioService(MagicMock(), "mgr")
    svc.written = []
    monkeypatch.setattr(svc, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_write_targets", lambda targets, **k: svc.written.extend(targets) or len(targets))
    svc.rebuilt = []
    monkeypatch.setattr(svc, "_rebuild", lambda weeks, as_of_week, **k: svc.rebuilt.append(list(weeks)) or (1, 0))
    return svc


class TestApprove:
    def test_requires_preview_status(self, service, monkeypatch):
        monkeypatch.setattr(service, "get", lambda *a, **k: _scenario("DRAFT"))
        with pytest.raises(ScenarioError) as e:
            service.approve(7, {})
        assert e.value.status_code == 409

    def test_unreviewed_flags_block_approval(self, service, monkeypatch):
        monkeypatch.setattr(service, "get", lambda *a, **k: _scenario())
        monkeypatch.setattr(service, "_cascade", lambda *a: _flagged_result(("C1", "S1"), ("C2", "S2")))
        with pytest.raises(ScenarioError) as e:
            service.approve(7, {("C1", "S1"): ("ACCEPT", None)})
        assert e.value.status_code == 400
        assert e.value.detail["unreviewed"] == [{"customer_account": "C2", "stock_code": "S2"}]
        assert service.written == []
        service._db.rollback.assert_called_once()

    def test_all_accepted_materialises(self, service, monkeypatch):
        monkeypatch.setattr(service, "get", lambda *a, **k: _scenario())
        monkeypatch.setattr(service, "_cascade", lambda *a: _flagged_result(("C1", "S1")))
        s, result, written = service.approve(7, {("C1", "S1"): ("ACCEPT", "known promo")})
        assert written == 1 and s.status == "ACTIVE"
        assert service.rebuilt == [[W1]]          # the whole period is re-solved and written
        service._db.commit.assert_called_once()

    def test_conflict_blocks_approval(self, service, monkeypatch):
        from services.forecasting.resolver import Conflict
        r = _flagged_result()
        r.conflicts.append(Conflict(7, W1, 500.0, 600.0, "UNITS", "inner targets add up to 600"))
        monkeypatch.setattr(service, "get", lambda *a, **k: _scenario())
        monkeypatch.setattr(service, "_cascade", lambda *a: r)
        with pytest.raises(ScenarioError) as e:
            service.approve(7, {})
        assert e.value.status_code == 409
        assert e.value.detail["conflicts"][0]["inner_total"] == 600.0
        assert service.rebuilt == []

    def test_exclude_persists_in_scope_and_recomputes(self, service, monkeypatch):
        sc_obj = _scenario()
        monkeypatch.setattr(service, "get", lambda *a, **k: sc_obj)
        calls = []

        def fake_cascade(s, as_of):
            calls.append(s.scope)
            return _flagged_result(("C1", "S1")) if len(calls) == 1 else _flagged_result()
        monkeypatch.setattr(service, "_cascade", fake_cascade)

        service.approve(7, {("C1", "S1"): ("EXCLUDE", None)})
        assert len(calls) == 2
        assert calls[1].exclude_leaves == (("C1", "S1"),)
        executed_sql = " ".join(str(c.args[0]) for c in service._db.execute.call_args_list)
        assert "cScopeJson" in executed_sql

    def test_exclusion_that_raises_new_flags_is_rejected(self, service, monkeypatch):
        monkeypatch.setattr(service, "get", lambda *a, **k: _scenario())
        results = iter([_flagged_result(("C1", "S1")), _flagged_result(("C2", "S2"))])
        monkeypatch.setattr(service, "_cascade", lambda *a: next(results))
        with pytest.raises(ScenarioError) as e:
            service.approve(7, {("C1", "S1"): ("EXCLUDE", None)})
        assert e.value.status_code == 409
        assert service.written == []


class TestWriteTargets:
    def test_batches_stay_under_sql_server_param_limit(self):
        db = MagicMock()
        svc = sc.ScenarioService(db, "mgr")
        targets = [sc.CascadedTarget(leaf(f"C{i}"), 1.0, [1], []) for i in range(250)]
        assert svc._write_targets(targets) == 250
        assert db.execute.call_count == 3
        for call in db.execute.call_args_list:
            assert len(call.args[1]) < 2100
            assert "tgt.bIsLocked = 0" in str(call.args[0])

    def test_allow_locked_drops_lock_guard(self):
        db = MagicMock()
        sc.ScenarioService(db, "mgr")._write_targets(
            [sc.CascadedTarget(leaf(), 1.0, [1], [])], allow_locked=True)
        assert "bIsLocked = 0" not in str(db.execute.call_args.args[0])


class TestGroupDecisions:
    def fl(self, c, s, *codes):
        return sc.FlaggedLeaf(c, s, list(codes), [W1], [])

    def test_group_accept_and_exclude(self):
        flagged = [self.fl("C1", "S1", "above_historical_p95"),
                   self.fl("C2", "S2", "new_product_requires_review"),
                   self.fl("C3", "S3", "above_historical_p95", "new_product_requires_review"),
                   self.fl("C4", "S4", "below_pack_size_minimum")]
        out = sc.expand_group_decisions(flagged, {}, {"above_historical_p95": "ACCEPT",
                                                      "new_product_requires_review": "EXCLUDE"})
        assert out[("C1", "S1")][0] == "ACCEPT"
        assert out[("C2", "S2")][0] == "EXCLUDE"
        assert out[("C3", "S3")][0] == "EXCLUDE"      # any excluded group excludes it
        assert ("C4", "S4") not in out                 # its group has no decision yet

    def test_individual_decision_wins(self):
        flagged = [self.fl("C1", "S1", "above_historical_p95")]
        out = sc.expand_group_decisions(flagged, {("c1", "s1"): ("EXCLUDE", "no")},
                                        {"above_historical_p95": "ACCEPT"})
        assert out == {("c1", "s1"): ("EXCLUDE", "no")}

    def test_approve_with_group_decision_only(self, service, monkeypatch):
        monkeypatch.setattr(service, "get", lambda *a, **k: _scenario())
        monkeypatch.setattr(service, "_cascade", lambda *a: _flagged_result(("C1", "S1"), ("C2", "S2")))
        s, _, _ = service.approve(7, {}, group_decisions={FLAG_P95: "ACCEPT"})
        assert s.status == "ACTIVE"

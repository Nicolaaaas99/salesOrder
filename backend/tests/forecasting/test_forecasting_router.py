"""HTTP-level tests for /api/forecasting: auth, validation, error mapping.

The ScenarioService is replaced with a fake so no database is needed.
"""

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import auth
import main
from database import get_db
from routers import forecasting as router_mod
from services.forecasting import scenarios as sc


def _scenario(status="DRAFT", **kw) -> sc.Scenario:
    base = dict(
        id=7, name="Q4 Milk push", description=None,
        scope=sc.ScenarioScope(dist_centre=("WC",), category=("Milk",)),
        lever_type="PERCENT", lever_value=5.0,
        period_from=date(2026, 11, 9), period_to=date(2026, 12, 14), status=status,
        created_by="mgr", created_at=datetime(2026, 11, 3, 9, 0),
        approved_by=None, approved_at=None, cancel_reason=None, cancelled_at=None,
    )
    base.update(kw)
    return sc.Scenario(**base)


class FakeService:
    last: "FakeService" = None
    approve_error: sc.ScenarioError = None

    def __init__(self, db, username):
        self.username = username
        self.calls = []
        FakeService.last = self

    def create(self, **kw):
        self.calls.append(("create", kw))
        sc.validate_definition(kw["lever_type"], kw["lever_value"], kw["period_from"], kw["period_to"])
        return _scenario(name=kw["name"], scope=kw["scope"])

    def get(self, sid, lock=False):
        raise sc.ScenarioError(f"Scenario {sid} not found", 404)

    def approve(self, sid, reviews, as_of=None, group_decisions=None):
        self.calls.append(("approve", reviews))
        self.group_decisions = group_decisions
        if FakeService.approve_error:
            raise FakeService.approve_error
        r = sc.CascadeResult(scenario_id=sid, as_of_week=date(2026, 11, 2), weeks=[date(2026, 11, 9)])
        return _scenario("ACTIVE"), r, 0

    override_deletes = False

    def override_target(self, target_id, override_qty, reason, break_lock=False, as_of=None):
        self.calls.append(("override", target_id, override_qty, reason))
        row = {"idScenarioTarget": target_id, "cCustomerAccount": "C1", "cStockCode": "S1",
               "dWeekStart": date(2026, 11, 9), "cRoute": "R1", "cDistCentre": "WC", "cCategory": "Milk",
               "fForecastQty": 10, "fTargetQty": 12,
               "fOverrideQty": override_qty, "cOverrideReason": reason, "cOverrideBy": "mgr",
               "cSource": "OVERRIDE" if override_qty is not None else "SCENARIO",
               "cScenariosAppliedJson": "[7]", "bIsFlagged": 0, "cFlagReasonsJson": None,
               "bIsLocked": 0}
        if override_qty is None and FakeService.override_deletes:
            row["deleted"] = True
        return row


@pytest.fixture
def client(monkeypatch):
    role = {"value": "SUPERVISOR"}
    main.app.dependency_overrides[auth.get_current_user] = lambda: "mgr"
    main.app.dependency_overrides[auth.get_current_user_role] = lambda: role["value"]
    main.app.dependency_overrides[get_db] = lambda: MagicMock()
    monkeypatch.setattr(router_mod.sc_mod, "ScenarioService", FakeService)
    FakeService.approve_error = None
    c = TestClient(main.app)
    c.role = role
    yield c
    main.app.dependency_overrides.clear()


CREATE_BODY = {
    "name": "Q4 Milk push",
    "scope": {"distCentre": ["WC"], "category": ["Milk"]},
    "lever_type": "PERCENT",
    "lever_value": 5,
    "period_from": "2026-11-09",
    "period_to": "2026-12-14",
}


def test_agents_are_forbidden(client):
    client.role["value"] = "AGENT"
    assert client.post("/api/forecasting/scenarios", json=CREATE_BODY).status_code == 403
    assert client.get("/api/forecasting/targets").status_code == 403


def test_create_passes_username_and_parsed_scope(client):
    r = client.post("/api/forecasting/scenarios", json=CREATE_BODY)
    assert r.status_code == 201, r.text
    assert FakeService.last.username == "mgr"
    kind, kw = FakeService.last.calls[0]
    assert kw["scope"] == sc.ScenarioScope(dist_centre=("WC",), category=("Milk",))
    body = r.json()
    assert body["scope"]["distCentre"] == ["WC"]
    assert body["status"] == "DRAFT"


def test_unknown_lever_type_is_422(client):
    assert client.post("/api/forecasting/scenarios",
                       json={**CREATE_BODY, "lever_type": "DOUBLE"}).status_code == 422


def test_business_rule_violation_is_400(client):
    r = client.post("/api/forecasting/scenarios", json={**CREATE_BODY, "lever_value": -150})
    assert r.status_code == 400
    assert "-100" in r.json()["detail"]


def test_missing_scenario_is_404(client):
    assert client.get("/api/forecasting/scenarios/999").status_code == 404


def test_approve_forwards_reviews(client):
    r = client.post("/api/forecasting/scenarios/7/approve", json={"reviews": [
        {"customer_account": "C1", "stock_code": "S1", "decision": "ACCEPT", "note": "promo"},
        {"customer_account": "C2", "stock_code": "S2", "decision": "EXCLUDE"},
    ]})
    assert r.status_code == 200, r.text
    _, reviews = FakeService.last.calls[0]
    assert reviews == {("C1", "S1"): ("ACCEPT", "promo"), ("C2", "S2"): ("EXCLUDE", None)}
    assert r.json()["scenario"]["status"] == "ACTIVE"


def test_approve_rejects_duplicate_reviews(client):
    review = {"customer_account": "C1", "stock_code": "S1", "decision": "ACCEPT"}
    r = client.post("/api/forecasting/scenarios/7/approve", json={"reviews": [review, review]})
    assert r.status_code == 400


def test_approve_unreviewed_flags_returns_list(client):
    FakeService.approve_error = sc.ScenarioError(
        "x", 400, {"message": "1 flagged leaf(s) have not been reviewed",
                   "unreviewed": [{"customer_account": "C2", "stock_code": "S2"}]})
    r = client.post("/api/forecasting/scenarios/7/approve", json={"reviews": []})
    assert r.status_code == 400
    assert r.json()["detail"]["unreviewed"] == [{"customer_account": "C2", "stock_code": "S2"}]


def test_override_requires_reason(client):
    r = client.patch("/api/forecasting/targets/1/override", json={"override_qty": 10})
    assert r.status_code == 422


def test_override_rejects_negative_qty(client):
    r = client.patch("/api/forecasting/targets/1/override",
                     json={"override_qty": -1, "reason": "x"})
    assert r.status_code == 422


def test_clear_override_returns_restored_row(client):
    FakeService.override_deletes = False
    r = client.patch("/api/forecasting/targets/22/override",
                     json={"override_qty": None, "reason": "Override cleared"})
    assert r.status_code == 200
    body = r.json()
    assert body["override_qty"] is None
    assert body["source"] == "SCENARIO"
    assert FakeService.last.calls[-1] == ("override", 22, None, "Override cleared")


def test_clear_override_on_orphan_row_returns_410(client):
    FakeService.override_deletes = True
    try:
        r = client.patch("/api/forecasting/targets/22/override",
                         json={"override_qty": None, "reason": "Override cleared"})
    finally:
        FakeService.override_deletes = False
    assert r.status_code == 410
    assert "follows the forecast" in r.json()["detail"]


def test_approve_passes_group_decisions(client):
    r = client.post("/api/forecasting/scenarios/7/approve",
                    json={"reviews": [], "group_decisions": {"above_historical_p95": "ACCEPT"}})
    assert r.status_code == 200
    assert FakeService.last.group_decisions == {"above_historical_p95": "ACCEPT"}


def test_rollup_deltas_per_row_for_one_week():
    W = date(2026, 11, 9)
    def t(cust, sku, route, before, after, week=W):
        lf = sc.ForecastLeaf(cust, sku, week, None, before, route=route, dist_centre="MAK", category="Milk")
        return sc.CascadedTarget(lf, after, [1], [], before_qty=before)
    targets = [t("C1", "S1", "R1", 100, 150), t("C2", "S1", "R1", 50, 40), t("C3", "S1", "R2", 10, 10),
               t("C1", "S1", "R1", 100, 999, week=date(2026, 11, 16))]
    deltas, total = router_mod.rollup_deltas(targets, "route", {"dist_centre": "mak"}, W)
    assert deltas == {"R1": 40.0} and total == 40.0
    deltas, _ = router_mod.rollup_deltas(targets, "customer", {"route": "R1"}, W, {"S1": 2.0})
    assert deltas == {"C1": 100.0, "C2": -20.0}

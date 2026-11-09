"""HTTP-level tests for /api/analytics/order-intake. The DB session is a
fake that records the SQL params and returns canned rows."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import auth
import main
from database import get_db
from routers import order_intake as mod
from services import short_cache


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return _Result(self.rows)


SUMMARY_ROW = {
    "CreatedDate": date(2026, 11, 5), "CreatedHour": 8,
    "OrderSource": "SO Assistant", "AppScreen": "ORDER_ASSISTANT",
    "UserName": "BrendaS", "CorpGroup": "Spar",
    "orders": 3, "ordered_qty": 120, "ordered_litres": 450.5, "ordered_excl": 9000.25,
    "invoiced_qty": 100, "invoiced_litres": 400, "invoiced_excl": 8000,
    "lost_qty": 15, "lost_litres": 30.5, "lost_excl": 750.5,
    "pending_qty": 5, "pending_litres": 20, "pending_excl": 249.75,
}

ORDER_ROW = {
    "OrderNum": "01.HOFSO1", "CreatedAt": datetime(2026, 11, 5, 8, 12), "CreatedHour": 8,
    "OrderSource": "Evolution", "AppScreen": None, "UserName": None,
    "CorpGroup": "Non Corporate", "CustomerAccount": "C1", "CustomerName": "Cust ",
    "DocState": 1, "OrderedQty": 10, "OrderedLitres": 20, "OrderedExclAmnt": 300,
    "InvoicedQty": 0, "InvoicedLitres": 0, "InvoicedExclAmnt": None,
    "LostQty": 0, "LostLitres": 0, "LostExclAmnt": None,
}


@pytest.fixture
def make_client():
    def _make(rows, role="SUPERVISOR"):
        db = FakeDB(rows)
        main.app.dependency_overrides[auth.get_current_user] = lambda: "rep"
        main.app.dependency_overrides[auth.get_current_user_role] = lambda: role
        main.app.dependency_overrides[get_db] = lambda: db
        short_cache.invalidate(mod.CACHE_NS)
        return TestClient(main.app), db
    yield _make
    main.app.dependency_overrides.clear()
    short_cache.invalidate(mod.CACHE_NS)


@pytest.mark.parametrize("path", [
    "/api/analytics/order-intake",
    "/api/analytics/order-intake/orders",
    "/api/analytics/order-intake/export",
])
def test_agents_are_refused(make_client, path):
    client, db = make_client([SUMMARY_ROW], role="AGENT")
    r = client.get(path, params={"from": "2026-11-05", "to": "2026-11-05"})
    assert r.status_code == 403
    assert db.calls == []


@pytest.mark.parametrize("role", ["SUPERVISOR", "ADMIN"])
def test_supervisors_and_admins_allowed(make_client, role):
    client, _ = make_client([SUMMARY_ROW], role=role)
    r = client.get("/api/analytics/order-intake", params={"from": "2026-11-05", "to": "2026-11-05"})
    assert r.status_code == 200


def test_summary_maps_rows(make_client):
    client, db = make_client([SUMMARY_ROW])
    r = client.get("/api/analytics/order-intake", params={"from": "2026-11-05", "to": "2026-11-05"})
    assert r.status_code == 200
    body = r.json()
    assert body["from_date"] == "2026-11-05"
    row = body["rows"][0]
    assert row["hour"] == 8 and row["source"] == "SO Assistant" and row["user"] == "BrendaS"
    assert row["orders"] == 3 and row["ordered_litres"] == 450.5
    assert row["lost_qty"] == 15 and row["lost_excl"] == 750.5
    assert row["pending_litres"] == 20 and row["pending_excl"] == 249.75
    assert db.calls[0][1] == {"from_date": date(2026, 11, 5), "to_date": date(2026, 11, 5)}


def test_summary_is_cached_per_range(make_client):
    client, db = make_client([SUMMARY_ROW])
    params = {"from": "2026-11-05", "to": "2026-11-05"}
    client.get("/api/analytics/order-intake", params=params)
    client.get("/api/analytics/order-intake", params=params)
    assert len(db.calls) == 1
    client.get("/api/analytics/order-intake", params={"from": "2026-11-04", "to": "2026-11-05"})
    assert len(db.calls) == 2


def test_reversed_range_is_swapped(make_client):
    client, db = make_client([])
    r = client.get("/api/analytics/order-intake", params={"from": "2026-11-05", "to": "2026-11-01"})
    assert r.status_code == 200
    assert db.calls[0][1] == {"from_date": date(2026, 11, 1), "to_date": date(2026, 11, 5)}


def test_range_over_cap_is_rejected(make_client):
    client, db = make_client([])
    frm = date(2026, 10, 1)
    to = frm + timedelta(days=mod.MAX_RANGE_DAYS)  # one day too many
    r = client.get("/api/analytics/order-intake", params={"from": frm.isoformat(), "to": to.isoformat()})
    assert r.status_code == 400
    assert db.calls == []


def test_defaults_to_today():
    assert mod.resolve_range(None, None) == (date.today(), date.today())


def test_orders_drill_passes_filters_and_defaults_user(make_client):
    client, db = make_client([ORDER_ROW])
    r = client.get(
        "/api/analytics/order-intake/orders",
        params={"from": "2026-11-05", "to": "2026-11-05", "hour": 8,
                "source": "Evolution", "user": "", "corp_group": "Non Corporate"},
    )
    assert r.status_code == 200
    params = db.calls[0][1]
    assert params["hour"] == 8 and params["source"] == "Evolution"
    assert params["user_name"] is None and params["corp_group"] == "Non Corporate"
    o = r.json()[0]
    assert o["user"] == "Evolution" and o["customer_name"] == "Cust"
    assert o["invoiced_excl"] == 0.0
    assert o["lost_excl"] == 0.0
    # DocState 1 = unprocessed: the whole order is pending.
    assert o["pending_qty"] == 10 and o["pending_excl"] == 300


def test_orders_rejects_bad_hour(make_client):
    client, _ = make_client([])
    r = client.get("/api/analytics/order-intake/orders", params={"hour": 24})
    assert r.status_code == 422


def test_export_returns_xlsx(make_client):
    client, _ = make_client([ORDER_ROW])
    r = client.get("/api/analytics/order-intake/export", params={"from": "2026-11-05"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert 'order-intake-2026-11-05.xlsx' in r.headers["content-disposition"]

"""HTTP-level tests for the Deliveries report endpoints (/api/history/
deliveries and the export). The DB session is a fake that answers each
query by what it selects."""
from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import auth
import main
from database import get_db
from routers import history as mod
from services import short_cache


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


CUSTOMERS = [
    {"route": "R101 ", "account": "C1 ", "customer_name": "Alpha "},
    {"route": "R101", "account": "C2", "customer_name": "Beta"},
    {"route": "R202", "account": "C3", "customer_name": "Gamma"},
]
D = date(2026, 11, 5)
TOTALS = [
    # C1: one order on the day.
    {"account": "C1", "customer_name": "Alpha", "route": "R101", "tx_date": D,
     "order_count": 1, "litres": 120.5},
    # C9 is on hold / has no route: not in the customer master.
    {"account": "C9", "customer_name": "Held Co", "route": None, "tx_date": D,
     "order_count": 2, "litres": 40},
]


class FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "WITH picked" in sql:
            return _Result(TOTALS)
        if "_tb_SOCustomerContactLog" in sql:
            return _Result([])
        return _Result(CUSTOMERS)


@pytest.fixture
def client():
    db = FakeDB()
    main.app.dependency_overrides[auth.get_current_user] = lambda: "rep"
    main.app.dependency_overrides[auth.get_current_user_role] = lambda: "SUPERVISOR"
    main.app.dependency_overrides[get_db] = lambda: db
    short_cache.invalidate(mod.ORDERS_TODAY_CACHE)
    yield TestClient(main.app), db
    main.app.dependency_overrides.clear()
    short_cache.invalidate(mod.ORDERS_TODAY_CACHE)


def test_deliveries_lists_every_customer_with_orders_and_litres(client):
    c, db = client
    r = c.get("/api/history/deliveries", params={"from": "2026-11-05", "to": "2026-11-05"})
    assert r.status_code == 200
    rows = {x["account"]: x for x in r.json()}
    assert set(rows) == {"C1", "C2", "C3", "C9"}
    assert rows["C1"] == {"route": "R101", "account": "C1", "customer_name": "Alpha",
                          "order_count": 1, "litres": 120.5}
    assert rows["C2"]["order_count"] == 0 and rows["C2"]["litres"] == 0
    # Not in the routed master list but has orders -> still shown.
    assert rows["C9"]["route"] == "No route" and rows["C9"]["order_count"] == 2
    totals_sql, params = next(x for x in db.calls if "WITH picked" in x[0])
    assert "NUM.DeliveryDate >= :from_date" in totals_sql and "FORCESEEK" in totals_sql
    assert params == {"from_date": D, "to_exclusive": date(2026, 11, 6)}


def test_deliveries_is_cached_per_range(client):
    c, db = client
    c.get("/api/history/deliveries", params={"from": "2026-11-05", "to": "2026-11-05"})
    n = len(db.calls)
    c.get("/api/history/deliveries", params={"from": "2026-11-05", "to": "2026-11-05"})
    assert len(db.calls) == n
    # An order write clears the namespace -> next call re-queries.
    short_cache.invalidate("orders_today")
    c.get("/api/history/deliveries", params={"from": "2026-11-05", "to": "2026-11-05"})
    assert len(db.calls) > n


def test_export_has_litres_column(client):
    c, _ = client
    r = c.get("/api/history/export-orders-per-day",
              params={"from": "2026-11-05", "to": "2026-11-05", "by": "delivery"})
    assert r.status_code == 200
    ws = load_workbook(BytesIO(r.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("DeliveryDate", "Route", "CustomerAccount", "CustomerName",
                       "NoOfOrders", "Litres", "Reason")
    by_acc = {row[2]: row for row in rows[1:]}
    assert by_acc["C1"][4:] == (1, 120.5, "PLACED")
    assert by_acc["C2"][4:] == (0, 0, "NOT_CONTACTED")
    assert by_acc["C9"][1] == "No route" and by_acc["C9"][4] == 2

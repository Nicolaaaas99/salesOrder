"""Who may capture an order from which warehouse (services/dc_warehouse.py)
and who may change their own default warehouse (/auth/default-warehouse)."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import auth
import main
from database import get_db
from services import dc_warehouse as dw


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


DC_ROWS = [{"WhseLink": 48, "Code": "01.HOF_POL"}, {"WhseLink": 49, "Code": "01.HOF_MAK"}]


class FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "WhseLink IN (48, 49)" in sql:
            return _Result(DC_ROWS)
        if "FROM dbo._rtblAgents" in sql:
            return _Result([{"idAgents": 7}])
        return _Result([])

    def commit(self):
        pass


# ---- the order-line guard ---------------------------------------------------

def test_agent_lines_must_be_on_their_own_warehouse():
    dw.check_order_line_warehouses(FakeDB(), "AGENT", "01.HOF_MAK", ["01.hof_mak ", "01.HOF_MAK"])
    with pytest.raises(HTTPException) as e:
        dw.check_order_line_warehouses(FakeDB(), "AGENT", "01.HOF_MAK", ["01.HOF_MAK", "01.HOF_POL"])
    assert e.value.status_code == 400 and "01.HOF_POL" in e.value.detail


def test_agent_without_a_warehouse_is_told_to_ask_an_admin():
    with pytest.raises(HTTPException) as e:
        dw.check_order_line_warehouses(FakeDB(), "AGENT", None, ["01.HOF_MAK"])
    assert "admin" in e.value.detail


@pytest.mark.parametrize("role", ["SUPERVISOR", "ADMIN"])
def test_supervisors_may_use_either_dc_but_nothing_else(role):
    dw.check_order_line_warehouses(FakeDB(), role, "01.HOF_MAK", ["01.HOF_POL", "01.HOF_MAK"])
    with pytest.raises(HTTPException):
        dw.check_order_line_warehouses(FakeDB(), role, "01.HOF_MAK", ["01.HOF_MAN1"])


def test_lines_without_a_warehouse_are_left_alone():
    dw.check_order_line_warehouses(FakeDB(), "AGENT", None, [None, ""])


def test_customer_card_dc_maps_to_the_dc_warehouse():
    assert dw.warehouse_for_dist_centre(" polokwane ") == "01.HOF_POL"
    assert dw.warehouse_for_dist_centre("Makhado") == "01.HOF_MAK"
    # Blank / unknown values on the card give no warning.
    for v in (None, "", "1", "Louis Trichardt", "Polokwane Depo"):
        assert dw.warehouse_for_dist_centre(v) is None


# ---- changing your own default warehouse ------------------------------------

@pytest.fixture
def client_as():
    def _make(role):
        db = FakeDB()
        main.app.dependency_overrides[auth.get_current_user] = lambda: "rep"
        main.app.dependency_overrides[auth.get_current_user_role] = lambda: role
        main.app.dependency_overrides[get_db] = lambda: db
        return TestClient(main.app), db
    yield _make
    main.app.dependency_overrides.clear()


def test_agent_cannot_change_own_warehouse(client_as):
    c, db = client_as("AGENT")
    r = c.post("/api/auth/default-warehouse", json={"code": "01.HOF_POL"})
    assert r.status_code == 403
    assert not any("UPDATE" in sql for sql, _ in db.calls)


@pytest.mark.parametrize("role", ["SUPERVISOR", "ADMIN"])
def test_supervisor_switches_to_a_dc_only(client_as, role):
    c, db = client_as(role)
    r = c.post("/api/auth/default-warehouse", json={"code": "01.HOF_POL"})
    assert r.status_code == 200 and r.json()["default_warehouse_code"] == "01.HOF_POL"
    upd = [p for sql, p in db.calls if "UPDATE dbo._rtblAgents" in sql]
    assert upd == [{"whse_id": 48, "agent_id": 7}]
    r = c.post("/api/auth/default-warehouse", json={"code": "01.HOF_MAN1"})
    assert r.status_code == 400


# ---- fixing a customer card's DC from the mismatch banner -------------------

class CustomerDB(FakeDB):
    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "prev_dc" in sql:
            return _Result([{"dclink": 11, "prev_dc": "Polokwane", "route": "R1", "telesale": None,
                             "rep_id": 3, "rep_code": "V", "on_hold": 0}])
        if "_vw_SOCustomerDetail" in sql:
            return _Result([{"Account": "C1", "Name": "Cust", "DistCentre": "Makhado"}])
        return _Result([])

    def rollback(self):
        pass


@pytest.fixture
def customer_client():
    def _make(role):
        db = CustomerDB()
        main.app.dependency_overrides[auth.get_current_user] = lambda: "sup"
        main.app.dependency_overrides[auth.get_current_user_role] = lambda: role
        main.app.dependency_overrides[get_db] = lambda: db
        return TestClient(main.app), db
    yield _make
    main.app.dependency_overrides.clear()


def test_supervisor_fixes_customer_dc_with_audit(customer_client):
    c, db = customer_client("SUPERVISOR")
    r = c.patch("/api/lookups/customers/C1/dist-centre", json={"warehouse_code": "01.hof_mak"})
    assert r.status_code == 200
    assert r.json()["dist_centre"] == "Makhado" and r.json()["dc_warehouse_code"] == "01.HOF_MAK"
    audit = next(p for sql, p in db.calls if "INSERT INTO [SOAssistant].dbo._tb_SOCustomerAssignmentAudit" in sql)
    assert audit["prev_dc"] == "Polokwane" and audit["new_dc"] == "Makhado" and audit["username"] == "sup"
    upd = next(p for sql, p in db.calls if "UPDATE dbo.Client SET ulARDistCentre" in sql)
    assert upd == {"dc": "Makhado", "dclink": 11}


def test_agent_cannot_fix_customer_dc(customer_client):
    c, db = customer_client("AGENT")
    r = c.patch("/api/lookups/customers/C1/dist-centre", json={"warehouse_code": "01.HOF_MAK"})
    assert r.status_code == 403
    assert not any("UPDATE" in sql for sql, _ in db.calls)


def test_fix_customer_dc_only_to_a_dc(customer_client):
    c, _ = customer_client("ADMIN")
    r = c.patch("/api/lookups/customers/C1/dist-centre", json={"warehouse_code": "01.HOF_MAN1"})
    assert r.status_code == 400


# ---- a new order must come from the customer's own DC -----------------------

class CardDB(FakeDB):
    def __init__(self, dc):
        super().__init__()
        self.dc = dc

    def execute(self, stmt, params=None):
        if "ulARDistCentre FROM dbo.Client" in str(stmt):
            return _Result([{"Name": "Spar X", "ulARDistCentre": self.dc}])
        return super().execute(stmt, params)


def test_order_from_the_other_dc_is_blocked():
    with pytest.raises(HTTPException) as e:
        dw.check_customer_dc(CardDB("Polokwane"), "C1", ["01.HOF_MAK", "01.HOF_MAK"])
    assert e.value.status_code == 400 and "01.HOF_POL" in e.value.detail and "Spar X" in e.value.detail


def test_order_from_the_customers_dc_passes():
    dw.check_customer_dc(CardDB("Polokwane"), "C1", ["01.hof_pol", None])


@pytest.mark.parametrize("dc", [None, "", "1", "Louis Trichardt"])
def test_customers_without_a_known_dc_are_not_blocked(dc):
    dw.check_customer_dc(CardDB(dc), "C1", ["01.HOF_MAK"])

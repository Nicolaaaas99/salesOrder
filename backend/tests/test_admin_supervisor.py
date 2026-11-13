"""Users & Roles for supervisors (2026-11-12): they see registered non-admin
users, may only change an AGENT's warehouse, and see who is online."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import auth
import main
from database import get_db


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def _user(agent_id, name, role, app_user_id=None, whse="01.HOF_MAK"):
    return {
        "agent_id": agent_id, "agent_name": name, "agent_description": "",
        "app_user_id": app_user_id, "role": role, "is_active": True if app_user_id else None,
        "registered_at": datetime(2026, 11, 1) if app_user_id else None,
        "registered_by": "x" if app_user_id else None, "default_warehouse_code": whse,
    }


USERS = [
    _user(1, "Brenda", "AGENT", 11),
    _user(2, "Sam", "SUPERVISOR", 12),
    _user(3, "NJ", "ADMIN", 13),
    _user(4, "NewGuy", None),                 # not registered
]


class FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "WhseLink IN (48, 49)" in sql:
            return _Result([{"WhseLink": 48, "Code": "01.HOF_POL"}, {"WhseLink": 49, "Code": "01.HOF_MAK"}])
        if "u.cRole = 'ADMIN'" in sql:
            return _Result([{"cAgentName": "NJ"}])
        if "WHERE u.idAppUser = :id" in sql:
            return _Result([u for u in USERS if u["app_user_id"] == params["id"]])
        if "g.bAgentActive = 1" in sql and "ORDER BY g.cAgentName" in sql:
            return _Result(USERS)
        return _Result([])

    def commit(self):
        pass


@pytest.fixture
def client_as():
    def _make(role, user="sup"):
        db = FakeDB()
        main.app.dependency_overrides[auth.get_current_user] = lambda: user
        main.app.dependency_overrides[auth.get_current_user_role] = lambda: role
        main.app.dependency_overrides[get_db] = lambda: db
        return TestClient(main.app), db
    yield _make
    main.app.dependency_overrides.clear()


def test_agent_cannot_open_users(client_as):
    c, _ = client_as("AGENT")
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/presence").status_code == 403


def test_supervisor_sees_registered_non_admins_only(client_as):
    c, _ = client_as("SUPERVISOR")
    names = [u["agent_name"] for u in c.get("/api/admin/users").json()]
    assert names == ["Brenda", "Sam"]


def test_admin_sees_everyone(client_as):
    c, _ = client_as("ADMIN")
    assert len(c.get("/api/admin/users").json()) == 4


def test_supervisor_can_only_change_an_agents_warehouse(client_as):
    c, db = client_as("SUPERVISOR")
    r = c.patch("/api/admin/users/11", json={"default_warehouse_code": "01.HOF_POL"})
    assert r.status_code == 200
    assert any("SET iDefWhseId" in sql for sql, _ in db.calls)
    # Not another supervisor's, not roles, not activation, not adding.
    assert c.patch("/api/admin/users/12", json={"default_warehouse_code": "01.HOF_POL"}).status_code == 403
    assert c.patch("/api/admin/users/11", json={"role": "SUPERVISOR"}).status_code == 403
    assert c.patch("/api/admin/users/11", json={"is_active": False}).status_code == 403
    assert c.post("/api/admin/users", json={"agent_id": 4, "role": "AGENT"}).status_code == 403


def test_presence_online_and_hides_admins_from_supervisors(client_as):
    t1 = auth.issue_token("Brenda")
    t2 = auth.issue_token("NJ")
    try:
        c, _ = client_as("SUPERVISOR")
        people = {p["username"]: p for p in c.get("/api/admin/presence").json()}
        assert "Brenda" in people and people["Brenda"]["online"] is True
        assert "NJ" not in people
        c, _ = client_as("ADMIN", user="NJ")
        assert "NJ" in {p["username"] for p in c.get("/api/admin/presence").json()}
    finally:
        auth.revoke_token(t1)
        auth.revoke_token(t2)


def test_logout_removes_presence():
    t = auth.issue_token("Ghost")
    assert "ghost" in auth.presence()
    auth.revoke_token(t)
    assert "ghost" not in auth.presence()


def test_heartbeat_never_touches_the_database():
    """PERFORMANCE.md: the heartbeat runs every 60 s for every open app, so
    it must stay memory-only. Fails if anything on its path queries SQL."""
    calls = []

    class Spy:
        def execute(self, stmt, params=None):
            calls.append(str(stmt))
            raise AssertionError("heartbeat queried the database")

    main.app.dependency_overrides[get_db] = lambda: Spy()
    t = auth.issue_token("Beat")
    try:
        r = TestClient(main.app).post("/api/auth/heartbeat", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 200 and calls == []
        assert auth.presence()["beat"]["online"] is True
    finally:
        auth.revoke_token(t)
        main.app.dependency_overrides.clear()

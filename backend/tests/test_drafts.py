"""Draft orders: router scoping / errors, and the order deleting its draft.
The SQL itself is checked live on dev in a rolled-back transaction."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import auth
import main
from database import get_db
from routers import drafts as drafts_router
from services import drafts as svc


class _Db:
    def __init__(self):
        self.committed = self.rolled_back = 0

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


@pytest.fixture
def db():
    return _Db()


@pytest.fixture
def client(db, monkeypatch):
    # The customer-DC check is tested in test_dc_warehouse.py; here the
    # router is kept away from the database.
    monkeypatch.setattr(drafts_router, "check_customer_dc", lambda *a, **k: None)
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[auth.get_current_user] = lambda: "alice"
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _summary(**kw):
    base = dict(
        id=5, source="ORDER_ASSISTANT", customer_account="C01", customer_name="Cafe",
        line_count=2, total_incl=10.0, created_at=datetime(2026, 11, 10, 9),
        updated_at=datetime(2026, 11, 10, 9), expires_on=date(2026, 11, 13), stale=True,
    )
    base.update(kw)
    return svc.DraftSummary(**base)


BODY = {"source": "SALES_ORDER_ENTRY", "customer_account": "C01", "line_count": 1,
        "payload": {"lines": []}}


def test_list_is_for_the_signed_in_agent(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(svc, "list_drafts", lambda db, u: seen.setdefault("u", u) and [_summary()])
    r = client.get("/api/drafts")
    assert r.status_code == 200 and seen["u"] == "alice"
    assert r.json()[0]["stale"] is True and r.json()[0]["expires_on"] == "2026-11-13"


def test_save_conflict_returns_the_existing_draft(client, db, monkeypatch):
    def save(db_, req, u):
        raise svc.DraftConflict(_summary(id=9))
    monkeypatch.setattr(svc, "save_draft", save)
    r = client.post("/api/drafts", json=BODY)
    assert r.status_code == 409
    assert r.json()["detail"]["existing"]["id"] == 9 and db.rolled_back


def test_save_without_the_table_says_what_to_run(client, monkeypatch):
    def save(db_, req, u):
        raise RuntimeError("Invalid object name '_tb_SODraftOrder'")
    monkeypatch.setattr(svc, "save_draft", save)
    r = client.post("/api/drafts", json=BODY)
    assert r.status_code == 503 and "create_views.sql" in r.json()["detail"]


def test_only_known_sources(client):
    assert client.post("/api/drafts", json={**BODY, "source": "ORDER_IMPORT"}).status_code == 422


def test_get_someone_elses_draft_is_404(client, monkeypatch):
    monkeypatch.setattr(svc, "get_draft", lambda db, i, u: None)
    assert client.get("/api/drafts/5").status_code == 404


def test_delete_is_scoped_and_committed(client, db, monkeypatch):
    seen = {}
    monkeypatch.setattr(svc, "delete_draft", lambda db_, i, u: seen.update(i=i, u=u) or True)
    r = client.delete("/api/drafts/5")
    assert r.json() == {"deleted": True} and seen == {"i": 5, "u": "alice"} and db.committed


def test_delete_after_order_never_fails_the_order(db, monkeypatch):
    def boom(*a):
        raise RuntimeError("table missing")
    monkeypatch.setattr(svc, "delete_draft", boom)
    svc.delete_after_order(db, 5, "alice")          # no exception
    assert db.rolled_back
    svc.delete_after_order(db, None, "alice")       # nothing to do


def test_purge_keeps_drafts_for_three_days():
    """Saved on the 11th: deleted from the 14th -> rows before the start of
    (today - 2) go."""
    calls = {}

    class Db:
        def execute(self, stmt, params):
            calls["sql"], calls["p"] = str(stmt), params

            class R:
                rowcount = 0
            return R()
    svc.purge_expired(Db())
    assert calls["p"] == {"keep": -2} and "dtCreatedAt <" in calls["sql"]


def test_no_draft_while_the_customer_dc_does_not_match(client, monkeypatch):
    from fastapi import HTTPException

    seen = {}

    def block(db, account, whses, action="placing the order"):
        seen["args"] = (account, list(whses), action)
        raise HTTPException(400, "C01 is on DC Polokwane (01.HOF_POL) ...")

    monkeypatch.setattr(drafts_router, "check_customer_dc", block)
    monkeypatch.setattr(svc, "save_draft", lambda *a: pytest.fail("draft must not be saved"))
    body = {**BODY, "payload": {"lines": [{"stock_code": "A", "warehouse_code": "01.HOF_MAK"}]}}
    r = client.post("/api/drafts", json=body)
    assert r.status_code == 400
    assert seen["args"] == ("C01", ["01.HOF_MAK"], "saving the draft")

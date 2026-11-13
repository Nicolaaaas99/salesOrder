"""Item search (2026-11-13): an exact stock code must come first, so the
item picker never commits a different code (typing 012 added 001)."""
from fastapi.testclient import TestClient

import auth
import main
from database import get_db


class _Result:
    def mappings(self):
        return self

    def all(self):
        return []


class FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return _Result()


def test_exact_code_first_then_prefix():
    db = FakeDB()
    main.app.dependency_overrides[get_db] = lambda: db
    main.app.dependency_overrides[auth.get_current_user] = lambda: "x"
    try:
        r = TestClient(main.app).get("/api/lookups/inventory", params={"q": " 012 "})
        assert r.status_code == 200
        sql, params = db.calls[-1]
        assert "ORDER BY CASE WHEN StockCode = :exact THEN 0" in sql
        assert "WHEN StockCode LIKE :prefix THEN 1" in sql
        assert params["exact"] == "012" and params["prefix"] == "012%" and params["like"] == "%012%"
        # Empty search: plain list, no exact/prefix.
        TestClient(main.app).get("/api/lookups/inventory")
        _, params = db.calls[-1]
        assert params["exact"] is None and params["prefix"] is None
    finally:
        main.app.dependency_overrides.clear()

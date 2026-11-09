"""Live-DB smoke test of the Targets screen flow through the HTTP API
(2026-11 rethink, Phase D): exactly the calls TargetsScreen + SetTargetPanel make.

    1. GET  /workbench (route level inside <DC>, litres) -> current DC target
    2. POST /scenarios  (DC weekly total = target x 1.05, LITRES, anchor dist_centre)
    3. POST /scenarios/{id}/preview?rollup_level=route&dist_centre=<DC>&measure=litres
       -> the Proposed column (per-route deltas) adds up to the DC's change
    4. POST /scenarios/{id}/approve with group decisions only (Accept every warning group)
    5. GET  /workbench again -> DC total = the new target (in litres)
    6. POST /scenarios/{id}/cancel -> back to where we started

Runs as `smoke_test`, cleans up. Usage:
    .venv\\Scripts\\python.exe scripts\\run_targets_screen_smoke_test.py --dc Makhado --week 2026-11-16
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import main  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}  {detail}")


def main_() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dc", default="Makhado")
    ap.add_argument("--week", required=True)
    args = ap.parse_args()

    main.app.dependency_overrides[auth.get_current_user] = lambda: "smoke_test"
    main.app.dependency_overrides[auth.get_current_user_role] = lambda: "ADMIN"
    c = TestClient(main.app)
    wb_params = {"week": args.week, "level": "route", "measure": "litres", "dist_centre": args.dc}

    before = c.get("/api/forecasting/workbench", params=wb_params).json()
    dc_target = before["total"]["target"]
    goal = round(dc_target * 1.05, 2)
    print(f"\nTargets screen smoke -- {args.dc}, week {before['week_start']}: target {dc_target:,.1f} L -> {goal:,.1f} L\n")

    r = c.post("/api/forecasting/scenarios", json={
        "name": f"SMOKE D: {args.dc} = {goal} L", "scope": {"distCentre": [args.dc]},
        "lever_type": "FIXED_QTY", "lever_value": goal, "measure": "LITRES",
        "anchor_level": "dist_centre", "period_from": before["week_start"], "period_to": before["week_start"],
    })
    check("create", r.status_code in (200, 201), str(r.status_code))
    sid = r.json()["id"]
    try:
        t0 = time.time()
        p = c.post(f"/api/forecasting/scenarios/{sid}/preview", params={
            "limit": 0, "rollup_level": "route", "rollup_week": before["week_start"],
            "measure": "litres", "dist_centre": args.dc}).json()
        print(f"  preview {time.time() - t0:.1f}s: scope delta {p['rollup_scope_delta']:,.1f} L, "
              f"{len(p['rollup_deltas'])} routes change, {len(p['flagged_leaves'])} warnings, "
              f"{len(p['conflicts'])} conflicts, targets page {len(p['targets'])}")
        check("Proposed column adds up to the DC change",
              abs(sum(p["rollup_deltas"].values()) - p["rollup_scope_delta"]) < 0.05)
        check("DC change = goal - current target", abs(p["rollup_scope_delta"] - (goal - dc_target)) < 0.5,
              f"{p['rollup_scope_delta']:,.2f} vs {goal - dc_target:,.2f}")
        check("no target rows shipped to the browser", len(p["targets"]) == 0)
        codes = sorted({code for f in p["flagged_leaves"] for code in f["flag_codes"]})
        t0 = time.time()
        a = c.post(f"/api/forecasting/scenarios/{sid}/approve",
                   json={"reviews": [], "group_decisions": {code: "ACCEPT" for code in codes}})
        check("approve with group decisions only", a.status_code == 200,
              f"{a.status_code} {time.time() - t0:.1f}s groups {codes}")
        after = c.get("/api/forecasting/workbench", params=wb_params).json()
        check("workbench DC total = new target", abs(after["total"]["target"] - goal) < 0.5,
              f"{after['total']['target']:,.2f} vs {goal:,.2f}")
    finally:
        x = c.post(f"/api/forecasting/scenarios/{sid}/cancel", json={"reason": "smoke test cleanup"})
        check("cancel", x.status_code == 200, str(x.json().get("targets_deleted")) + " rows removed")
        back = c.get("/api/forecasting/workbench", params=wb_params).json()
        check("back to the starting target", abs(back["total"]["target"] - dc_target) < 0.5,
              f"{back['total']['target']:,.2f}")
    print(f"\nRESULT: {'PASS' if ok_all else 'FAIL'}\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main_())

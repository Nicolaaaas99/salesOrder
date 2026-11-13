"""Which warehouse an order is captured from — the rules in one place.

Everyone works out of one of the two distribution centres (WhseLink 48
01.HOF_POL, 49 01.HOF_MAK). The person's default warehouse lives on their
Evolution agent record (_rtblAgents.iDefWhseId) and every new order line
uses it (pricing is scoped to it too).

  AGENT              — fixed. Only an ADMIN sets it (Users & Roles).
  SUPERVISOR / ADMIN — may switch themselves between the DCs at any time.

A customer's own DC is the Dist Centre on their customer card
(Client.ulARDistCentre). When it maps to the other DC than the one the
order is captured from, new orders and drafts are BLOCKED (2026-11-12)
until a supervisor switches warehouse or fixes the customer card.
"""
from typing import Iterable, Optional, Set

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

# The DC warehouses (WhseLink). Keep in step with _vw_SOAgentDefaultWhse
# and the @dcs list in _fn_SOOrderIntake.
DC_WHSE_LINKS = (48, 49)

# Client.ulARDistCentre (upper-cased) -> the DC's warehouse code.
CUSTOMER_DC_WAREHOUSE = {
    "POLOKWANE": "01.HOF_POL",
    "MAKHADO": "01.HOF_MAK",
}

# The Dist Centre value written to a customer card for each DC warehouse
# (the "fix this customer's DC" action). Same spelling as the cards.
WAREHOUSE_DIST_CENTRE = {"01.HOF_POL": "Polokwane", "01.HOF_MAK": "Makhado"}

ROLES_THAT_CHOOSE_WAREHOUSE = ("SUPERVISOR", "ADMIN")


def can_choose_own_warehouse(role: Optional[str]) -> bool:
    return (role or "").upper() in ROLES_THAT_CHOOSE_WAREHOUSE


def warehouse_for_dist_centre(dist_centre: Optional[str]) -> Optional[str]:
    """The DC warehouse a customer card's Dist Centre points at, or None
    when it's blank / not one of the DCs (e.g. a typo on the card)."""
    return CUSTOMER_DC_WAREHOUSE.get((dist_centre or "").strip().upper())


def dc_warehouses(db: Session) -> dict:
    """{CODE: WhseLink} for the DC warehouses."""
    rows = db.execute(
        text(
            "SELECT WhseLink, Code FROM [SOAssistant].dbo._vw_SOWarehouses "
            "WHERE WhseLink IN (48, 49)"
        )
    ).mappings().all()
    return {(r["Code"] or "").strip().upper(): int(r["WhseLink"]) for r in rows}


def _norm(code: Optional[str]) -> str:
    return (code or "").strip().upper()


def check_order_line_warehouses(
    db: Session,
    role: Optional[str],
    default_warehouse: Optional[str],
    line_warehouses: Iterable[Optional[str]],
) -> None:
    """Server-side guard on a NEW order (the screens already set it right;
    this stops a stale or hand-crafted request). Raises 400.

    AGENT: every line must be on their own default warehouse.
    SUPERVISOR / ADMIN: every line must be on a DC warehouse.
    Lines without a warehouse are left to Evolution's default."""
    codes: Set[str] = {_norm(w) for w in line_warehouses if _norm(w)}
    if not codes:
        return
    if can_choose_own_warehouse(role):
        allowed = set(dc_warehouses(db))
        bad = sorted(codes - allowed)
        if bad:
            raise HTTPException(
                400,
                f"Lines must come from one of the DC warehouses ({', '.join(sorted(allowed))}); "
                f"this order has {', '.join(bad)}.",
            )
        return
    own = _norm(default_warehouse)
    if not own:
        raise HTTPException(
            400, "You don't have a warehouse set yet. Ask an admin to set it on Users & Roles."
        )
    bad = sorted(codes - {own})
    if bad:
        raise HTTPException(
            400,
            f"Every line must come from your warehouse {own}; this order has {', '.join(bad)}. "
            "Only a supervisor can capture orders from another warehouse.",
        )


def check_customer_dc(
    db: Session,
    customer_account: str,
    line_warehouses: Iterable[Optional[str]],
    action: str = "placing the order",
) -> None:
    """Block a NEW order whose lines come from a different DC than the one
    on the customer's card (2026-11-12: warn-only was not enough — orders
    were placed without fixing it). Resolve it first: switch to the
    customer's DC, or (supervisor) fix the customer's card. Customers with a
    blank / unknown DC on their card are not checked. Raises 400."""
    row = db.execute(
        text("SELECT Name, ulARDistCentre FROM dbo.Client WHERE Account = :a"),
        {"a": customer_account},
    ).mappings().first()
    if not row:
        return
    theirs = warehouse_for_dist_centre(row["ulARDistCentre"])
    if not theirs:
        return
    wrong = sorted({_norm(w) for w in line_warehouses if _norm(w) and _norm(w) != theirs})
    if wrong:
        raise HTTPException(
            400,
            f"{(row['Name'] or customer_account).strip()} is on DC {(row['ulARDistCentre'] or '').strip()} "
            f"({theirs}) but this order is captured from {', '.join(wrong)}. Switch to {theirs}, "
            f"or have a supervisor fix the customer's DC, before {action}.",
        )

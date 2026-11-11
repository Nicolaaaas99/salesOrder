from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

import schemas

# Orders are saved in tax-INCLUSIVE mode: the line's unit price is the incl
# price and Evolution charges qty x incl exactly (verified on 26k inclusive
# Evolution lines, 2026-11-09). The incl comes from the price resolver
# (price list's stored incl, or the deal's incl), which can differ from
# excl + VAT by a cent — that cent is how Evolution itself charges in
# inclusive mode, so we keep it.
#
# A bigger gap means the stored incl doesn't belong to this line's tax rate:
# e.g. a 0%-VAT (exempt/export) customer, whose line has no VAT but whose
# price list incl includes 15%. Using it would overcharge by the full VAT, so
# in that case the incl is recomputed from the excl, exactly what the
# exclusive save charged before.
INCL_TRUST_TOLERANCE = 0.02

# Mirrored in frontend/src/components/LineItemsGrid.tsx (inclUnitPrice).


def cents(x: float) -> float:
    """Round half-up to the cent (not banker's rounding), absorbing float noise."""
    return float(Decimal(str(round(x, 6))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def inclusive_unit_price(excl: float, incl: Optional[float], tax_rate: float) -> float:
    """The incl unit price to save on an inclusive order line."""
    computed = cents(excl * (1 + (tax_rate or 0) / 100))
    if incl is not None and incl > 0 and abs(incl - computed) <= INCL_TRUST_TOLERANCE + 1e-6:
        return cents(incl)
    return computed


def price_list_row_broken(source: str, excl: float, incl: Optional[float],
                          item_tax_rate: Optional[float]) -> bool:
    """True when a PRICE-LIST row's stored incl doesn't match its own excl +
    the item's VAT (e.g. MOO099: R25.90 excl / R25.90 incl at 15%).

    Evolution charges that stored incl as-is on an inclusive order, so the
    customer would be charged the wrong price if the order were keyed in
    Evolution directly. The app is not affected — inclusive_unit_price()
    falls back to excl + VAT — so this is a warning for the price-list
    owner, never a reason to block the order.

    Compared against the ITEM's VAT rate (what the price list incl is built
    from), not the order line's: a 0%-VAT customer legitimately has a stored
    incl that includes 15%, and must not be flagged. Deals are never flagged —
    their incl is derived from the deal price itself.
    """
    if source != "Price List" or item_tax_rate is None or not excl:
        return False
    expected = cents(excl * (1 + item_tax_rate / 100))
    return abs((incl or 0) - expected) > INCL_TRUST_TOLERANCE + 1e-6


def resolve_customer_prices(
    db: Session,
    account: str,
    stock_codes: List[str],
    as_of: date,
    warehouse_code: Optional[str] = None,
) -> Dict[str, schemas.CustomerPrice]:
    """
    Look up the resolved price (Customer Deal > Group Deal > Price List) for
    each stock code, keyed by stock code. Stocks with no priced entry on the
    customer's price list in the rep's warehouse are omitted from the map
    (caller treats missing as source='None', price=0, which unlocks the
    manual-entry input).

    `as_of` is the rep's session date — deals are only considered active if
    their effective window straddles that date. Caller must always pass this
    (typically from AuthContext.sessionDate) so a rep working an order dated
    next Monday sees Monday's deal set, not today's.

    When `warehouse_code` is provided (typically the logged-in agent's
    default DC), ONLY prices for that warehouse are returned — we no longer
    fall back to lowest-across-warehouses, because the business wants the
    rep to enter the price manually rather than silently use a price from
    a warehouse they aren't shipping from.

    When `warehouse_code` is None (unscoped lookup), the cheapest row across
    any warehouse is returned — same as before.
    """
    codes = sorted({c for c in stock_codes if c})
    if not codes:
        return {}

    sql = text(
        """
        SELECT P.StockCode, P.WhseCode, P.ExclPrice, P.InclPrice, P.PriceSource,
               ItemTaxRate = TX.TaxRate
        FROM [SOAssistant].dbo._fn_SOCustomerPriceAsOf(:as_of) P
        -- Item's own VAT rate (master stock-details row, the same source the
        -- deals view uses) — only for the broken-price-list-row flag.
        OUTER APPLY (
            SELECT TOP 1 T.TaxRate
            FROM [BA_00_LPD_CON].dbo._etblStockDetails DET
            JOIN [BA_00_LPD_CON].dbo.TaxRate T ON T.idTaxRate = DET.TTInvID
            WHERE DET.StockID = P.iStockID AND DET.WhseID < 1
        ) TX
        WHERE P.Account = :account
          AND P.StockCode IN :codes
        """
    ).bindparams(bindparam("codes", expanding=True))

    rows = db.execute(
        sql, {"account": account, "codes": codes, "as_of": as_of}
    ).mappings().all()

    preferred: Dict[str, schemas.CustomerPrice] = {}
    unscoped: Dict[str, schemas.CustomerPrice] = {}
    normalized_whse = (warehouse_code or "").strip().upper() or None

    for r in rows:
        code = r["StockCode"]
        whse = (r["WhseCode"] or "").strip().upper()
        excl = float(r["ExclPrice"] or 0)
        incl = float(r["InclPrice"] or 0)
        source = r["PriceSource"] or "Price List"
        item_rate = r["ItemTaxRate"]
        broken = price_list_row_broken(
            source, excl, incl, float(item_rate) if item_rate is not None else None
        )

        if normalized_whse:
            # Warehouse-scoped: take only rows for the preferred warehouse.
            if whse != normalized_whse:
                continue
            preferred.setdefault(
                code,
                schemas.CustomerPrice(
                    stock_code=code,
                    excl_price=excl,
                    incl_price=incl,
                    source=source,
                    is_fallback_warehouse=False,
                    price_list_broken=broken,
                ),
            )
            continue

        # Unscoped lookup — keep the cheapest excl across warehouses.
        existing = unscoped.get(code)
        if existing and existing.excl_price <= excl:
            continue
        unscoped[code] = schemas.CustomerPrice(
            stock_code=code,
            excl_price=excl,
            incl_price=incl,
            source=source,
            is_fallback_warehouse=False,
            price_list_broken=broken,
        )

    return preferred if normalized_whse else unscoped

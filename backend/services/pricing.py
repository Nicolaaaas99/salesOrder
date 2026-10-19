from typing import Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

import schemas


def resolve_customer_prices(
    db: Session,
    account: str,
    stock_codes: List[str],
    warehouse_code: Optional[str] = None,
) -> Dict[str, schemas.CustomerPrice]:
    """
    Look up the resolved price (Customer Deal > Group Deal > Price List) for
    each stock code, keyed by stock code. Stocks with no priced entry on the
    customer's price list in the rep's warehouse are omitted from the map
    (caller treats missing as source='None', price=0, which unlocks the
    manual-entry input).

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
        SELECT StockCode, WhseCode, ExclPrice, InclPrice, PriceSource
        FROM dbo._vw_SOCustomerPrice
        WHERE Account = :account
          AND StockCode IN :codes
        """
    ).bindparams(bindparam("codes", expanding=True))

    rows = db.execute(sql, {"account": account, "codes": codes}).mappings().all()

    preferred: Dict[str, schemas.CustomerPrice] = {}
    unscoped: Dict[str, schemas.CustomerPrice] = {}
    normalized_whse = (warehouse_code or "").strip().upper() or None

    for r in rows:
        code = r["StockCode"]
        whse = (r["WhseCode"] or "").strip().upper()
        excl = float(r["ExclPrice"] or 0)
        incl = float(r["InclPrice"] or 0)
        source = r["PriceSource"] or "Price List"

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
        )

    return preferred if normalized_whse else unscoped

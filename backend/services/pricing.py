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
    customer's price list are omitted from the map (caller treats missing as
    source='None', price=0).

    When `warehouse_code` is provided (typically the logged-in agent's default
    DC), the price is preferred from that warehouse. If the stock has no row
    in that warehouse, we fall back to the lowest price across any warehouse
    and set `is_fallback_warehouse=True` so the UI can show a badge.
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

    # First pass: for each stock, pick the row that matches the preferred
    # warehouse (if given). Second pass covers any stock that had no row in
    # that warehouse — fall back to the cheapest row across any warehouse.
    preferred: Dict[str, schemas.CustomerPrice] = {}
    fallback_candidates: Dict[str, schemas.CustomerPrice] = {}
    normalized_whse = (warehouse_code or "").strip().upper() or None

    for r in rows:
        code = r["StockCode"]
        whse = (r["WhseCode"] or "").strip().upper()
        excl = float(r["ExclPrice"] or 0)
        incl = float(r["InclPrice"] or 0)
        source = r["PriceSource"] or "Price List"

        if normalized_whse and whse == normalized_whse:
            # Take the first matching-warehouse row we see; the view is
            # already de-duped at (customer, stock, warehouse) by our Python
            # dedupe downstream so there shouldn't be multiples anyway.
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

        # Fallback candidate — keep the cheapest excl across warehouses.
        existing = fallback_candidates.get(code)
        if existing and existing.excl_price <= excl:
            continue
        fallback_candidates[code] = schemas.CustomerPrice(
            stock_code=code,
            excl_price=excl,
            incl_price=incl,
            source=source,
            is_fallback_warehouse=True,
        )

    # Merge: preferred wins, fallback fills the rest.
    out: Dict[str, schemas.CustomerPrice] = {}
    for code in codes:
        if code in preferred:
            out[code] = preferred[code]
        elif code in fallback_candidates:
            out[code] = fallback_candidates[code]
    return out

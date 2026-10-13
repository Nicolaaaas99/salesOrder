from typing import Dict, List

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

import schemas


def resolve_customer_prices(
    db: Session,
    account: str,
    stock_codes: List[str],
) -> Dict[str, schemas.CustomerPrice]:
    """
    Look up the resolved price (Customer Deal > Group Deal > Price List) for
    each stock code, keyed by stock code. Stocks with no priced entry on the
    customer's price list are omitted from the map (caller treats missing as
    source='None', price=0).

    De-duplicates on stock code before hitting the DB and again in Python, in
    case the underlying view produces more than one row per (customer, stock).
    """
    codes = sorted({c for c in stock_codes if c})
    if not codes:
        return {}

    sql = text(
        """
        SELECT StockCode, ExclPrice, InclPrice, PriceSource
        FROM dbo._vw_SOCustomerPrice
        WHERE Account = :account
          AND StockCode IN :codes
        """
    ).bindparams(bindparam("codes", expanding=True))

    rows = db.execute(sql, {"account": account, "codes": codes}).mappings().all()

    out: Dict[str, schemas.CustomerPrice] = {}
    for r in rows:
        code = r["StockCode"]
        if code in out:
            continue
        out[code] = schemas.CustomerPrice(
            stock_code=code,
            excl_price=float(r["ExclPrice"] or 0),
            incl_price=float(r["InclPrice"] or 0),
            source=r["PriceSource"] or "Price List",
        )
    return out

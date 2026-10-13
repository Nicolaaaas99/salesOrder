from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
import schemas
from services.pricing import resolve_customer_prices

router = APIRouter(
    prefix="/lookups",
    tags=["lookups"],
    dependencies=[Depends(get_current_user)],
)


def _join_address(*parts) -> str:
    return "\n".join([str(p).strip() for p in parts if p is not None and str(p).strip()])


@router.get("/customers", response_model=List[schemas.CustomerSummary])
def list_customers(
    q: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    sql = """
        SELECT TOP (:limit) AccountCode AS account, CustomerName AS name
        FROM dbo._vw_SOCustomers
        WHERE (:like IS NULL OR AccountCode LIKE :like OR CustomerName LIKE :like)
        ORDER BY CustomerName
    """
    q_stripped = (q or "").strip()
    like = f"%{q_stripped}%" if q_stripped else None
    rows = db.execute(text(sql), {"like": like, "limit": limit}).mappings().all()
    return [schemas.CustomerSummary(**r) for r in rows]


CUSTOMER_CREDIT_SQL = """
    SELECT * FROM dbo._vw_SOCustomerDetail WHERE Account = :account
"""


def _build_customer_detail(row) -> schemas.CustomerDetail:
    # Convert to plain dict so missing columns fall back to defaults via .get()
    r = dict(row)

    def g(key, default=None):
        return r.get(key, default)

    credit_limit = float(g("Credit_Limit", 0) or 0)
    total_outstanding = float(g("TotalOutstanding", 0) or 0)
    total_ordered = float(g("TotalOrdered", 0) or 0)
    available_credit = credit_limit - total_outstanding - total_ordered
    over_credit_limit = credit_limit > 0 and available_credit < 0

    check_terms = bool(g("CheckTerms", 0))
    terms_days = int(g("TermsDays", 0) or 0)
    terms_code = g("TermsCode", "") or ""
    terms_age_type = int(g("AgeTypeOption", 0) or 0)
    cutoff_val = g("CutoffDate")
    try:
        terms_cutoff_date = cutoff_val if hasattr(cutoff_val, "year") else None
    except Exception:
        terms_cutoff_date = None
    overdue_amount = float(g("OverdueAmount", 0) or 0)
    over_terms = check_terms and overdue_amount > 0

    def_tax = g("iDefTaxTypeID")

    return schemas.CustomerDetail(
        account=g("Account", ""),
        name=g("Name", "") or "",
        delivery_address=_join_address(
            g("Physical1"), g("Physical2"), g("Physical3"),
            g("Physical4"), g("Physical5"), g("PhysicalPC"),
        ),
        postal_address=_join_address(
            g("Post1"), g("Post2"), g("Post3"),
            g("Post4"), g("Post5"), g("PostPC"),
        ),
        contact_name=g("Contact_Person", "") or "",
        contact_tel=g("Telephone", "") or "",
        contact_email=g("EMail", "") or "",
        default_tax_type_id=int(def_tax) if def_tax else None,
        credit_limit=credit_limit,
        total_outstanding=total_outstanding,
        total_ordered=total_ordered,
        available_credit=available_credit,
        over_credit_limit=over_credit_limit,
        check_terms=check_terms,
        terms_code=terms_code,
        terms_age_type=terms_age_type,
        terms_days=terms_days,
        terms_cutoff_date=terms_cutoff_date,
        overdue_amount=overdue_amount,
        over_terms=over_terms,
    )


@router.get("/customers/{account}", response_model=schemas.CustomerDetail)
def get_customer(account: str, db: Session = Depends(get_db)):
    row = db.execute(text(CUSTOMER_CREDIT_SQL), {"account": account}).mappings().first()
    if not row:
        raise HTTPException(404, f"Customer '{account}' not found")
    return _build_customer_detail(row)


@router.get("/inventory", response_model=List[schemas.InventoryItem])
def list_inventory(
    q: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    sql = """
        SELECT TOP (:limit)
            StockCode                                            AS code,
            MAX(StockDesc)                                       AS description,
            MAX(CAST(IsUomItem AS int))                          AS is_uom_item,
            MAX(CAST(IsWhseItem AS int))                         AS is_whse_item,
            MAX(Uom)                                             AS uom,
            MAX(DefaultTaxTypeId)                                AS default_tax_type_id,
            MAX(PackSize)                                        AS pack_size,
            SUM(QtyOnHand)                                       AS available,
            STRING_AGG(WhseCode, ',') WITHIN GROUP (ORDER BY WhseCode) AS warehouse_codes
        FROM dbo._vw_SOInventory
        WHERE (:like IS NULL OR StockCode LIKE :like OR StockDesc LIKE :like)
        GROUP BY StockCode
        ORDER BY StockCode
    """
    q_stripped = (q or "").strip()
    like = f"%{q_stripped}%" if q_stripped else None
    rows = db.execute(text(sql), {"like": like, "limit": limit}).mappings().all()
    return [
        schemas.InventoryItem(
            code=r["code"],
            description=r["description"] or "",
            uom=r["uom"] or "",
            is_uom_item=bool(r["is_uom_item"]),
            is_whse_item=bool(r["is_whse_item"]),
            default_tax_type_id=int(r["default_tax_type_id"]) if r["default_tax_type_id"] else None,
            available=float(r["available"] or 0),
            warehouse_codes=[c for c in (r["warehouse_codes"] or "").split(",") if c],
            pack_size=max(1, int(r["pack_size"] or 1)),
        )
        for r in rows
    ]


@router.get("/warehouses", response_model=List[schemas.Warehouse])
def list_warehouses(db: Session = Depends(get_db)):
    sql = """
        SELECT Code AS code, Description AS description
        FROM dbo._vw_SOWarehouses
        ORDER BY Code
    """
    rows = db.execute(text(sql)).mappings().all()
    return [schemas.Warehouse(**r) for r in rows]


@router.get("/tax-types", response_model=List[schemas.TaxType])
def list_tax_types(db: Session = Depends(get_db)):
    sql = """
        SELECT
            Id          AS id,
            Code        AS code,
            Description AS description,
            Rate        AS rate
        FROM dbo._vw_SOTaxTypes
        ORDER BY Code
    """
    rows = db.execute(text(sql)).mappings().all()
    return [
        schemas.TaxType(
            id=int(r["id"]),
            code=r["code"] or "",
            description=r["description"] or "",
            rate=float(r["rate"] or 0),
        )
        for r in rows
    ]


@router.get("/projects", response_model=List[schemas.Project])
def list_projects(
    q: Optional[str] = Query(None),
    limit: int = Query(200, le=500),
    db: Session = Depends(get_db),
):
    sql = """
        SELECT TOP (:limit)
            ProjectCode AS code,
            ProjectName AS description
        FROM dbo._vw_SOProjects
        WHERE (:like IS NULL OR ProjectCode LIKE :like OR ProjectName LIKE :like)
        ORDER BY ProjectCode
    """
    q_stripped = (q or "").strip()
    like = f"%{q_stripped}%" if q_stripped else None
    rows = db.execute(text(sql), {"like": like, "limit": limit}).mappings().all()
    return [schemas.Project(**r) for r in rows]


@router.post("/pack-sizes", response_model=Dict[str, int])
def get_pack_sizes(
    payload: schemas.PackSizesRequest,
    db: Session = Depends(get_db),
):
    """
    Resolve ulIIPerCrate for a batch of stock codes. Returns a map of
    { stock_code: pack_size }. Codes with no pack size default to 1.
    Codes not found in inventory are omitted.
    """
    codes = [c.strip() for c in payload.stock_codes if c and c.strip()]
    if not codes:
        return {}
    sql = text(
        """
        SELECT StockCode, MAX(PackSize) AS pack_size
        FROM dbo._vw_SOInventory
        WHERE StockCode IN :codes
        GROUP BY StockCode
        """
    ).bindparams(bindparam("codes", expanding=True))
    rows = db.execute(sql, {"codes": codes}).mappings().all()
    return {r["StockCode"]: max(1, int(r["pack_size"] or 1)) for r in rows}


@router.post("/customer-prices", response_model=Dict[str, schemas.CustomerPrice])
def get_customer_prices(
    payload: schemas.CustomerPricesRequest,
    db: Session = Depends(get_db),
):
    """
    Resolve prices for one customer across a batch of stock codes.

    Response is a map { stock_code: {excl_price, incl_price, source} }. Stocks
    with no priced entry on the customer's price list (and no active deal) are
    omitted from the map — the frontend treats "missing" as source='None' and
    leaves the rep to enter a price manually.
    """
    account = (payload.account or "").strip()
    if not account:
        raise HTTPException(400, "account is required")
    return resolve_customer_prices(db, account, payload.stock_codes)


@router.get("/settlement-terms", response_model=List[schemas.SettlementTerm])
def list_settlement_terms(db: Session = Depends(get_db)):
    sql = """
        SELECT Code AS code, Description AS description
        FROM dbo._vw_SOSettlementTerms
        ORDER BY Code
    """
    rows = db.execute(text(sql)).mappings().all()
    return [schemas.SettlementTerm(**r) for r in rows]

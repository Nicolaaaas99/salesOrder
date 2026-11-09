from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from auth import get_current_user, require_min_role
from database import get_db
import schemas
from services.pricing import resolve_customer_prices


def _app_version(
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
) -> Optional[str]:
    """SPA build version header — stamped onto audit rows so we can tell
    which client build made a change."""
    return (x_app_version or "").strip()[:32] or None

router = APIRouter(
    prefix="/lookups",
    tags=["lookups"],
    dependencies=[Depends(get_current_user)],
)


# Client.ulARCorpGroup values that count as corporate. Sales Order Entry
# may only place orders for these customers (Order Assistant is
# unrestricted). 'OK' = OK Foods. Allowlist rather than
# "<> 'Non Corporate'" so NULL and any group added later stay excluded
# until someone opts them in here.
CORPORATE_GROUPS = ("Spar", "Checkers", "Boxer", "Pick n Pay", "OK")

# SQL predicate on alias CL. NULL is folded to 'Non Corporate' explicitly
# (it would be excluded anyway, but this reads unambiguously).
CORPORATE_GROUP_SQL = (
    "LTRIM(RTRIM(ISNULL(CL.ulARCorpGroup, 'Non Corporate'))) IN :corp_groups"
)


def is_corporate_customer(db: Session, account: str) -> bool:
    row = db.execute(
        text(
            f"SELECT 1 FROM [BA_00_LPD_CON].dbo.Client CL "
            f"WHERE CL.Account = :account AND {CORPORATE_GROUP_SQL}"
        ).bindparams(bindparam("corp_groups", expanding=True)),
        {"account": (account or "").strip(), "corp_groups": list(CORPORATE_GROUPS)},
    ).first()
    return row is not None


def _join_address(*parts) -> str:
    return "\n".join([str(p).strip() for p in parts if p is not None and str(p).strip()])


@router.get("/customers", response_model=List[schemas.CustomerSummary])
def list_customers(
    q: Optional[str] = Query(None),
    limit: int = Query(50, le=2000),
    route: Optional[str] = Query(
        None,
        description="Restrict to customers on this route (Client.ulARRoute).",
    ),
    telesale: Optional[str] = Query(
        None,
        description="Restrict to customers with this telesales rep (Client.ulARTelesalesRep).",
    ),
    sales_rep: Optional[str] = Query(
        None,
        description=(
            "Restrict to customers whose assigned SalesRep.Code matches "
            "this value (joins Client.RepID → SalesRep.Code)."
        ),
    ),
    corporate_only: bool = Query(
        False,
        description=(
            "Only customers whose Client.ulARCorpGroup is a corporate "
            "group (Spar / Checkers / Boxer / Pick n Pay). Used by the "
            "Sales Order Entry customer picker."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Customer search for pickers. Optional dim filters narrow the list
    so the Target form can offer only customers that match the other
    scope dims the user has already set — no "always-zero" compound
    targets."""
    # The customer-side filters hit the underlying Client table (not the
    # _vw_SOCustomers view, which already filters out On_Hold). We re-apply
    # the On_Hold = 0 guard here to keep behaviour identical when none of
    # the filters are set.
    corp_clause = f"AND {CORPORATE_GROUP_SQL}" if corporate_only else ""
    sql = f"""
        SELECT TOP (:limit)
            LTRIM(RTRIM(CL.Account)) AS account,
            CL.Name                  AS name
        FROM [BA_00_LPD_CON].dbo.Client CL
        LEFT JOIN [BA_00_LPD_CON].dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
        WHERE ISNULL(CL.On_Hold, 0) = 0
          AND CL.Account IS NOT NULL
          AND CL.Name IS NOT NULL
          AND (:like IS NULL OR CL.Account LIKE :like OR CL.Name LIKE :like)
          AND (:route IS NULL
               OR LTRIM(RTRIM(CL.ulARRoute)) = :route)
          AND (:telesale IS NULL
               OR LTRIM(RTRIM(ISNULL(CL.ulARTelesalesRep, ''))) = :telesale)
          AND (:sales_rep IS NULL
               OR LTRIM(RTRIM(ISNULL(REP.Code, ''))) = :sales_rep)
          {corp_clause}
        ORDER BY CL.Name
    """
    q_stripped = (q or "").strip()
    like = f"%{q_stripped}%" if q_stripped else None
    params = {
        "like": like,
        "limit": limit,
        "route": (route or "").strip() or None,
        "telesale": (telesale or "").strip() or None,
        "sales_rep": (sales_rep or "").strip() or None,
    }
    stmt = text(sql)
    if corporate_only:
        stmt = stmt.bindparams(bindparam("corp_groups", expanding=True))
        params["corp_groups"] = list(CORPORATE_GROUPS)
    rows = db.execute(stmt, params).mappings().all()
    return [
        schemas.CustomerSummary(
            account=(r["account"] or "").strip(),
            name=(r["name"] or "").strip(),
        )
        for r in rows
        if r["account"] and r["name"]
    ]


CUSTOMER_CREDIT_SQL = """
    SELECT * FROM [SOAssistant].dbo._vw_SOCustomerDetail WHERE Account = :account
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

    # Treat only truthy numeric values as "check enabled". bool("0") is True,
    # which was wrongly blocking CheckTerms=0 customers when the SQL driver
    # returned the column as a string.
    check_terms = int(g("CheckTerms", 0) or 0) == 1
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
        route=(g("Route", "") or "").strip(),
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
        contact_tel2=g("Telephone2", "") or "",
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


@router.patch("/customers/{account}/contact", response_model=schemas.CustomerDetail)
def update_customer_contact(
    account: str,
    payload: schemas.CustomerContactUpdate,
    db: Session = Depends(get_db),
):
    """Update the Client's contact person and two telephone numbers. The rep
    uses the inline-edit strip in the Order Assistant to keep these in sync
    with what the customer tells them during the call."""
    exists = db.execute(
        text("SELECT 1 FROM dbo.Client WHERE Account = :account"),
        {"account": account},
    ).first()
    if not exists:
        raise HTTPException(404, f"Customer '{account}' not found")

    db.execute(
        text(
            """
            UPDATE dbo.Client
            SET Contact_Person = :name,
                Telephone      = :tel,
                Telephone2     = :tel2
            WHERE Account = :account
            """
        ),
        {
            "account": account,
            "name": (payload.contact_name or "").strip(),
            "tel": (payload.contact_tel or "").strip(),
            "tel2": (payload.contact_tel2 or "").strip(),
        },
    )
    db.commit()

    row = db.execute(text(CUSTOMER_CREDIT_SQL), {"account": account}).mappings().first()
    return _build_customer_detail(row)


@router.get("/inventory", response_model=List[schemas.InventoryItem])
def list_inventory(
    q: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    category: Optional[str] = Query(
        None,
        description=(
            "Restrict to stock items in this category "
            "(StkItem.ulIIMainCategory). Lets the Target form filter the "
            "Stock dropdown once a Category chip is set."
        ),
    ),
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
            MAX(Category)                                        AS category,
            SUM(QtyOnHand)                                       AS available,
            STRING_AGG(WhseCode, ',') WITHIN GROUP (ORDER BY WhseCode) AS warehouse_codes
        FROM [SOAssistant].dbo._vw_SOInventory
        WHERE (:like IS NULL OR StockCode LIKE :like OR StockDesc LIKE :like)
          AND (:category IS NULL
               OR ISNULL(Category, '') = :category)
        GROUP BY StockCode
        ORDER BY StockCode
    """
    q_stripped = (q or "").strip()
    like = f"%{q_stripped}%" if q_stripped else None
    rows = db.execute(
        text(sql),
        {
            "like": like,
            "limit": limit,
            "category": (category or "").strip() or None,
        },
    ).mappings().all()
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
            category=(r["category"] or "").strip(),
        )
        for r in rows
    ]


@router.get("/warehouses", response_model=List[schemas.Warehouse])
def list_warehouses(db: Session = Depends(get_db)):
    sql = """
        SELECT Code AS code, Description AS description
        FROM [SOAssistant].dbo._vw_SOWarehouses
        ORDER BY Code
    """
    rows = db.execute(text(sql)).mappings().all()
    return [schemas.Warehouse(**r) for r in rows]


@router.get("/warehouses/default-choices", response_model=List[schemas.Warehouse])
def list_warehouse_default_choices(db: Session = Depends(get_db)):
    """Only the DC warehouses a telesales agent is allowed to pick as their
    default. Mirrors the WhseLink filter on _vw_SOAgentDefaultWhse so the
    first-login prompt can't offer invalid DCs."""
    sql = """
        SELECT Code AS code, Description AS description
        FROM [SOAssistant].dbo._vw_SOWarehouses
        WHERE WhseLink IN (48, 49)
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
        FROM [SOAssistant].dbo._vw_SOTaxTypes
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
        FROM [SOAssistant].dbo._vw_SOProjects
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
        FROM [SOAssistant].dbo._vw_SOInventory
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
    return resolve_customer_prices(
        db,
        account,
        payload.stock_codes,
        as_of=payload.as_of,
        warehouse_code=payload.warehouse_code,
    )


@router.get("/settlement-terms", response_model=List[schemas.SettlementTerm])
def list_settlement_terms(db: Session = Depends(get_db)):
    sql = """
        SELECT Code AS code, Description AS description
        FROM [SOAssistant].dbo._vw_SOSettlementTerms
        ORDER BY Code
    """
    rows = db.execute(text(sql)).mappings().all()
    return [schemas.SettlementTerm(**r) for r in rows]


# ---------------------------------------------------------------------------
# Customer-assignment maintenance (Analytics → Customer Sales History → Edit)
# ---------------------------------------------------------------------------

@router.get("/route-options", response_model=List[schemas.RouteOption])
def list_route_options(db: Session = Depends(get_db)):
    """Every route the user-dict offers for Client.ulARRoute. Distinct from
    `/history/routes` (which lists routes with at least one customer) —
    this one includes a freshly-created INACTIVE option before anyone's
    been reassigned."""
    rows = db.execute(
        text(
            "SELECT DISTINCT Route FROM [SOAssistant].dbo._vw_SORouteOptions "
            "ORDER BY Route"
        )
    ).mappings().all()
    return [schemas.RouteOption(route=(r["Route"] or "").strip()) for r in rows]


@router.get("/telesale-options", response_model=List[schemas.TelesaleOption])
def list_telesale_options(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT DISTINCT Telesale FROM [SOAssistant].dbo._vw_SOTelesaleOptions "
            "ORDER BY Telesale"
        )
    ).mappings().all()
    return [schemas.TelesaleOption(telesale=(r["Telesale"] or "").strip()) for r in rows]


@router.get("/category-options", response_model=List[schemas.CategoryOption])
def list_category_options(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT DISTINCT Category FROM [SOAssistant].dbo._vw_SOCategoryOptions "
            "ORDER BY Category"
        )
    ).mappings().all()
    return [schemas.CategoryOption(category=(r["Category"] or "").strip()) for r in rows]


@router.get("/agent-options", response_model=List[schemas.AgentOption])
def list_agent_options(db: Session = Depends(get_db)):
    """Agents who can own a "Placed by" target. Queries Evolution's
    canonical `_rtblAgents` table directly — the login handle a user
    types to sign into the app IS `cAgentName`, and `cAgentName` is
    what gets stamped onto `_tb_SOOrderAssistantAudit.cUsername` on
    every placed order. One row per logical person, no pseudo-
    duplicates ("Brenda" / "Brendas", "Fridah" / "FridahR") because
    the agent master doesn't carry the typos that the telesales
    user-dict + historical audit rows do.

    Filtered to active agents only (`bAgentActive = 1`). If someone
    needs to appear here, add them as an agent in Evolution.

    Returns `cAgentName` as `name`. The Target form's "Placed by" chip
    always persists to `_tb_SOTargets.cUsername`; the measurement path
    then matches audit rows by that exact string."""
    rows = db.execute(
        text(
            """
            SELECT idAgents AS id, LTRIM(RTRIM(cAgentName)) AS name
            FROM [BA_00_LPD_CON].dbo._rtblAgents
            WHERE bAgentActive = 1
              AND cAgentName IS NOT NULL
              AND LTRIM(RTRIM(cAgentName)) <> ''
            ORDER BY name
            """
        )
    ).mappings().all()
    return [
        schemas.AgentOption(id=int(r["id"]), name=(r["name"] or "").strip())
        for r in rows
        if r["id"] is not None and (r["name"] or "").strip()
    ]


@router.get("/all-agents", response_model=List[schemas.AllAgentOption])
def list_all_agents(db: Session = Depends(get_db)):
    """Every known agent username, merged from three sources:
      • `_tb_SOOrderAssistantAudit.cUsername` — anyone who has ever
        placed an order via the app (catches non-rep logins like admins).
      • `SalesRep.Code` — Evolution's rep master.
      • `_rtblAgents.cAgentName` — Evolution's agent master.

    Dedupe on lowercase username; the SalesRep name (if available) wins
    the display label. Used by the target form's Agent dropdown so a
    user that isn't a SalesRep (e.g. you, NJ) can still have targets
    set against them."""
    rows = db.execute(
        text(
            """
            WITH all_sources AS (
                SELECT DISTINCT
                       LTRIM(RTRIM(cUsername)) AS username,
                       CAST(NULL AS varchar(100)) AS label,
                       'audit' AS source
                FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit
                WHERE cUsername IS NOT NULL
                  AND LTRIM(RTRIM(cUsername)) <> ''

                UNION ALL
                SELECT DISTINCT
                       LTRIM(RTRIM(Code)) AS username,
                       NULLIF(LTRIM(RTRIM(Name)), '') AS label,
                       'salesrep' AS source
                FROM [BA_00_LPD_CON].dbo.SalesRep
                WHERE Code IS NOT NULL
                  AND LTRIM(RTRIM(Code)) <> ''

                UNION ALL
                SELECT DISTINCT
                       LTRIM(RTRIM(cAgentName)) AS username,
                       NULLIF(LTRIM(RTRIM(cDescription)), '') AS label,
                       'agent' AS source
                FROM [BA_00_LPD_CON].dbo._rtblAgents
                WHERE cAgentName IS NOT NULL
                  AND LTRIM(RTRIM(cAgentName)) <> ''
            ),
            picked AS (
                SELECT username, label, source,
                       ROW_NUMBER() OVER (
                           PARTITION BY LOWER(username)
                           ORDER BY
                               -- Prefer rows that carry a label; then
                               -- prefer SalesRep > agent > audit as the
                               -- "most canonical" source.
                               CASE WHEN label IS NOT NULL THEN 0 ELSE 1 END,
                               CASE source
                                   WHEN 'salesrep' THEN 0
                                   WHEN 'agent'    THEN 1
                                   ELSE 2
                               END
                       ) AS rn
                FROM all_sources
            )
            SELECT username, label, source
            FROM picked
            WHERE rn = 1
            ORDER BY username
            """
        )
    ).mappings().all()
    return [
        schemas.AllAgentOption(
            username=(r["username"] or "").strip(),
            label=((r["label"] or r["username"]) or "").strip(),
            source=(r["source"] or "").strip(),
        )
        for r in rows
    ]


@router.get("/sales-reps", response_model=List[schemas.SalesRepOption])
def list_sales_reps(db: Session = Depends(get_db)):
    # Filter out reps with no Code — a rep without a readable short-name
    # has nothing meaningful to display, and showing "#<id>" as a
    # fallback made the dropdown look like it contained system
    # placeholders. Hidden reps simply aren't pickable from the modal.
    rows = db.execute(
        text(
            """
            SELECT Id, SalesRep, RepName
            FROM [SOAssistant].dbo._vw_SOSalesRep
            WHERE SalesRep IS NOT NULL AND LTRIM(RTRIM(SalesRep)) <> ''
            ORDER BY SalesRep
            """
        )
    ).mappings().all()
    return [
        schemas.SalesRepOption(
            id=int(r["Id"]),
            code=(r["SalesRep"] or "").strip(),
            name=(r["RepName"] or "").strip(),
        )
        for r in rows
    ]


@router.get("/customers/{account}/assignment", response_model=schemas.CustomerAssignment)
def get_customer_assignment(account: str, db: Session = Depends(get_db)):
    """Current route / telesales / sales-rep assignment for the given
    customer. Powers the Edit modal's prefill."""
    row = db.execute(
        text(
            """
            SELECT CL.Account         AS account,
                   CL.Name            AS name,
                   CL.ulARRoute       AS route,
                   CL.ulARTelesalesRep AS telesale,
                   CL.RepID           AS salesrep_id,
                   REP.Code           AS salesrep_code,
                   ISNULL(CL.On_Hold, 0) AS on_hold
            FROM dbo.Client CL
            LEFT JOIN dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
            WHERE CL.Account = :account
            """
        ),
        {"account": account},
    ).mappings().first()
    if not row:
        raise HTTPException(404, f"Customer '{account}' not found")

    def _clean(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return schemas.CustomerAssignment(
        account=(row["account"] or "").strip(),
        name=(row["name"] or "").strip(),
        route=_clean(row["route"]),
        telesale=_clean(row["telesale"]),
        salesrep_id=int(row["salesrep_id"]) if row["salesrep_id"] is not None else None,
        salesrep_code=_clean(row["salesrep_code"]),
        on_hold=bool(row["on_hold"]),
    )


@router.patch(
    "/customers/{account}/assignment",
    response_model=schemas.CustomerAssignment,
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)
def update_customer_assignment(
    account: str,
    payload: schemas.CustomerAssignmentUpdate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    app_version: Optional[str] = Depends(_app_version),
):
    """Reassign a customer's route / telesales / sales-rep. Writes
    directly to Client (bypasses the SDK — these are plain scalar columns
    with no SDK-side invariants). An audit row is written with the
    before/after values before the UPDATE commits, so the two always
    land together or neither does."""
    # Validate salesrep_id if provided
    if payload.salesrep_id is not None:
        rep = db.execute(
            text("SELECT idSalesRep, Code FROM dbo.SalesRep WHERE idSalesRep = :id"),
            {"id": payload.salesrep_id},
        ).mappings().first()
        if not rep:
            raise HTTPException(400, f"Sales rep id {payload.salesrep_id} does not exist")
        new_rep_code = (rep["Code"] or "").strip() or None
    else:
        new_rep_code = None

    # Snapshot the current values before the UPDATE so the audit row can
    # record the exact before/after. Load DCLink too so the audit row is
    # keyed by both account + dclink (survives account renames).
    current = db.execute(
        text(
            """
            SELECT CL.DCLink      AS dclink,
                   CL.ulARRoute       AS prev_route,
                   CL.ulARTelesalesRep AS prev_telesale,
                   CL.RepID           AS prev_salesrep_id,
                   REP.Code           AS prev_salesrep_code,
                   ISNULL(CL.On_Hold, 0) AS prev_on_hold
            FROM dbo.Client CL
            LEFT JOIN dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
            WHERE CL.Account = :account
            """
        ),
        {"account": account},
    ).mappings().first()
    if not current:
        raise HTTPException(404, f"Customer '{account}' not found")

    def _norm(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    new_route = _norm(payload.route)
    new_telesale = _norm(payload.telesale)
    new_rep_id = payload.salesrep_id
    new_on_hold = 1 if payload.on_hold else 0
    prev_on_hold = 1 if int(current["prev_on_hold"] or 0) else 0

    try:
        # Audit first — if the Client UPDATE later fails for some reason
        # (permission revoked, deadlock) the audit insert rolls back with
        # it because both run in the same transaction. Both commit together.
        db.execute(
            text(
                """
                INSERT INTO [SOAssistant].dbo._tb_SOCustomerAssignmentAudit
                    (iCustomerDCLink, cCustomerAccount,
                     cPrevRoute, cNewRoute,
                     cPrevTelesale, cNewTelesale,
                     iPrevSalesRepId, iNewSalesRepId,
                     cPrevSalesRepCode, cNewSalesRepCode,
                     bPrevOnHold, bNewOnHold,
                     cUsername, cAppVersion, cNote)
                VALUES
                    (:dclink, :account,
                     :prev_route, :new_route,
                     :prev_telesale, :new_telesale,
                     :prev_rep_id, :new_rep_id,
                     :prev_rep_code, :new_rep_code,
                     :prev_on_hold, :new_on_hold,
                     :username, :app_version, :note)
                """
            ),
            {
                "dclink": int(current["dclink"]),
                "account": account,
                "prev_route": _norm(current["prev_route"]),
                "new_route": new_route,
                "prev_telesale": _norm(current["prev_telesale"]),
                "new_telesale": new_telesale,
                "prev_rep_id": int(current["prev_salesrep_id"]) if current["prev_salesrep_id"] is not None else None,
                "new_rep_id": new_rep_id,
                "prev_rep_code": _norm(current["prev_salesrep_code"]),
                "new_rep_code": new_rep_code,
                "prev_on_hold": prev_on_hold,
                "new_on_hold": new_on_hold,
                "username": username,
                "app_version": app_version,
                "note": (payload.note or "").strip()[:200] or None,
            },
        )

        db.execute(
            text(
                """
                UPDATE dbo.Client
                SET ulARRoute        = :route,
                    ulARTelesalesRep = :telesale,
                    RepID            = :salesrep_id,
                    On_Hold          = :on_hold
                WHERE Account = :account
                """
            ),
            {
                "account": account,
                "route": new_route,
                "telesale": new_telesale,
                "salesrep_id": new_rep_id,
                "on_hold": new_on_hold,
            },
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Could not update customer assignment: {e}")

    # Return the freshly-read state so the frontend can confirm what's
    # persisted (and populate the pivot row's new label).
    return get_customer_assignment(account, db)


@router.patch(
    "/customers/bulk-assignment",
    response_model=schemas.CustomerBulkAssignmentResult,
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)
def bulk_update_customer_assignment(
    payload: schemas.CustomerBulkAssignmentUpdate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    app_version: Optional[str] = Depends(_app_version),
):
    """Reassign the same route / telesales / sales-rep across N customers
    in one atomic transaction. Per-field `update_*` flags are required so
    an unchanged field doesn't accidentally overwrite each customer's
    existing value. One audit row per customer is written in the same
    transaction — all N commit together or none do."""
    if not payload.accounts:
        raise HTTPException(400, "accounts cannot be empty")
    if not (
        payload.update_route
        or payload.update_telesale
        or payload.update_salesrep
        or payload.update_on_hold
    ):
        raise HTTPException(
            400,
            "at least one of update_route, update_telesale, update_salesrep, "
            "update_on_hold must be true",
        )

    # Deduplicate and trim account values — the frontend occasionally
    # submits whitespace-padded ids, and dupes just waste cycles.
    accounts = sorted({a.strip() for a in payload.accounts if a and a.strip()})
    if not accounts:
        raise HTTPException(400, "accounts cannot be empty")

    # Validate salesrep_id up front so we don't start writing before
    # discovering it's bogus.
    new_rep_code: Optional[str] = None
    if payload.update_salesrep and payload.salesrep_id is not None:
        rep = db.execute(
            text("SELECT idSalesRep, Code FROM dbo.SalesRep WHERE idSalesRep = :id"),
            {"id": payload.salesrep_id},
        ).mappings().first()
        if not rep:
            raise HTTPException(400, f"Sales rep id {payload.salesrep_id} does not exist")
        new_rep_code = (rep["Code"] or "").strip() or None

    # Snapshot every customer's current state in one round-trip; also
    # confirms every submitted account exists. If any is missing, fail
    # the whole thing before writing a single row.
    rows = db.execute(
        text(
            """
            SELECT CL.Account         AS account,
                   CL.DCLink          AS dclink,
                   CL.ulARRoute       AS prev_route,
                   CL.ulARTelesalesRep AS prev_telesale,
                   CL.RepID           AS prev_salesrep_id,
                   REP.Code           AS prev_salesrep_code,
                   ISNULL(CL.On_Hold, 0) AS prev_on_hold
            FROM dbo.Client CL
            LEFT JOIN dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
            WHERE CL.Account IN :accounts
            """
        ).bindparams(bindparam("accounts", expanding=True)),
        {"accounts": accounts},
    ).mappings().all()
    found = {(r["account"] or "").strip() for r in rows}
    missing = [a for a in accounts if a not in found]
    if missing:
        raise HTTPException(
            400,
            f"Accounts not found: {', '.join(missing[:10])}"
            + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""),
        )

    def _norm(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    # Precompute the "new" values per customer. Flags off → carry prev.
    new_route_val = _norm(payload.route) if payload.update_route else None
    new_telesale_val = _norm(payload.telesale) if payload.update_telesale else None
    new_rep_id_val = payload.salesrep_id if payload.update_salesrep else None
    new_on_hold_val = (1 if payload.on_hold else 0) if payload.update_on_hold else None
    note = (payload.note or "").strip()[:200] or None

    # Build the UPDATE SET dynamically — only set the columns the user
    # actually asked to change. Avoids writing a column back to itself
    # (which would spuriously tick off any trigger watching for changes).
    set_parts = []
    if payload.update_route:
        set_parts.append("ulARRoute = :route")
    if payload.update_telesale:
        set_parts.append("ulARTelesalesRep = :telesale")
    if payload.update_salesrep:
        set_parts.append("RepID = :salesrep_id")
    if payload.update_on_hold:
        set_parts.append("On_Hold = :on_hold")
    update_sql = text(
        f"UPDATE dbo.Client SET {', '.join(set_parts)} WHERE Account = :account"
    )

    audit_sql = text(
        """
        INSERT INTO [SOAssistant].dbo._tb_SOCustomerAssignmentAudit
            (iCustomerDCLink, cCustomerAccount,
             cPrevRoute, cNewRoute,
             cPrevTelesale, cNewTelesale,
             iPrevSalesRepId, iNewSalesRepId,
             cPrevSalesRepCode, cNewSalesRepCode,
             bPrevOnHold, bNewOnHold,
             cUsername, cAppVersion, cNote)
        VALUES
            (:dclink, :account,
             :prev_route, :new_route,
             :prev_telesale, :new_telesale,
             :prev_rep_id, :new_rep_id,
             :prev_rep_code, :new_rep_code,
             :prev_on_hold, :new_on_hold,
             :username, :app_version, :note)
        """
    )

    try:
        for curr in rows:
            account = (curr["account"] or "").strip()
            prev_route = _norm(curr["prev_route"])
            prev_telesale = _norm(curr["prev_telesale"])
            prev_rep_id = (
                int(curr["prev_salesrep_id"])
                if curr["prev_salesrep_id"] is not None
                else None
            )
            prev_rep_code = _norm(curr["prev_salesrep_code"])
            prev_on_hold = 1 if int(curr["prev_on_hold"] or 0) else 0

            # Audit: always capture all four before/after. When a flag is
            # false the "new" is the "prev" so the row makes sense in
            # isolation (anyone reading the audit sees the state after).
            db.execute(
                audit_sql,
                {
                    "dclink": int(curr["dclink"]),
                    "account": account,
                    "prev_route": prev_route,
                    "new_route": new_route_val if payload.update_route else prev_route,
                    "prev_telesale": prev_telesale,
                    "new_telesale": new_telesale_val if payload.update_telesale else prev_telesale,
                    "prev_rep_id": prev_rep_id,
                    "new_rep_id": new_rep_id_val if payload.update_salesrep else prev_rep_id,
                    "prev_rep_code": prev_rep_code,
                    "new_rep_code": new_rep_code if payload.update_salesrep else prev_rep_code,
                    "prev_on_hold": prev_on_hold,
                    "new_on_hold": new_on_hold_val if payload.update_on_hold else prev_on_hold,
                    "username": username,
                    "app_version": app_version,
                    "note": note,
                },
            )

            # Only touch the columns the user actually asked to change.
            params: dict = {"account": account}
            if payload.update_route:
                params["route"] = new_route_val
            if payload.update_telesale:
                params["telesale"] = new_telesale_val
            if payload.update_salesrep:
                params["salesrep_id"] = new_rep_id_val
            if payload.update_on_hold:
                params["on_hold"] = new_on_hold_val
            db.execute(update_sql, params)

        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Bulk update failed, no changes applied: {e}")

    return schemas.CustomerBulkAssignmentResult(updated_count=len(rows))

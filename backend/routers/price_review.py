"""Price Review reports (supervisor screen, next to the orders waiting for
review). Same SQL as sql/diagnostics/broken_price_list_rows.sql.

PERFORMANCE: both run ONLY when a supervisor opens the tab (no timers, no
auto-refresh). The broken-rows query reads the price list for the two DCs
only (warehouses 48 / 49); the PO-differences query reads the small
_tb_SOImportPriceDecision table plus keyed lookups, then re-prices each
customer once through the normal price resolver. Only OPEN lines are
re-priced: settled ones (fixed / within 5c / dismissed with a reason) are
archived on that load, so the work doesn't grow with every import.
"""
import logging
from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from auth import get_current_user, require_min_role
from database import get_db
from services.order_import.validate import (
    PO_ABOVE_OURS_TOL,
    PRICE_TOL_INCL,
    _norm,
    _stock_info,
    _tax_rates,
    load_customer,
)
from services.pricing import inclusive_unit_price, resolve_customer_prices

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/price-review",
    tags=["price-review"],
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)


class BrokenPriceRow(BaseModel):
    effect: str
    price_list: str
    whse: str
    stock_code: str
    description: str = ""
    stored_excl: float
    stored_incl: float
    vat_pct: float
    correct_incl: float
    diff: float
    customers: int


class PoDiffLine(BaseModel):
    """One imported PO line whose PO price differed from ours.

    status:
      PROBLEM    ours today is above the PO, the PO is more than 5c above
                 ours, or there's no price at all: needs fixing
      TOLERANCE  the PO is up to 5c above ours (the import charges ours
                 automatically): a difference, not a problem   -> archived
      FIXED      ours today matches the PO                     -> archived
      DISMISSED  a supervisor chose not to fix it, with a      -> archived
                 reason (archive_note)
    """
    id: int
    status: str
    archived: bool = False
    archived_at: Optional[str] = None
    archived_by: str = ""
    archive_note: str = ""            # the dismiss reason
    customer_account: str
    po_number: str
    order_num: str
    stock_code: str
    description: str = ""
    whse: str = ""
    qty: float = 0.0
    price_list: str = ""
    source_then: str = ""
    our_price_then: float
    po_price: float
    charged: str = ""                 # PO / OURS / SET
    charged_price: float = 0.0
    charged_auto: bool = False
    # Ours TODAY for that customer, exactly as an import would charge it
    # (deal beats price list, customer deal beats group deal). For archived
    # lines: ours when it was archived.
    our_price_now: Optional[float] = None
    source_now: str = ""              # Price List / Customer Deal / Group Deal / None
    diff_now: Optional[float] = None  # PO - ours now
    deal_for: str = ""                # deal's customer account / customer group
    deal_from: Optional[str] = None
    deal_to: Optional[str] = None


class PoDiffPo(BaseModel):
    """One imported PO (= one sales order) with differing lines."""
    customer_group: str
    customer_account: str
    customer_name: str = ""
    po_number: str
    order_num: str
    imported_on: Optional[str] = None
    document_id: Optional[int] = None
    to_fix: int = 0
    tolerance: int = 0
    fixed: int = 0
    dismissed: int = 0


class PoDiffReport(BaseModel):
    pos: List[PoDiffPo]
    lines: List[PoDiffLine]          # the open (not archived) lines


BROKEN_SQL = """
SELECT Effect      = CASE WHEN CAST(PR.fInclPrice AS decimal(19, 6)) > C.CorrectIncl
                          THEN 'Evolution overcharges' ELSE 'Evolution undercharges' END,
       PriceList   = PN.cName,
       Whse        = WH.Code,
       StockCode   = STK.Code,
       Description = STK.Description_1,
       StoredExcl  = CAST(PR.fExclPrice AS float),
       StoredIncl  = CAST(PR.fInclPrice AS float),
       VatPct      = CAST(T.TaxRate AS float),
       CorrectIncl = CAST(C.CorrectIncl AS float),
       Diff        = CAST(CAST(PR.fInclPrice AS decimal(19, 6)) - C.CorrectIncl AS float),
       Customers   = (SELECT COUNT(*) FROM dbo.Client CL
                      WHERE CL.iARPriceListNameID = PR.iPriceListNameID)
FROM dbo._etblPriceListPrices PR
JOIN dbo._etblPriceListName PN ON PN.IDPriceListName = PR.iPriceListNameID
JOIN dbo.StkItem STK           ON STK.StockLink      = PR.iStockID
JOIN dbo.WhseMst WH            ON WH.WhseLink        = PR.iWarehouseID
CROSS APPLY (
    SELECT TOP 1 TX.TaxRate
    FROM dbo._etblStockDetails DET
    JOIN dbo.TaxRate TX ON TX.idTaxRate = DET.TTInvID
    WHERE DET.StockID = STK.StockLink AND DET.WhseID < 1
) T
CROSS APPLY (
    SELECT CorrectIncl = ROUND(CAST(PR.fExclPrice AS decimal(19, 6))
                               * (1 + CAST(T.TaxRate AS decimal(9, 4)) / 100), 2)
) C
WHERE PR.fExclPrice > 0
  AND STK.ItemActive = 1
  AND PR.iWarehouseID IN (48, 49)
  AND EXISTS (SELECT 1 FROM dbo.Client CL WHERE CL.iARPriceListNameID = PR.iPriceListNameID)
  AND ABS(CAST(ISNULL(PR.fInclPrice, 0) AS decimal(19, 6)) - C.CorrectIncl) > 0.02
ORDER BY CASE WHEN CAST(PR.fInclPrice AS decimal(19, 6)) > C.CorrectIncl THEN 0 ELSE 1 END,
         ABS(CAST(ISNULL(PR.fInclPrice, 0) AS decimal(19, 6)) - C.CorrectIncl) DESC,
         PN.cName, STK.Code
"""

PD_TABLE = "[SOAssistant].dbo._tb_SOImportPriceDecision"

_LINE_COLS = """
SELECT PD.idPriceDecision, PD.cCustomerAccount, PD.cPONumber, PD.cOrderNum,
       PD.cStockCode, Description = STK.Description_1, PD.cWhseCode,
       PriceList = PN.cName, PD.cPriceSource, PD.fQty, PD.fEvoIncl, PD.fPOIncl,
       PD.fChargedIncl, PD.cChoice, PD.bAuto,
       ArchivedAt = CONVERT(varchar(16), PD.dtArchivedAt, 120),
       PD.cArchiveStatus, PD.cArchivedBy, PD.fOursAtArchive, PD.cSourceAtArchive,
       PD.cArchiveNote
FROM {table} PD
LEFT JOIN dbo.Client CL   ON CL.Account = PD.cCustomerAccount COLLATE DATABASE_DEFAULT
LEFT JOIN dbo.StkItem STK ON STK.Code   = PD.cStockCode       COLLATE DATABASE_DEFAULT
LEFT JOIN dbo._etblPriceListName PN ON PN.IDPriceListName = CL.iARPriceListNameID
""".format(table=PD_TABLE)

OPEN_LINES_SQL = _LINE_COLS + "WHERE PD.dtArchivedAt IS NULL"

ARCHIVED_LINES_SQL = _LINE_COLS + """
WHERE PD.dtArchivedAt IS NOT NULL
  AND PD.cCustomerAccount = :account
  AND PD.cPONumber = :po
  AND PD.cOrderNum = :order_num
"""

ARCHIVE_SQL = f"""
UPDATE {PD_TABLE}
SET dtArchivedAt = SYSDATETIME(), cArchiveStatus = :status, cArchivedBy = :user,
    fOursAtArchive = :ours, cSourceAtArchive = :source
WHERE idPriceDecision = :id AND dtArchivedAt IS NULL
"""

# One row per imported PO (sales order) with its line counts. Only POs with
# open lines unless :all_pos = 1 ("show archived POs").
PO_SUMMARY_SQL = f"""
SELECT P.*, CustomerName = CL.Name,
       DocumentId = COALESCE(
           (SELECT TOP 1 D.idDocument FROM [SOAssistant].dbo._tb_SOImportDocument D
            WHERE D.cOrderNum = P.cOrderNum ORDER BY D.idDocument DESC),
           (SELECT TOP 1 R.iDocumentID FROM [SOAssistant].dbo._tb_SOImportReview R
            WHERE R.cOrderNum = P.cOrderNum AND R.iDocumentID IS NOT NULL))
FROM (
    SELECT cCustomerAccount, cPONumber, cOrderNum,
           Scope      = MAX(cScope),
           ImportedOn = CONVERT(varchar(10), MAX(dtCreatedAt), 23),
           OpenLines  = SUM(CASE WHEN dtArchivedAt IS NULL THEN 1 ELSE 0 END),
           Tolerance  = SUM(CASE WHEN cArchiveStatus = 'TOLERANCE'  THEN 1 ELSE 0 END),
           Fixed      = SUM(CASE WHEN cArchiveStatus = 'FIXED'      THEN 1 ELSE 0 END),
           Dismissed  = SUM(CASE WHEN cArchiveStatus = 'DISMISSED'  THEN 1 ELSE 0 END)
    FROM {PD_TABLE}
    GROUP BY cCustomerAccount, cPONumber, cOrderNum
    HAVING :all_pos = 1 OR SUM(CASE WHEN dtArchivedAt IS NULL THEN 1 ELSE 0 END) > 0
) P
LEFT JOIN dbo.Client CL ON CL.Account = P.cCustomerAccount COLLATE DATABASE_DEFAULT
"""

# The deal behind a resolved deal price, picked the same way as
# _fn_SOCustomerPriceAsOf (customer deal before group deal).
DEAL_SQL = """
SELECT AD.StockCode, AD.DealType, AD.Account, AD.CustGroup,
       DealFrom = CONVERT(varchar(10), AD.dEffDate, 23),
       DealTo   = CONVERT(varchar(10), AD.dExpDate, 23)
FROM [SOAssistant].dbo._vw_SOActiveDeals AD
WHERE AD.Account = :account
  AND AD.StockCode IN :codes
  AND AD.dEffDate <= :as_of
  AND AD.dExpDate >= :as_of
ORDER BY AD.StockCode, CASE AD.DealType WHEN 'Customer Deal' THEN 1 ELSE 2 END
"""


@router.get("/broken-rows", response_model=List[BrokenPriceRow])
def broken_rows(db: Session = Depends(get_db)):
    rows = db.execute(text(BROKEN_SQL)).mappings().all()
    return [
        BrokenPriceRow(
            effect=r["Effect"], price_list=r["PriceList"] or "", whse=r["Whse"] or "",
            stock_code=(r["StockCode"] or "").strip(),
            description=(r["Description"] or "").strip(),
            stored_excl=round(r["StoredExcl"] or 0, 2),
            stored_incl=round(r["StoredIncl"] or 0, 2),
            vat_pct=r["VatPct"] or 0, correct_incl=round(r["CorrectIncl"] or 0, 2),
            diff=round(r["Diff"] or 0, 2), customers=int(r["Customers"] or 0),
        )
        for r in rows
    ]


def difference_status(diff_now: Optional[float]) -> str:
    """PO - ours today -> PROBLEM / TOLERANCE / FIXED. Same thresholds as the
    import (validate._resolve_line_price), so "within tolerance" here means
    exactly "the import charges ours automatically"."""
    if diff_now is None:
        return "PROBLEM"                       # no price for the customer today
    if abs(diff_now) <= PRICE_TOL_INCL + 1e-6:
        return "FIXED"
    if 0 < diff_now <= PO_ABOVE_OURS_TOL + 1e-6:
        return "TOLERANCE"
    return "PROBLEM"


def _deals(db: Session, account: str, codes: List[str], as_of: date) -> Dict[str, dict]:
    if not codes:
        return {}
    rows = db.execute(
        text(DEAL_SQL).bindparams(bindparam("codes", expanding=True)),
        {"account": account, "codes": codes, "as_of": as_of},
    ).mappings().all()
    out: Dict[str, dict] = {}
    for r in rows:
        out.setdefault(_norm(r["StockCode"]), dict(r))
    return out


def _price_today(db: Session, account: str, whse: str, codes: List[str]) -> Dict[str, dict]:
    """Ours today for one customer, as validate_import would charge it."""
    today = date.today()
    customer = load_customer(db, account) or {}
    prices = resolve_customer_prices(db, account, codes, as_of=today,
                                     warehouse_code=whse or None)
    stock = _stock_info(db, codes)
    tax_ids = [i["tax_type_id"] for i in stock.values()] + [customer.get("def_tax_type_id")]
    rates = _tax_rates(db, [t for t in tax_ids if t])
    deals = _deals(db, account, [c for c, p in prices.items() if "Deal" in p.source], today)
    out: Dict[str, dict] = {}
    for code, p in prices.items():
        info = stock.get(_norm(code)) or {}
        tax_id = info.get("tax_type_id") or customer.get("def_tax_type_id")
        rate = rates.get(tax_id, 0.0) if tax_id else 0.0
        out[_norm(code)] = {
            "incl": inclusive_unit_price(p.excl_price, p.incl_price, rate),
            "source": p.source,
            "deal": deals.get(_norm(code)) or {},
        }
    return out


_CHARGED = {"PO": "PO", "EVOLUTION": "OURS", "OVERRIDE": "SET"}
ARCHIVE_STATUSES = ("TOLERANCE", "FIXED")


def line_status(diff_now: Optional[float], auto: bool) -> str:
    """Status of an open PO line, re-priced today.

    Settled lines split by what they were AT IMPORT, not by how exactly
    they match now:
      FIXED      was a problem (a supervisor decided its price) and ours
                 now matches the PO or is within 5c below it, i.e.
                 someone fixed it in Evolution
      TOLERANCE  was already within 5c at import (charged ours
                 automatically) and still is
    """
    status = difference_status(diff_now)
    if status in ARCHIVE_STATUSES:
        return "TOLERANCE" if auto else "FIXED"
    return status


def _group_name(scope: str) -> str:
    scope = scope or ""
    return scope[len("GROUP:"):] if scope.startswith("GROUP:") else "Non Corporate"


def _line(r, **extra) -> PoDiffLine:
    po = round(r["fPOIncl"] or 0, 2)
    status = r["cArchiveStatus"] or "PROBLEM"
    ours = r["fOursAtArchive"]
    fields = dict(
        id=int(r["idPriceDecision"]), status=status,
        archived=r["ArchivedAt"] is not None, archived_at=r["ArchivedAt"],
        archived_by=r["cArchivedBy"] or "",
        archive_note=r["cArchiveNote"] or "",
        customer_account=(r["cCustomerAccount"] or "").strip(),
        po_number=r["cPONumber"] or "", order_num=r["cOrderNum"] or "",
        stock_code=(r["cStockCode"] or "").strip(),
        description=(r["Description"] or "").strip(), whse=(r["cWhseCode"] or "").strip(),
        qty=float(r["fQty"] or 0), price_list=r["PriceList"] or "",
        source_then=r["cPriceSource"] or "", our_price_then=round(r["fEvoIncl"] or 0, 2),
        po_price=po, charged=_CHARGED.get(r["cChoice"] or "", r["cChoice"] or ""),
        charged_price=round(r["fChargedIncl"] or 0, 2), charged_auto=bool(r["bAuto"]),
        our_price_now=round(ours, 2) if ours is not None else None,
        source_now=r["cSourceAtArchive"] or "",
        diff_now=round(po - ours, 2) if ours is not None else None,
    )
    fields.update(extra)
    return PoDiffLine(**fields)


def _try(db: Session, what: str, fn) -> bool:
    """Archive writes are best effort: without the UPDATE grant (or before
    create_views.sql added the columns) the report still works, it just
    doesn't archive."""
    try:
        fn()
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("price-review: could not %s", what)
        return False


def _missing_schema() -> HTTPException:
    return HTTPException(
        503,
        "The PO price differences table needs updating: run sql/create_views.sql "
        "(adds the archive columns).",
    )


@router.get("/po-differences", response_model=PoDiffReport)
def po_differences(all_pos: bool = False, db: Session = Depends(get_db)):
    """Imported PO lines whose PO price differed from ours, as a PO tree.

    On every load (a supervisor opening the tab / Refresh — never on a timer):
      1. the open lines are re-priced as of today with the import's own
         resolver; FIXED / within-5c lines are archived with the price that
         settled them (DISMISSED lines are archived by a supervisor);
      2. returns the lines still open, plus one row per PO with its counts
         (POs with nothing open only when all_pos).
    Archived lines load per PO on demand (/po-differences/lines).
    """
    try:
        rows = db.execute(text(OPEN_LINES_SQL)).mappings().all()
    except Exception:  # noqa: BLE001 — table / columns not created yet
        db.rollback()
        logger.exception("price-review: PO differences query failed")
        raise _missing_schema()

    # One resolver call per (customer, warehouse).
    groups: Dict[tuple, List[str]] = {}
    for r in rows:
        key = ((r["cCustomerAccount"] or "").strip(), (r["cWhseCode"] or "").strip())
        groups.setdefault(key, []).append((r["cStockCode"] or "").strip())
    today_prices: Dict[tuple, Dict[str, dict]] = {
        key: _price_today(db, key[0], key[1], codes)
        for key, codes in groups.items() if key[0]
    }

    lines: List[PoDiffLine] = []
    for r in rows:
        account = (r["cCustomerAccount"] or "").strip()
        whse = (r["cWhseCode"] or "").strip()
        code = (r["cStockCode"] or "").strip()
        po = round(r["fPOIncl"] or 0, 2)
        now = today_prices.get((account, whse), {}).get(_norm(code))
        ours_now = now["incl"] if now else None
        diff_now = round(po - ours_now, 2) if ours_now is not None else None
        deal = now["deal"] if now else {}
        lines.append(_line(
            r, status=line_status(diff_now, bool(r["bAuto"])),
            our_price_now=ours_now, source_now=now["source"] if now else "None",
            diff_now=diff_now,
            deal_for=(deal.get("Account") if deal.get("DealType") == "Customer Deal"
                      else deal.get("CustGroup")) or "",
            deal_from=deal.get("DealFrom"), deal_to=deal.get("DealTo"),
        ))

    settled = [l for l in lines if l.status in ARCHIVE_STATUSES]
    if settled and _try(db, "archive settled lines", lambda: db.execute(
        text(ARCHIVE_SQL),
        [{"id": l.id, "status": l.status, "user": "system",
          "ours": l.our_price_now, "source": l.source_now[:20]} for l in settled],
    )):
        lines = [l for l in lines if l.status not in ARCHIVE_STATUSES]

    try:
        po_rows = db.execute(
            text(PO_SUMMARY_SQL), {"all_pos": 1 if all_pos else 0}
        ).mappings().all()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("price-review: PO summary query failed")
        raise _missing_schema()

    # Counts of the lines still open (archived ones come from the SQL).
    open_counts: Dict[tuple, Dict[str, int]] = {}
    for l in lines:
        c = open_counts.setdefault((l.customer_account, l.po_number, l.order_num), {})
        c[l.status] = c.get(l.status, 0) + 1
    pos = []
    for r in po_rows:
        key = ((r["cCustomerAccount"] or "").strip(), r["cPONumber"] or "", r["cOrderNum"] or "")
        c = open_counts.get(key, {})
        pos.append(PoDiffPo(
            customer_group=_group_name(r["Scope"]), customer_account=key[0],
            customer_name=(r["CustomerName"] or "").strip(),
            po_number=key[1], order_num=key[2], imported_on=r["ImportedOn"],
            document_id=r["DocumentId"],
            to_fix=c.get("PROBLEM", 0),
            tolerance=int(r["Tolerance"] or 0) + c.get("TOLERANCE", 0),
            fixed=int(r["Fixed"] or 0) + c.get("FIXED", 0),
            dismissed=int(r["Dismissed"] or 0),
        ))

    lines.sort(key=lambda x: (
        0 if x.status == "PROBLEM" else 1,
        -abs(x.diff_now if x.diff_now is not None else 999),
        x.stock_code,
    ))
    return PoDiffReport(pos=pos, lines=lines)


@router.get("/po-differences/lines", response_model=List[PoDiffLine])
def po_archived_lines(
    account: str, po: str, order_num: str, db: Session = Depends(get_db),
):
    """The archived lines of one PO, as they were when archived (no
    re-pricing). Loaded when a supervisor opens Within 5c / Fixed /
    Archived inside a PO."""
    try:
        rows = db.execute(
            text(ARCHIVED_LINES_SQL), {"account": account, "po": po, "order_num": order_num}
        ).mappings().all()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("price-review: archived lines query failed")
        raise _missing_schema()
    return [_line(r) for r in rows]


class DismissRequest(BaseModel):
    reason: str


DISMISS_SQL = f"""
UPDATE {PD_TABLE}
SET dtArchivedAt = SYSDATETIME(), cArchiveStatus = 'DISMISSED',
    cArchivedBy = :user, cArchiveNote = :reason
WHERE idPriceDecision = :id AND dtArchivedAt IS NULL
"""


@router.post("/po-differences/{line_id}/dismiss", response_model=PoDiffLine)
def dismiss_line(
    line_id: int,
    payload: DismissRequest,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    """A supervisor decides NOT to fix a price difference (e.g. the
    customer's PO price was never agreed). A reason is required; who, when
    and why are kept, and the line moves to "Dismissed" inside its PO."""
    reason = " ".join((payload.reason or "").split())
    if len(reason) < 3:
        raise HTTPException(400, "Give a reason for dismissing this price difference.")
    try:
        n = db.execute(
            text(DISMISS_SQL),
            {"id": line_id, "user": (username or "")[:50], "reason": reason[:200]},
        ).rowcount
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("price-review: dismiss failed for line %s", line_id)
        raise HTTPException(
            500, "Could not dismiss: run sql/create_views.sql (adds cArchiveNote) and "
                 "the grants block (UPDATE on _tb_SOImportPriceDecision).")
    if not n:
        raise HTTPException(409, "This line is no longer open: refresh the list.")
    logger.info("price-review: %s dismissed line %s: %s", username, line_id, reason)
    row = db.execute(
        text(_LINE_COLS + "WHERE PD.idPriceDecision = :id"), {"id": line_id}
    ).mappings().first()
    return _line(row)

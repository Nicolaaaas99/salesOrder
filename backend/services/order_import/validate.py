"""Check an imported purchase order against Evolution.

Stateless: the client sends the parsed document back on every call
(after the user maps a line or changes the delivery date) and gets a fresh
verdict. The create endpoint runs the same check server-side before it
places the order, so nothing the client says can skip a check.

Runs on button clicks only (upload / map / re-check / create) — never on a
timer — and every query is keyed by account, DCLink or a short code list.
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

import config
from routers.lookups import is_corporate_customer
from services.order_import.models import (
    ImportIssue,
    ImportLineResult,
    ImportTotals,
    ImportValidation,
    ItemMapping,
    OurPackRequest,
    PackFix,
    PackFixRequest,
    PriceChoice,
    StoreMatch,
)
from services.order_import.parsers import ParsedLine, ParsedOrder
from services.pricing import cents as _cents
from services.pricing import inclusive_unit_price, resolve_customer_prices

logger = logging.getLogger(__name__)

MAP_TABLE = "[SOAssistant].dbo._tb_SOCustomerItemMap"
PACK_FIX_TABLE = "[SOAssistant].dbo._tb_SOImportPackFix"
PACK_AUDIT_TABLE = "[SOAssistant].dbo._tb_SOPackSizeAudit"

# Unit price tolerance. The order is saved tax-INCLUSIVE, so the incl unit
# price is what the customer is charged — it must agree with the PO to the
# cent. (The excl is derived and stored by Evolution to 2 decimals; it is
# not compared.)
PRICE_TOL_INCL = 0.005
# Parsed lines must add up to the printed total (proves no line was missed).
PARSE_TOL = 0.05
# The PO may be up to this much per unit ABOVE our price: our price is
# charged and the agent can create the order (still flagged). A bigger gap,
# or our price above the PO's, needs a supervisor (submit for review).
# Rules agreed 2026-11-10; exactly 5c is allowed.
PO_ABOVE_OURS_TOL = 0.05

REVIEW_TABLE = "[SOAssistant].dbo._tb_SOImportReview"

# The crate / container stock. Never charged to the customer, so a PO line
# for it is left off the order and its amount taken off the PO total. Same
# code the Order Assistant treats as crates (frontend utils/history.ts).
CRATE_STOCK_CODE = "999000"


def _norm(code: Optional[str]) -> str:
    return (code or "").strip().upper()


def _po_unit(po_incl: float) -> float:
    """The PO's incl unit price, to the cent, rounded DOWN. Boxer prints
    whole cents (unchanged); Pick n Pay prints only line totals, so its unit
    price can be a fraction of a cent (142.84 / 12 = 11.9033). Rounding down
    means "ours matches the PO" can never charge more than the PO line
    (11.905 -> 11.90, not 11.91, which would be 12 x 11.91 = 6c over)."""
    return math.floor(round((po_incl or 0) * 100, 6)) / 100


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

def load_customer(db: Session, account: str) -> Optional[dict]:
    row = db.execute(
        text(
            """
            SELECT LTRIM(RTRIM(CL.Account))                  AS account,
                   CL.Name                                   AS name,
                   CL.DCLink                                 AS dclink,
                   LTRIM(RTRIM(ISNULL(CL.ucARStoreCode, ''))) AS store_code,
                   LTRIM(RTRIM(ISNULL(CL.ulARCorpGroup, ''))) AS corp_group,
                   CL.iDefTaxTypeID                          AS def_tax_type_id
            FROM dbo.Client CL
            WHERE CL.Account = :account
            """
        ),
        {"account": (account or "").strip()},
    ).mappings().first()
    return dict(row) if row else None


def mapping_scope(customer: dict) -> str:
    """Mappings are shared by the whole corporate group (every Boxer store
    uses the same codes); customers without a group get their own."""
    group = (customer.get("corp_group") or "").strip()
    if group and group.lower() != "non corporate":
        return f"GROUP:{group}"[:120]
    return f"ACCOUNT:{customer['account']}"[:120]


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

def load_mappings(db: Session, scope: str) -> List[ItemMapping]:
    rows = db.execute(
        text(
            f"""
            SELECT idItemMap, cScope, cCustomerAccount, cCustCode, cCustItemCode,
                   cCustDescription, cStockCode
            FROM {MAP_TABLE}
            WHERE cScope = :scope
            ORDER BY COALESCE(dtUpdatedAt, dtCreatedAt) DESC, idItemMap DESC
            """
        ),
        {"scope": scope},
    ).mappings().all()
    return [
        ItemMapping(
            id=int(r["idItemMap"]),
            scope=r["cScope"],
            customer_account=(r["cCustomerAccount"] or "").strip(),
            supplier_code=r["cCustCode"] or "",
            item_code=r["cCustItemCode"] or "",
            description=r["cCustDescription"] or "",
            stock_code=(r["cStockCode"] or "").strip(),
        )
        for r in rows
    ]


def find_mapping(
    mappings: List[ItemMapping], supplier_code: str, item_code: str
) -> Optional[ItemMapping]:
    """Most specific first: both codes > supplier code > item code.
    `mappings` is newest-first, so the latest decision wins in each tier."""
    sc, ic = _norm(supplier_code), _norm(item_code)
    if sc and ic:
        for m in mappings:
            if _norm(m.supplier_code) == sc and _norm(m.item_code) == ic:
                return m
    if sc:
        for m in mappings:
            if _norm(m.supplier_code) == sc:
                return m
    if ic:
        for m in mappings:
            if _norm(m.item_code) == ic:
                return m
    return None


def mapping_conflict(
    db: Session, scope: str, supplier_code: str, item_code: str, stock_code: str
) -> Optional[dict]:
    """Another of the customer's items already mapped to `stock_code` in this
    scope (each customer item must map to its own stock item), or None."""
    return db.execute(
        text(
            f"""
            SELECT TOP 1 idItemMap, cCustCode, cCustItemCode, cCustDescription
            FROM {MAP_TABLE}
            WHERE cScope = :scope AND cStockCode = :stock
              AND NOT (cCustCode = :code AND cCustItemCode = :item)
            """
        ),
        {
            "scope": scope,
            "stock": stock_code.strip()[:20],
            "code": (supplier_code or "").strip()[:40],
            "item": (item_code or "").strip()[:40],
        },
    ).mappings().first()


def upsert_mapping(
    db: Session,
    scope: str,
    supplier_code: str,
    item_code: str,
    description: str,
    stock_code: str,
    username: str,
    customer_account: str,
) -> int:
    """Insert or update one mapping. `customer_account` is the customer the
    mapping was captured for — recorded on the row for traceability; the
    mapping itself applies to the whole scope (corporate group)."""
    params = {
        "scope": scope,
        "account": (customer_account or "").strip()[:20] or None,
        "code": (supplier_code or "").strip()[:40],
        "item": (item_code or "").strip()[:40],
        "descr": (description or "").strip()[:200] or None,
        "stock": stock_code.strip()[:20],
        "user": (username or "")[:50],
    }
    existing = db.execute(
        text(
            f"SELECT idItemMap FROM {MAP_TABLE} "
            "WHERE cScope = :scope AND cCustCode = :code AND cCustItemCode = :item"
        ),
        params,
    ).mappings().first()
    if existing:
        db.execute(
            text(
                f"""
                UPDATE {MAP_TABLE}
                SET cStockCode = :stock, cCustDescription = :descr,
                    cCustomerAccount = :account,
                    cUpdatedBy = :user, dtUpdatedAt = SYSDATETIME()
                WHERE idItemMap = :id
                """
            ),
            {**params, "id": existing["idItemMap"]},
        )
        map_id = int(existing["idItemMap"])
    else:
        row = db.execute(
            text(
                f"""
                INSERT INTO {MAP_TABLE}
                    (cScope, cCustomerAccount, cCustCode, cCustItemCode,
                     cCustDescription, cStockCode, cCreatedBy)
                OUTPUT INSERTED.idItemMap
                VALUES (:scope, :account, :code, :item, :descr, :stock, :user)
                """
            ),
            params,
        ).first()
        map_id = int(row[0])
    db.commit()
    return map_id


# ---------------------------------------------------------------------------
# Evolution lookups
# ---------------------------------------------------------------------------

def _stock_info(db: Session, codes: List[str]) -> Dict[str, dict]:
    """Active, non-service stock items (same source as the item picker),
    keyed by upper-cased code."""
    codes = sorted({c for c in codes if c})
    if not codes:
        return {}
    rows = db.execute(
        text(
            """
            SELECT StockCode                                                  AS code,
                   MAX(StockDesc)                                             AS description,
                   MAX(CAST(IsWhseItem AS int))                               AS is_whse_item,
                   MAX(DefaultTaxTypeId)                                      AS tax_type_id,
                   MAX(PackSize)                                              AS pack_size,
                   STRING_AGG(WhseCode, ',') WITHIN GROUP (ORDER BY WhseCode) AS warehouse_codes
            FROM [SOAssistant].dbo._vw_SOInventory
            WHERE StockCode IN :codes
            GROUP BY StockCode
            """
        ).bindparams(bindparam("codes", expanding=True)),
        {"codes": codes},
    ).mappings().all()
    out = {}
    for r in rows:
        out[_norm(r["code"])] = {
            "code": r["code"].strip(),
            "description": r["description"] or "",
            "is_whse_item": bool(r["is_whse_item"]),
            "tax_type_id": int(r["tax_type_id"]) if r["tax_type_id"] else None,
            "pack_size": pack_int(r.get("pack_size")),
            "warehouse_codes": [
                _norm(c) for c in (r["warehouse_codes"] or "").split(",") if c
            ],
        }
    return out


def pack_int(value) -> int:
    """Our pack size from StkItem.ulIIPerCrate, a VARCHAR that can be NULL,
    '' or '0': anything that isn't a whole number above 0 is 1 (no pack),
    the same as the item picker / Order Assistant read it."""
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 1
    return n if n > 0 else 1


# ---------------------------------------------------------------------------
# Pack sizes
# ---------------------------------------------------------------------------

def load_pack_fixes(db: Session, scope: str) -> List[PackFix]:
    """Supervisors' decisions on the retailer's pack sizes. Before the table
    exists (create_views.sql not run yet) imports carry on without them."""
    try:
        rows = db.execute(
            text(
                f"""
                SELECT idPackFix, cScope, cCustCode, cCustItemCode, fDocPack, fPack
                FROM {PACK_FIX_TABLE}
                WHERE cScope = :scope
                """
            ),
            {"scope": scope},
        ).mappings().all()
    except Exception:
        logger.warning("order-import: pack fixes unavailable (run create_views.sql)", exc_info=True)
        db.rollback()
        return []
    return [
        PackFix(
            id=int(r["idPackFix"]), scope=r["cScope"],
            supplier_code=r["cCustCode"] or "", item_code=r["cCustItemCode"] or "",
            doc_pack=float(r["fDocPack"]), pack=float(r["fPack"]),
        )
        for r in rows
    ]


def find_pack_fix(fixes: List[PackFix], pl: ParsedLine) -> Optional[PackFix]:
    """The decision for this item of theirs, while the PO still prints the
    same pack it was made for (a retailer that fixes its setup no longer
    matches)."""
    sc, ic = _norm(pl.supplier_code), _norm(pl.item_code)
    printed = pl.pack_size or 1.0
    for f in fixes:
        if (_norm(f.supplier_code) == sc and _norm(f.item_code) == ic
                and abs(f.doc_pack - printed) < 1e-6):
            return f
    return None


def apply_pack(pl: ParsedLine, pack: float) -> ParsedLine:
    """Read the line again with another pack size: units = packs x pack,
    unit prices = the PO's line amounts / units (the line amounts are what
    the retailer will pay, whichever pack is right)."""
    packs = pl.pack_qty if pl.pack_qty is not None else pl.qty
    units = round(packs * pack, 4)
    upd = {
        "qty": units,
        "pack_size": pack if pack != 1 else None,
        "pack_qty": packs if pack != 1 else None,
    }
    if units:
        if pl.line_excl:
            upd["unit_excl"] = round(pl.line_excl / units, 4)
        if pl.line_incl:
            upd["unit_incl"] = round(pl.line_incl / units, 4)
    return pl.model_copy(update=upd)


def upsert_pack_fix(
    db: Session, scope: str, req: PackFixRequest, username: str
) -> int:
    params = {
        "scope": scope,
        "code": (req.supplier_code or "").strip()[:40],
        "item": (req.item_code or "").strip()[:40],
        "doc": round(req.doc_pack, 2),
        "pack": round(req.pack, 2),
        "descr": (req.description or "").strip()[:200] or None,
        "stock": (req.stock_code or "").strip()[:20] or None,
        "acct": (req.customer_account or "").strip()[:20] or None,
        "user": username[:50],
    }
    existing = db.execute(
        text(
            f"SELECT idPackFix FROM {PACK_FIX_TABLE} "
            "WHERE cScope = :scope AND cCustCode = :code AND cCustItemCode = :item "
            "AND fDocPack = :doc"
        ),
        params,
    ).mappings().first()
    if existing:
        db.execute(
            text(
                f"""
                UPDATE {PACK_FIX_TABLE}
                SET fPack = :pack, cCustDescription = :descr, cStockCode = :stock,
                    cCustomerAccount = :acct, cUpdatedBy = :user, dtUpdatedAt = sysdatetime()
                WHERE idPackFix = :id
                """
            ),
            {**params, "id": existing["idPackFix"]},
        )
        return int(existing["idPackFix"])
    row = db.execute(
        text(
            f"""
            INSERT INTO {PACK_FIX_TABLE}
                (cScope, cCustCode, cCustItemCode, fDocPack, fPack, cCustDescription,
                 cStockCode, cCustomerAccount, cCreatedBy)
            OUTPUT INSERTED.idPackFix
            VALUES (:scope, :code, :item, :doc, :pack, :descr, :stock, :acct, :user)
            """
        ),
        params,
    ).first()
    return int(row[0])


def delete_pack_fix(db: Session, fix_id: int) -> bool:
    res = db.execute(
        text(f"DELETE FROM {PACK_FIX_TABLE} WHERE idPackFix = :id"), {"id": fix_id}
    )
    return (res.rowcount or 0) > 0


def set_our_pack(db: Session, req: OurPackRequest, username: str) -> Optional[str]:
    """Set StkItem.ulIIPerCrate (OUR pack size) in Evolution and log it in
    _tb_SOPackSizeAudit. Returns the old value; raises LookupError when the
    stock code doesn't exist. The caller commits (both writes or neither)."""
    code = req.stock_code.strip()
    rows = db.execute(
        text("SELECT Code, ulIIPerCrate FROM dbo.StkItem WHERE Code = :code"),
        {"code": code},
    ).mappings().all()
    if len(rows) != 1:
        raise LookupError(code)
    old = rows[0]["ulIIPerCrate"]
    db.execute(
        text("UPDATE dbo.StkItem SET ulIIPerCrate = :pack WHERE Code = :code"),
        {"pack": str(int(req.pack)), "code": rows[0]["Code"]},
    )
    db.execute(
        text(
            f"""
            INSERT INTO {PACK_AUDIT_TABLE}
                (cStockCode, cOldPack, cNewPack, cChangedBy, cCustomerAccount, cPoNumber, cNote)
            VALUES (:code, :old, :new, :user, :acct, :po, :note)
            """
        ),
        {
            "code": rows[0]["Code"].strip(), "old": None if old is None else str(old)[:50],
            "new": str(int(req.pack)), "user": username[:50],
            "acct": (req.customer_account or "").strip()[:20] or None,
            "po": (req.po_number or "").strip()[:50] or None,
            "note": (req.note or "").strip()[:400] or None,
        },
    )
    return None if old is None else str(old)


def _tax_rates(db: Session, ids: List[int]) -> Dict[int, float]:
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    rows = db.execute(
        text("SELECT idTaxRate AS id, TaxRate AS rate FROM dbo.TaxRate WHERE idTaxRate IN :ids")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()
    return {int(r["id"]): float(r["rate"] or 0) for r in rows}


def _duplicate_orders(db: Session, dclink: int, ext_order: str) -> List[str]:
    """Live (not cancelled) sales orders for this customer that already
    carry this PO number. Seeks on AccountID first."""
    if not ext_order:
        return []
    rows = db.execute(
        text(
            """
            SELECT DISTINCT OrderNum
            FROM dbo.InvNum
            WHERE AccountID = :dclink
              AND DocType = 4
              AND DocState IN (1, 3, 4)
              AND ExtOrderNum = :ext
            """
        ),
        {"dclink": dclink, "ext": ext_order.strip()},
    ).mappings().all()
    return sorted({(r["OrderNum"] or "").strip() for r in rows if r["OrderNum"]})


def _customers_with_store_code(db: Session, store_code: str) -> List[StoreMatch]:
    rows = db.execute(
        text(
            """
            SELECT TOP 5 LTRIM(RTRIM(Account)) AS account, Name AS name
            FROM dbo.Client
            WHERE LTRIM(RTRIM(ucARStoreCode)) = :code
            ORDER BY Account
            """
        ),
        {"code": store_code.strip()},
    ).mappings().all()
    return [StoreMatch(account=r["account"], name=r["name"] or "") for r in rows]


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def check_store(db: Session, customer: dict, doc_store_code: str):
    """SAP Branch on the PO vs Client.ucARStoreCode.

    Returns (MATCH | MISMATCH | UNVERIFIED, customers that DO carry the PO's
    store code, issue or None). A mismatch blocks; a store code that can't
    be compared (blank on either side) only warns."""
    cust_store = (customer.get("store_code") or "").strip()
    doc_store = (doc_store_code or "").strip()
    if not doc_store:
        return "UNVERIFIED", [], ImportIssue(
            code="STORE_UNVERIFIED", blocking=False,
            message="The order has no store code, so the customer could not be verified. "
                    "Check that you chose the right store (OK Foods: the store number "
                    "is in the e-mail address the order came from).",
        )
    if not cust_store:
        return "UNVERIFIED", [], ImportIssue(
            code="STORE_UNVERIFIED", blocking=False,
            message=f"{customer['account']} has no store code (ucARStoreCode) in "
                    f"Evolution, so it could not be checked against {doc_store}.",
        )
    if _norm(cust_store) == _norm(doc_store):
        return "MATCH", [], None
    matches = _customers_with_store_code(db, doc_store)
    hint = (
        " Store " + doc_store + " belongs to "
        + ", ".join(f"{m.account} ({m.name})" for m in matches) + "."
        if matches else ""
    )
    return "MISMATCH", matches, ImportIssue(
        code="STORE_MISMATCH",
        message=f"This order is for store {doc_store}, but {customer['account']}'s "
                f"store code is {cust_store}.{hint}",
    )


def _resolve_line_price(
    lr: ImportLineResult,
    po_incl: float,
    evo_incl: float,
    override: Optional[float],
) -> tuple:
    """Decide which incl unit price a line charges. Sets the price_* fields
    on `lr` and returns (charged incl, blocking problem or None).

    Rules (meeting 2026-11-10):
      * PO and ours agree (< half a cent): charge it.
      * PO up to 5c ABOVE ours: charge ours, automatically (flagged).
      * PO more than 5c above ours, or ours ABOVE the PO (even 1c): the line
        needs review — only a supervisor can set its price.
      * Supervisor price (`override`, SUPERVISOR / ADMIN only — the caller
        drops overrides from agents): charged as given, any line.
    """
    po = _po_unit(po_incl) if po_incl else 0.0
    diff = po - evo_incl            # > 0: the PO is above our price
    # + 1e-6 absorbs float noise (16.00 - 15.99 = 0.010000000000001563).
    lr.price_differs = abs(diff) > PRICE_TOL_INCL + 1e-6

    if override is not None and override > 0:
        charged = _cents(override)
        # The PO button sends the PO's own (possibly fractional) price:
        # charge it rounded DOWN, never up. Only an exact match snaps —
        # "within 1c" also caught ours / a typed price 1c above the PO
        # and silently turned it back into the PO price.
        if po and abs(override - po_incl) < 1e-6:
            charged = po
        lr.price_override = charged
        if po and abs(charged - po) <= PRICE_TOL_INCL + 1e-6:
            lr.price_choice = "PO"
        elif abs(charged - evo_incl) <= PRICE_TOL_INCL + 1e-6:
            lr.price_choice = "EVOLUTION"
        else:
            lr.price_choice = "OVERRIDE"
        if po and charged > po + PRICE_TOL_INCL + 1e-6:
            lr.notes = [f"Charged above the PO price (R{po:,.2f}): claim risk."]
        return charged, None

    if not lr.price_differs:
        return evo_incl, None
    if po <= 0:
        lr.needs_review = True
        return evo_incl, f"No PO price; ours is R{evo_incl:,.2f}."
    if 0 < diff <= PO_ABOVE_OURS_TOL + 1e-6:
        lr.price_choice, lr.price_auto = "EVOLUTION", True
        return evo_incl, None

    lr.needs_review = True
    if diff < 0:
        return evo_incl, f"Ours R{evo_incl:,.2f} is R{-diff:,.2f} above the PO's R{po:,.2f}."
    return evo_incl, f"PO R{po:,.2f} is R{diff:,.2f} above ours R{evo_incl:,.2f}."


def _pending_reviews(db: Session, account: str, po_number: str) -> List[int]:
    """Reviews still waiting for this customer + PO number."""
    if not (po_number or "").strip():
        return []
    try:
        rows = db.execute(
            text(
                f"""
                SELECT idReview FROM {REVIEW_TABLE}
                WHERE cStatus = 'PENDING'
                  AND cCustomerAccount = :acc AND cPONumber = :po
                """
            ),
            {"acc": account, "po": po_number.strip()},
        ).all()
    except Exception:  # noqa: BLE001 — table not created yet: no reviews
        db.rollback()
        return []
    return [int(r[0]) for r in rows]


def validate_import(
    db: Session,
    customer: dict,
    doc: ParsedOrder,
    delivery_date: date,
    warehouse_code: Optional[str],
    price_overrides: Optional[Dict[int, float]] = None,
    can_override_prices: bool = False,
) -> ImportValidation:
    """`price_overrides` are only applied when `can_override_prices` (the
    caller is a SUPERVISOR / ADMIN) — an agent's overrides are ignored."""
    issues: List[ImportIssue] = []
    overrides = (price_overrides or {}) if can_override_prices else {}
    tolerance = config.ORDER_IMPORT_TOTAL_TOLERANCE

    # -- document-level ------------------------------------------------------
    for w in doc.warnings:
        issues.append(ImportIssue(code="DOCUMENT", message=w))

    if not is_corporate_customer(db, customer["account"]):
        issues.append(ImportIssue(
            code="NOT_CORPORATE",
            message=f"{customer['name']} is not a corporate customer. "
                    "Orders can only be imported for corporate customers.",
        ))

    # Store / branch check (the screen runs it on its own first, before the
    # order is shown; repeated here so create can never skip it).
    cust_store = (customer.get("store_code") or "").strip()
    store_check, store_matches, store_issue = check_store(db, customer, doc.store_code)
    if store_issue:
        issues.append(store_issue)

    if not (doc.order_no or "").strip():
        issues.append(ImportIssue(
            code="NO_PO_NUMBER",
            message="The PO number (external order number) isn't on this document: "
                    "type it in under Order details.",
        ))

    duplicates = _duplicate_orders(db, int(customer["dclink"]), doc.order_no)
    if duplicates:
        # Warning only (user's call, 2026-11-10): the order can still be
        # created — e.g. the first capture was cancelled or was incomplete.
        issues.append(ImportIssue(
            code="DUPLICATE", blocking=False,
            message=f"Order number {doc.order_no} has already been captured for "
                    f"{customer['account']} on sales order {', '.join(duplicates)}. "
                    "Make sure this isn't a second copy of the same order.",
        ))

    pending_reviews = _pending_reviews(db, customer["account"], doc.order_no)
    if pending_reviews:
        issues.append(ImportIssue(
            code="IN_REVIEW", blocking=False,
            message=f"Order number {doc.order_no} is already waiting for review "
                    f"(review {', '.join(f'#{r}' for r in pending_reviews)}).",
        ))

    if not warehouse_code:
        issues.append(ImportIssue(
            code="NO_WAREHOUSE",
            message="You don't have a default DC warehouse set, so prices can't be checked.",
        ))

    if doc.delivery_from and doc.delivery_to and not (
        doc.delivery_from <= delivery_date <= doc.delivery_to
    ):
        issues.append(ImportIssue(
            code="DELIVERY_WINDOW", blocking=False,
            message=f"Delivery date {delivery_date:%d/%m/%Y} is outside the order's "
                    f"delivery window ({doc.delivery_from:%d/%m/%Y} - {doc.delivery_to:%d/%m/%Y}).",
        ))

    # -- lines -----------------------------------------------------------------
    scope = mapping_scope(customer)
    mappings = load_mappings(db, scope)
    pack_fixes = load_pack_fixes(db, scope)

    # (parsed line, mapping or None, candidate code, pack fix or None, printed line)
    resolved: List[tuple] = []
    for pl in doc.lines:
        m = find_mapping(mappings, pl.supplier_code, pl.item_code)
        candidate = m.stock_code if m else (pl.supplier_code or "").strip()
        printed = pl
        fix = find_pack_fix(pack_fixes, pl)
        if fix and abs(fix.pack - (pl.pack_size or 1)) > 1e-6:
            pl = apply_pack(pl, fix.pack)       # their pack is wrong: re-read it
        resolved.append((pl, m, candidate, fix, printed))

    stock = _stock_info(db, [r[2] for r in resolved])
    found_codes = [stock[_norm(r[2])]["code"] for r in resolved if _norm(r[2]) in stock]
    prices = (
        resolve_customer_prices(
            db, customer["account"], found_codes,
            as_of=delivery_date, warehouse_code=warehouse_code,
        )
        if found_codes and warehouse_code else {}
    )
    prices_by_norm = {_norm(k): v for k, v in prices.items()}
    tax_ids = [s["tax_type_id"] for s in stock.values()] + [customer.get("def_tax_type_id")]
    rates = _tax_rates(db, [t for t in tax_ids if t])

    whse_norm = _norm(warehouse_code)
    lines: List[ImportLineResult] = []
    for pl, m, candidate, fix, printed in resolved:
        lr = ImportLineResult(
            line_no=pl.line_no,
            supplier_code=pl.supplier_code,
            item_code=pl.item_code,
            description=pl.description,
            qty=pl.qty,
            doc_unit_excl=pl.unit_excl,
            doc_unit_incl=pl.unit_incl,
            doc_vat_rate=pl.vat_rate,
            doc_line_excl=pl.line_excl,
            doc_line_incl=pl.line_incl,
            doc_pack_size=pl.pack_size,
            doc_pack_qty=pl.pack_qty,
            barcode=pl.barcode,
            mapping_id=m.id if m else None,
        )
        if fix:
            lr.pack_fix_id = fix.id
            lr.pack_fix = (
                "ACCEPTED" if abs(fix.pack - (printed.pack_size or 1)) < 1e-6 else "CORRECTED"
            )
            lr.doc_pack_printed = printed.pack_size or 1.0
            lr.doc_qty_printed = printed.qty
        line_issues: List[str] = []
        status = "OK"

        if pl.parse_warning or pl.qty <= 0:
            status = "PARSE_ERROR"
            line_issues.append(pl.parse_warning or "Quantity could not be read from the order.")

        info = stock.get(_norm(candidate))
        if info is None:
            if m:
                lr.match_source = "MAPPING"
                lr.stock_code = m.stock_code
                if status == "OK":
                    status = "ITEM_ISSUE"
                line_issues.append(
                    f"Mapped to {m.stock_code}, which is not an active stock item. Map it again."
                )
            else:
                if status == "OK":
                    status = "UNMATCHED"
                line_issues.append(
                    f"Supplier code '{pl.supplier_code}' is not one of our stock codes. "
                    "Choose the item to map it."
                    if pl.supplier_code else
                    "No supplier code on this line. Choose the item to map it."
                )
            lr.status = status
            lr.issues = line_issues
            lines.append(lr)
            continue

        lr.match_source = "MAPPING" if m else "DIRECT"
        lr.stock_code = info["code"]
        lr.stock_description = info["description"]

        lr.tax_type_id = info["tax_type_id"] or customer.get("def_tax_type_id")
        lr.tax_rate = rates.get(lr.tax_type_id, 0.0) if lr.tax_type_id else 0.0
        f = 1 + (lr.tax_rate or 0) / 100
        po_unit_incl, po_line_incl = pl.unit_incl, pl.line_incl
        if pl.prices_excl:
            # The PO printed EXCL prices only (OK Foods): the excl price IS
            # the PO price, and its incl is excl + the item's VAT rounded to
            # the cent, the way Evolution derives incl from excl. NOT rounded
            # down: 9.313 ex = 10.70995 incl is R10.71, the same as ours at
            # 9.31 ex; flooring it to 10.70 would flag a 1c "overcharge"
            # that isn't there (ours excl isn't above the PO's).
            po_unit_incl = _cents(round(pl.unit_excl * f, 6))
            po_line_incl = _cents(round(pl.line_excl * f, 6))
            lr.doc_unit_incl, lr.doc_line_incl = po_unit_incl, po_line_incl
            lr.doc_vat_rate = lr.tax_rate or 0
        elif not pl.vat_known:
            # The PO printed incl only (Pick n Pay): show its excl at the
            # item's VAT.
            lr.doc_vat_rate = lr.tax_rate or 0
            lr.doc_unit_excl = round(pl.unit_incl / f, 4)
            lr.doc_line_excl = round(pl.line_incl / f, 2)

        if _norm(info["code"]) == CRATE_STOCK_CODE:
            # Crates: never charged. Off the order and off the PO total; the
            # parse check still counts the line (proves nothing was missed).
            lr.excluded = True
            lr.status = "EXCLUDED"
            lr.notes = [
                f"Crates are not charged: left off the order, and "
                f"R{po_line_incl:,.2f} taken off the purchase order total."
            ]
            lines.append(lr)
            continue

        if info["is_whse_item"]:
            if whse_norm and whse_norm not in info["warehouse_codes"]:
                if status == "OK":
                    status = "ITEM_ISSUE"
                line_issues.append(
                    f"{info['code']} is not set up in your warehouse {warehouse_code}."
                )
            lr.warehouse_code = warehouse_code

        # -- pack size: theirs vs ours ----------------------------------------
        # (_stock_info always gives a pack size, 1 when ulIIPerCrate is blank.)
        # When the PO's case isn't our crate, the units are rounded DOWN to
        # whole crates of ours (4 x 10 = 40 -> 36 at 6): we never send more
        # than was ordered. Flagged, not blocking, unless nothing is left.
        qty = pl.qty
        our_pack = info.get("pack_size")
        lr.our_pack_size = our_pack
        doc_pack = pl.pack_size or 1.0
        if our_pack is None:
            pass
        elif doc_pack > 1 and abs(doc_pack - our_pack) > 1e-6:
            lr.pack_conflict = True
            if our_pack > 1:
                rounded = math.floor(qty / our_pack + 1e-9) * our_pack
                if abs(rounded - qty) > 1e-6:
                    lr.qty_before_pack = qty
                    qty = float(rounded)
                    lr.qty = qty
                if qty <= 0:
                    lr.pack_blocks = True
                    if status == "OK":
                        status = "PACK_MISMATCH"
                    line_issues.append(
                        f"The PO packs {doc_pack:g} per case and orders {pl.qty:g} units; "
                        f"our crate is {our_pack}, so rounding down leaves nothing. "
                        "Fix the pack size in Evolution (or capture this line manually), then re-check."
                    )
                elif lr.qty_before_pack is not None:
                    lr.notes = lr.notes + [
                        f"The PO packs {doc_pack:g} per case; ours is {our_pack}. "
                        f"{pl.qty:g} units rounded down to {qty:g} "
                        f"({qty / our_pack:g} crates of {our_pack})."
                    ]
                else:
                    lr.notes = lr.notes + [
                        f"The PO packs {doc_pack:g} per case; ours is {our_pack}. "
                        f"{qty:g} units is whole crates of ours: nothing rounded."
                    ]
            else:
                lr.notes = lr.notes + [
                    f"The PO packs {doc_pack:g} per case; our pack size isn't set "
                    "in Evolution, so the units aren't rounded."
                ]
        elif our_pack > 1 and abs(qty / our_pack - round(qty / our_pack)) > 1e-6:
            lr.off_pack = True
            lr.notes = lr.notes + [
                f"{qty:g} units isn't a whole number of our crates of {our_pack}: "
                "the order will show off-pack."
            ]

        price = prices_by_norm.get(_norm(info["code"]))
        if price is None:
            if status == "OK":
                status = "NO_PRICE"
            line_issues.append(
                f"No price for {customer['account']} on {info['code']} in "
                f"{warehouse_code or 'your warehouse'}. Fix the price in Evolution, then re-check."
            )
        else:
            lr.price_source = price.source
            lr.evo_unit_excl = price.excl_price
            # The order is saved tax-INCLUSIVE: this incl unit price is what
            # Evolution charges, and the line total is qty x incl exactly.
            # See services/pricing.inclusive_unit_price.
            evo_incl = inclusive_unit_price(price.excl_price, price.incl_price, lr.tax_rate or 0)
            lr.evo_unit_incl = evo_incl
            lr.evo_list_incl = price.incl_price
            lr.price_list_broken = price.price_list_broken

            charged_incl, price_problem = _resolve_line_price(
                lr, po_unit_incl, evo_incl, overrides.get(pl.line_no)
            )
            lr.unit_incl = charged_incl
            rate = lr.tax_rate or 0
            lr.line_incl = _cents(qty * charged_incl)
            lr.line_excl = _cents(lr.line_incl / (1 + rate / 100)) if rate else lr.line_incl
            lr.line_tax = round(lr.line_incl - lr.line_excl, 2)
            if price_problem:
                if status == "OK":
                    status = "PRICE_MISMATCH"
                line_issues.append(price_problem)

            if pl.vat_known and abs((lr.tax_rate or 0) - pl.vat_rate) > 0.5:
                if status == "OK":
                    status = "VAT_MISMATCH"
                line_issues.append(
                    f"VAT on the order is {pl.vat_rate:g}% but {info['code']} is taxed "
                    f"at {lr.tax_rate:g}% in Evolution."
                )

        lr.status = status
        lr.issues = line_issues
        lines.append(lr)

    # Two of the customer's lines on ONE of our stock items: one of them is
    # mapped wrong (each customer item maps to its own stock item). Flag
    # every line involved so the user picks which one to re-map.
    by_stock: Dict[str, List[ImportLineResult]] = {}
    for l in lines:
        if l.stock_code and not l.excluded and l.status != "UNMATCHED":
            by_stock.setdefault(_norm(l.stock_code), []).append(l)
    for same in by_stock.values():
        if len(same) < 2:
            continue
        for l in same:
            others = ", ".join(str(o.line_no) for o in same if o is not l)
            l.status = "ITEM_ISSUE"
            l.issues = [
                f"{l.stock_code} is also on line {others}. Each of the customer's items "
                "must map to its own stock item: change the wrong one."
            ] + l.issues

    bad_lines = [l for l in lines if l.status not in ("OK", "EXCLUDED")]
    if bad_lines:
        unmatched = sum(1 for l in bad_lines if l.status == "UNMATCHED")
        msg = f"{len(bad_lines)} of {len(lines)} lines need attention"
        if unmatched:
            msg += f" ({unmatched} not mapped to a stock item)"
        issues.append(ImportIssue(code="LINES", message=msg + "."))

    # -- totals ----------------------------------------------------------------
    totals = ImportTotals(
        doc_qty=doc.total_qty,
        doc_excl=doc.total_excl,
        doc_vat=doc.total_vat,
        doc_incl=doc.total_incl,
        parsed_qty=round(sum(l.qty for l in doc.lines), 4),
        parsed_incl=round(sum(l.line_incl for l in doc.lines), 2),
        parsed_excl=round(sum(l.line_excl for l in doc.lines), 2),
        order_qty=round(sum(l.qty for l in lines if l.unit_incl is not None), 4),
        order_excl=round(sum(l.line_excl for l in lines), 2),
        order_tax=round(sum(l.line_tax for l in lines), 2),
        order_incl=round(sum(l.line_incl for l in lines), 2),
        tolerance=tolerance,
        excluded_qty=round(sum(l.qty for l in lines if l.excluded), 4),
        excluded_incl=round(sum(l.doc_line_incl for l in lines if l.excluded), 2),
        # Lines charged at a price other than the PO's (needs-review lines
        # count at our price until a supervisor sets theirs).
        price_adjust_incl=round(sum(
            l.line_incl - _cents(l.qty * l.doc_unit_incl)
            for l in lines
            if l.unit_incl is not None and not l.excluded
            and abs(l.unit_incl - _po_unit(l.doc_unit_incl)) > PRICE_TOL_INCL + 1e-6
        ), 2),
        # Their pack corrected (units re-read) and units rounded down to our
        # crates: both move the PO's qty total.
        pack_adjust_qty=round(sum(
            (l.qty_before_pack if l.qty_before_pack is not None else l.qty) - l.doc_qty_printed
            for l in lines
            if l.pack_fix == "CORRECTED" and l.doc_qty_printed is not None
        ) + sum(
            l.qty - l.qty_before_pack for l in lines if l.qty_before_pack is not None
        ), 4),
        pack_round_incl=round(sum(
            _cents(l.qty * l.doc_unit_incl) - l.doc_line_incl
            for l in lines
            if l.qty_before_pack is not None and l.unit_incl is not None and not l.excluded
        ), 2),
        rounding_incl=round(sum(
            l.line_incl - (
                l.doc_line_incl if l.doc_line_incl and l.qty_before_pack is None
                else _cents(l.qty * l.doc_unit_incl)
            )
            for l in lines
            if l.unit_incl is not None and not l.excluded
            and abs(l.unit_incl - _po_unit(l.doc_unit_incl)) <= PRICE_TOL_INCL + 1e-6
        ), 2),
    )

    by_excl = any(l.prices_excl for l in doc.lines) and doc.total_excl is not None
    doc_total_incl = doc.total_incl
    if doc_total_incl is None and by_excl and lines and all(
        # Every line matched to an item (so its incl is known).
        l.doc_line_incl > 0 or not l.doc_line_excl for l in lines
    ):
        # Excl-only PO with no VAT or incl total (Spar): its incl is the sum
        # of its lines' incl, each worked out from the item's VAT. The excl
        # total (parse check below) still proves every line was read.
        doc_total_incl = round(sum(l.doc_line_incl for l in lines), 2)
        totals.doc_incl = doc_total_incl
        totals.doc_incl_from_lines = True

    parse_ok = False
    if by_excl or doc_total_incl is not None:
        # Excl-only layouts (OK Foods, Spar) are checked on excl: the lines
        # carry no incl until they're matched to an item's VAT. Checked even
        # before every line is mapped, so a missed line shows up at once.
        read, printed = (
            (totals.parsed_excl, doc.total_excl) if by_excl
            else (totals.parsed_incl, doc.total_incl)
        )
        parse_ok = abs(read - printed) <= PARSE_TOL and (
            doc.total_qty is None or abs(totals.parsed_qty - doc.total_qty) < 0.001
        )
        if not parse_ok:
            issues.append(ImportIssue(
                code="PARSE_TOTALS",
                message=f"The lines read from the document (qty {totals.parsed_qty:g}, "
                        f"R{read:,.2f}{' excl' if by_excl else ''}) don't add up to the "
                        f"order total (qty {doc.total_qty if doc.total_qty is not None else '?'}, "
                        f"R{printed:,.2f}{' excl' if by_excl else ''}). Some lines may not "
                        "have been read. Capture this order manually.",
            ))

    if doc_total_incl is not None:
        # Compare with the PO total less crates and adjusted for lines not
        # charged at the PO price.
        totals.expected_incl = round(
            doc_total_incl - totals.excluded_incl + totals.pack_round_incl
            + totals.price_adjust_incl + totals.rounding_incl, 2
        )
        totals.difference_incl = round(totals.order_incl - totals.expected_incl, 2)
        qty_ok = doc.total_qty is None or abs(
            totals.order_qty
            - (doc.total_qty - totals.excluded_qty + totals.pack_adjust_qty)
        ) < 0.001
        totals.agrees = (
            parse_ok and qty_ok and abs(totals.difference_incl) <= tolerance
        )
        # Only report the total separately when the lines themselves are
        # clean: with bad lines the difference is explained by them.
        if not totals.agrees and not bad_lines and parse_ok:
            issues.append(ImportIssue(
                code="TOTAL_MISMATCH",
                message=f"Order total R{totals.order_incl:,.2f} incl does not agree with "
                        f"the purchase order's R{totals.expected_incl:,.2f} "
                        + ("(after crates, pack rounding, price differences and rounding) "
                           if totals.expected_incl != doc_total_incl else "")
                        + 
                        f"(difference R{totals.difference_incl:,.2f}, "
                        f"tolerance R{tolerance:,.2f}).",
            ))
    else:
        totals.agrees = False

    can_create = (
        totals.agrees
        and not bad_lines
        and not any(i.blocking for i in issues)
    )
    # Only price lines stand in the way: the agent may hand it to
    # a supervisor.
    review_lines = [
        l for l in lines
        if l.needs_review and l.price_override is None
    ]
    can_submit_review = (
        not can_create
        and bool(review_lines)
        and all(l.status in ("OK", "EXCLUDED") or l in review_lines for l in lines)
        and all(l.status == "PRICE_MISMATCH" for l in review_lines)
        and totals.agrees
        and not any(i.blocking and i.code != "LINES" for i in issues)
    )

    return ImportValidation(
        document=doc,
        customer_account=customer["account"],
        customer_name=customer["name"] or "",
        customer_store_code=cust_store,
        store_check=store_check,
        store_code_customers=store_matches,
        warehouse_code=warehouse_code,
        price_date=delivery_date,
        duplicate_orders=duplicates,
        lines=lines,
        totals=totals,
        issues=issues,
        can_create=can_create,
        can_submit_review=can_submit_review,
        can_override_prices=can_override_prices,
        pending_reviews=pending_reviews,
    )


# ---------------------------------------------------------------------------
# Price-decision log (feeds the Broken Prices report)
# ---------------------------------------------------------------------------

DECISION_TABLE = "[SOAssistant].dbo._tb_SOImportPriceDecision"


def log_price_decisions(
    db: Session,
    result: ImportValidation,
    customer: dict,
    order_no: str,
    username: str,
) -> int:
    """Record the price charged on every line whose PO price differed from
    ours, for an order that was just CREATED. Called after the order is in
    Evolution, so a failure here is logged, never raised."""
    rows = [
        {
            "user": username[:50],
            "acc": customer["account"],
            "scope": mapping_scope(customer)[:120],
            "po": (result.document.order_no or "")[:50],
            "ord": (order_no or "")[:50],
            "stock": l.stock_code,
            "whse": l.warehouse_code,
            "src": (l.price_source or "")[:20],
            "qty": l.qty,
            "evo_excl": l.evo_unit_excl,
            "evo_incl": l.evo_unit_incl,
            "po_incl": l.doc_unit_incl,
            "charged": l.unit_incl,
            "choice": l.price_choice,
            "auto": 1 if l.price_auto else 0,
        }
        for l in result.lines
        if (l.price_differs or l.price_override is not None)
        and l.price_choice and not l.excluded
    ]
    if not rows:
        return 0
    try:
        db.execute(
            text(
                f"""
                INSERT INTO {DECISION_TABLE}
                    (cUsername, cCustomerAccount, cScope, cPONumber, cOrderNum,
                     cStockCode, cWhseCode, cPriceSource, fQty, fEvoExcl, fEvoIncl,
                     fPOIncl, fChargedIncl, cChoice, bAuto)
                VALUES
                    (:user, :acc, :scope, :po, :ord, :stock, :whse, :src, :qty,
                     :evo_excl, :evo_incl, :po_incl, :charged, :choice, :auto)
                """
            ),
            rows,
        )
        db.commit()
    except Exception:  # noqa: BLE001 — the order already exists in Evolution
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.exception(
            "order-import: could not log %d price decision(s) for order %s",
            len(rows), order_no,
        )
        return 0
    return len(rows)

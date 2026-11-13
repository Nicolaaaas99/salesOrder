"""Order Import — turn a customer's emailed purchase order (PDF) into a
sales order.

Flow (frontend: OrderImportScreen):
  1. POST /order-import/parse      PDF -> ParsedOrder; the PDF itself is kept
                                   in _tb_SOImportDocument (document_id)
  2. POST /order-import/validate   ParsedOrder + customer + delivery date ->
                                   line matches, price / VAT / store /
                                   duplicate / total checks
  3. POST /order-import/mappings   user resolves an unmatched line; stored
                                   so the same code matches next time
  4. POST /order-import/create     re-validates server-side, then places
                                   the order through the normal create path
                                   (credit / terms checks, audit, contact log)
  4b. POST /order-import/reviews   a line's PO price is too far from ours: the
                                   agent hands the import to a supervisor, who
                                   sets the prices and creates it (create with
                                   review_id) or rejects it.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import _ROLE_RANK, get_current_user, get_current_user_role
from database import get_db
from routers.auth import _default_warehouse_for
from routers.sales_orders import place_order
from services.order_import import store
from services.order_import.models import (
    ImportCreateRequest,
    ImportReviewDetail,
    ImportReviewReject,
    ImportReviewSubmit,
    ImportReviewSummary,
    ImportValidateRequest,
    ImportValidation,
    ItemMapping,
    ItemMappingUpsert,
    OurPackRequest,
    OurPackResult,
    PackFix,
    PackFixRequest,
    StoreCheck,
)
from services.order_import.parsers import (
    DESCRIPTION_KEY_PREFIX,
    OrderParseError,
    ParsedOrder,
    parse_order_pdf,
)
from services.order_import.validate import (
    MAP_TABLE,
    _stock_info,
    check_store,
    delete_pack_fix,
    load_customer,
    load_mappings,
    log_price_decisions,
    mapping_conflict,
    mapping_scope,
    set_our_pack,
    upsert_mapping,
    upsert_pack_fix,
    validate_import,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/order-import",
    tags=["order-import"],
    dependencies=[Depends(get_current_user)],
)

MAX_PDF_BYTES = 10 * 1024 * 1024


def _app_version(
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
) -> Optional[str]:
    return (x_app_version or "").strip()[:32] or None


def _is_supervisor(role: str) -> bool:
    return _ROLE_RANK.get((role or "").upper(), -1) >= _ROLE_RANK["SUPERVISOR"]


def _customer_or_404(db: Session, account: str) -> dict:
    customer = load_customer(db, account)
    if not customer:
        raise HTTPException(404, f"Customer '{account}' not found")
    return customer


def _validate(db: Session, payload: ImportValidateRequest, username: str, role: str):
    customer = _customer_or_404(db, payload.customer_account)
    result = validate_import(
        db, customer, payload.document, payload.delivery_date,
        _default_warehouse_for(db, username),
        payload.price_overrides, _is_supervisor(role),
    )
    return customer, result


@router.post("/parse", response_model=ParsedOrder)
async def parse_order(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB.")
    try:
        doc = parse_order_pdf(data)
    except OrderParseError as e:
        raise HTTPException(422, str(e))
    # Keep the original PO so it can be opened later. A storage failure
    # (e.g. table not created yet) must not stop the import.
    try:
        doc.document_id = store.save_document(
            db, data, file.filename or "order.pdf", username, doc.order_no
        )
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("order-import: could not store the PDF %s", file.filename)
    logger.info(
        "order-import parse: file=%s format=%s order=%s store=%s lines=%d doc=%s",
        file.filename, doc.format_name, doc.order_no, doc.store_code, len(doc.lines),
        doc.document_id,
    )
    return doc


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, db: Session = Depends(get_db)):
    """The original PO PDF, shown inline in the browser."""
    doc = store.get_document(db, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    name = (doc["cFileName"] or "order.pdf").replace('"', "")
    return Response(
        content=bytes(doc["bData"]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@router.get("/store-check", response_model=StoreCheck)
def store_check(account: str, store_code: str = "", db: Session = Depends(get_db)):
    """Run straight after the PDF is read, before the order is shown: a PO
    for another store is stopped here and the user is offered the customer
    that does carry that store code. /validate and /create repeat the check."""
    customer = _customer_or_404(db, account)
    check, matches, issue = check_store(db, customer, store_code)
    return StoreCheck(
        customer_account=customer["account"],
        customer_name=customer["name"] or "",
        customer_store_code=(customer.get("store_code") or "").strip(),
        store_code=(store_code or "").strip(),
        store_check=check,
        store_code_customers=matches,
        message=issue.message if issue else None,
    )


@router.post("/validate", response_model=ImportValidation)
def validate_order(
    payload: ImportValidateRequest,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    return _validate(db, payload, username, role)[1]


@router.get("/mappings", response_model=List[ItemMapping])
def list_mappings(account: str, db: Session = Depends(get_db)):
    customer = _customer_or_404(db, account)
    return load_mappings(db, mapping_scope(customer))


@router.post("/mappings", response_model=ItemMapping)
def save_mapping(
    payload: ItemMappingUpsert,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    if not (payload.supplier_code or "").strip() and not (payload.item_code or "").strip():
        raise HTTPException(400, "The line has no supplier code or item code to map.")
    customer = _customer_or_404(db, payload.customer_account)
    info = _stock_info(db, [payload.stock_code.strip()])
    item = info.get(payload.stock_code.strip().upper())
    if not item:
        raise HTTPException(400, f"Stock item '{payload.stock_code}' is not an active item.")

    scope = mapping_scope(customer)
    clash = mapping_conflict(
        db, scope, payload.supplier_code, payload.item_code, item["code"]
    )
    if clash:
        # A description key (lines with no code, Spar) is a fingerprint:
        # name the item by its description instead.
        codes = " / ".join(
            c for c in (clash["cCustCode"], clash["cCustItemCode"])
            if c and not c.startswith(DESCRIPTION_KEY_PREFIX)
        )
        descr = (clash["cCustDescription"] or "").strip()
        if not codes:
            codes, descr = f'"{descr}"', ""
        raise HTTPException(
            409,
            f"{item['code']} is already mapped to the customer's item {codes}"
            + (f" ({descr})" if descr else "")
            + ". Each of the customer's items must map to its own stock item.",
        )
    map_id = upsert_mapping(
        db, scope, payload.supplier_code, payload.item_code,
        payload.description, item["code"], username, customer["account"],
    )
    logger.info(
        "order-import mapping: scope=%s supplier=%s item=%s -> %s by %s",
        scope, payload.supplier_code, payload.item_code, item["code"], username,
    )
    return ItemMapping(
        id=map_id, scope=scope, customer_account=customer["account"],
        supplier_code=(payload.supplier_code or "").strip(),
        item_code=(payload.item_code or "").strip(),
        description=(payload.description or "").strip(),
        stock_code=item["code"],
    )


@router.delete("/mappings/{map_id}", status_code=204)
def delete_mapping(map_id: int, db: Session = Depends(get_db)):
    db.execute(text(f"DELETE FROM {MAP_TABLE} WHERE idItemMap = :id"), {"id": map_id})
    db.commit()


# ---------------------------------------------------------------------------
# Pack sizes (supervisor / admin)
# ---------------------------------------------------------------------------

def _require_supervisor(role: str, what: str) -> None:
    if not _is_supervisor(role):
        raise HTTPException(403, f"Only a supervisor can {what}.")


@router.post("/pack-fixes", response_model=PackFix)
def save_pack_fix(
    payload: PackFixRequest,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    """THEIR pack size: correct it (the line is read with `pack`) or accept
    it (`pack` = `doc_pack`: their case and our crate differ, both right).
    Remembered for the corporate group while their PO prints `doc_pack`."""
    _require_supervisor(role, "change a retailer's pack size")
    if not (payload.supplier_code or "").strip() and not (payload.item_code or "").strip():
        raise HTTPException(400, "The line has no supplier code or item code.")
    if not (0 < payload.doc_pack <= 10000 and 0 < payload.pack <= 10000):
        raise HTTPException(400, "The pack size must be between 1 and 10 000.")
    if abs(payload.pack - round(payload.pack)) > 1e-9:
        raise HTTPException(400, "The pack size must be a whole number.")
    customer = _customer_or_404(db, payload.customer_account)
    scope = mapping_scope(customer)
    try:
        fix_id = upsert_pack_fix(db, scope, payload, username)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.exception("order-import pack fix failed")
        raise HTTPException(
            503, "Could not save the pack size (has create_views.sql been run?): " + str(e)[:200]
        ) from e
    logger.info(
        "order-import pack fix: scope=%s supplier=%s item=%s printed=%s -> %s by %s",
        scope, payload.supplier_code, payload.item_code, payload.doc_pack, payload.pack, username,
    )
    return PackFix(
        id=fix_id, scope=scope,
        supplier_code=(payload.supplier_code or "").strip(),
        item_code=(payload.item_code or "").strip(),
        doc_pack=payload.doc_pack, pack=payload.pack,
    )


@router.delete("/pack-fixes/{fix_id}", status_code=204)
def remove_pack_fix(
    fix_id: int,
    db: Session = Depends(get_db),
    role: str = Depends(get_current_user_role),
):
    _require_supervisor(role, "change a retailer's pack size")
    delete_pack_fix(db, fix_id)
    db.commit()


@router.post("/our-pack", response_model=OurPackResult)
def save_our_pack(
    payload: OurPackRequest,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    """OUR pack size: StkItem.ulIIPerCrate in Evolution. Used everywhere
    (Order Assistant pack snapping, forecasting), so supervisor only and
    logged in _tb_SOPackSizeAudit."""
    _require_supervisor(role, "change our pack size in Evolution")
    if not 1 <= payload.pack <= 10000:
        raise HTTPException(400, "The pack size must be between 1 and 10 000.")
    try:
        old = set_our_pack(db, payload, username)
        db.commit()
    except LookupError:
        db.rollback()
        raise HTTPException(404, f"Stock item '{payload.stock_code}' not found.")
    except Exception as e:
        db.rollback()
        logger.exception("order-import our pack update failed")
        raise HTTPException(
            503,
            "Could not update the pack size in Evolution (does the app's login have "
            "UPDATE on StkItem.ulIIPerCrate? See the grants block in create_views.sql): "
            + str(e)[:200],
        ) from e
    logger.info(
        "order-import OUR pack: %s %s -> %s by %s (PO %s, %s)",
        payload.stock_code, old, payload.pack, username, payload.po_number, payload.customer_account,
    )
    return OurPackResult(stock_code=payload.stock_code.strip(), old_pack=old, new_pack=payload.pack)


@router.post("/create", response_model=schemas.SalesOrderCreateResponse, status_code=201)
def create_imported_order(
    payload: ImportCreateRequest,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
    app_version: Optional[str] = Depends(_app_version),
):
    if not payload.dates_confirmed:
        raise HTTPException(400, "Confirm the order dates before creating the order.")

    review = None
    if payload.review_id is not None:
        if not _is_supervisor(role):
            raise HTTPException(403, "Only a supervisor can create an order from a review.")
        review = store.get_review(db, payload.review_id)
        if not review:
            raise HTTPException(404, "Review not found")
        if review.status != "PENDING":
            raise HTTPException(409, f"This review is already {review.status.lower()}.")

    customer, result = _validate(db, payload, username, role)
    if not result.can_create:
        if result.can_submit_review:
            raise HTTPException(
                400,
                "Some prices differ too much from the PO. Submit the order for review.",
            )
        problems = [i.message for i in result.issues if i.blocking]
        if not problems and not result.totals.agrees:
            problems = ["The order total does not agree with the purchase order."]
        raise HTTPException(400, "The order can't be created yet: " + " ".join(problems))

    order = schemas.SalesOrderCreate(
        customer_account=customer["account"],
        order_date=payload.order_date,
        due_date=payload.due_date or payload.order_date,
        invoice_date=payload.invoice_date or payload.delivery_date,
        delivery_date=payload.delivery_date,
        external_order=payload.document.order_no,
        discount_percent=0,
        # Inclusive: unit_price below is the incl price Evolution charges
        # (qty x incl exactly): our price, or a supervisor's price.
        tax_mode="Inclusive",
        description=((payload.description or "").strip() or "Sales Order")[:40],
        lines=[
            schemas.SalesOrderLineInput(
                stock_code=l.stock_code,
                description=(l.stock_description or None) and l.stock_description[:100],
                warehouse_code=l.warehouse_code,
                quantity=l.qty,
                unit_price=l.unit_incl,
                tax_type_id=l.tax_type_id,
                discount_percent=0,
            )
            for l in result.lines
            if not l.excluded   # crates: not charged
        ],
        source="ORDER_IMPORT",
    )
    logger.info(
        "order-import create: customer=%s po=%s lines=%d incl=%.2f review=%s by %s",
        customer["account"], payload.document.order_no, len(order.lines),
        result.totals.order_incl, payload.review_id, username,
    )
    # Normal create path: corporate / credit / terms checks, Evolution SDK
    # save, audit row (cSourceApp = ORDER_IMPORT) and PLACED contact-log row.
    created = place_order(order, db=db, username=username, app_version=app_version)

    # Bookkeeping after the order exists in Evolution: never fail the request.
    log_price_decisions(db, result, customer, created.order_no, username)
    try:
        store.link_document(db, payload.document.document_id, customer["account"],
                            created.order_no)
        if review:
            store.close_review(db, review.id, "CREATED", username, order_num=created.order_no)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("order-import: post-create bookkeeping failed for %s", created.order_no)
    return created


# ---------------------------------------------------------------------------
# Reviews: imports whose prices need a supervisor
# ---------------------------------------------------------------------------

@router.post("/reviews", response_model=ImportReviewSummary, status_code=201)
def submit_review(
    payload: ImportReviewSubmit,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    # Re-checked as an agent: overrides never count towards a submission.
    payload.price_overrides = {}
    customer, result = _validate(db, payload, username, "AGENT")
    if not result.can_submit_review:
        raise HTTPException(
            400,
            "Only orders whose sole problem is a PO price that differs from ours "
            "can be submitted for review. Fix the other items first.",
        )
    if result.pending_reviews:
        raise HTTPException(
            409, f"PO {payload.document.order_no} is already waiting for review "
                 f"(#{result.pending_reviews[0]}).",
        )
    review_id = store.create_review(
        db, result, payload.document, payload.order_date, payload.description,
        payload.note, username,
    )
    try:
        store.link_document(db, payload.document.document_id, customer["account"])
    except Exception:  # noqa: BLE001
        db.rollback()
    logger.info(
        "order-import review submitted: #%d customer=%s po=%s by %s",
        review_id, customer["account"], payload.document.order_no, username,
    )
    return store.get_review(db, review_id)


@router.get("/reviews", response_model=List[ImportReviewSummary])
def list_reviews(
    status: Optional[str] = "PENDING",
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    """Supervisors see every review; agents see the ones they submitted."""
    status = (status or "").upper() or None
    if status and status not in ("PENDING", "CREATED", "REJECTED"):
        raise HTTPException(400, "Unknown status")
    return store.list_reviews(
        db, status, None if _is_supervisor(role) else username
    )


@router.get("/reviews/pending-count")
def pending_review_count(
    db: Session = Depends(get_db),
    role: str = Depends(get_current_user_role),
):
    """Badge count for supervisors / admins; agents always get 0 (no query).
    Declared before /reviews/{review_id} so the path isn't read as an id."""
    return {"pending": store.count_pending_reviews(db) if _is_supervisor(role) else 0}


@router.get("/reviews/{review_id}", response_model=ImportReviewDetail)
def get_review(
    review_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    review = store.get_review(db, review_id)
    if not review or (
        not _is_supervisor(role)
        and review.submitted_by.lower() != (username or "").lower()
    ):
        raise HTTPException(404, "Review not found")
    return review


@router.post("/reviews/{review_id}/reject", response_model=ImportReviewSummary)
def reject_review(
    review_id: int,
    payload: ImportReviewReject,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    if not _is_supervisor(role):
        raise HTTPException(403, "Only a supervisor can reject a review.")
    if not (payload.note or "").strip():
        raise HTTPException(400, "Give a reason for rejecting the order.")
    if not store.close_review(db, review_id, "REJECTED", username, note=payload.note):
        raise HTTPException(409, "This review is no longer pending.")
    return store.get_review(db, review_id)

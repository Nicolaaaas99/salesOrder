"""Request / response models for the Order Import endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from services.order_import.parsers import ParsedOrder


LineStatus = Literal[
    "OK",
    "UNMATCHED",        # no mapping and the supplier code isn't one of our stock codes
    "ITEM_ISSUE",       # matched, but the item can't go on the order (inactive / not in DC)
    "NO_PRICE",         # no price for this customer in the agent's DC
    "PRICE_MISMATCH",   # PO price too far from ours: needs a supervisor (submit for review)
    "PACK_MISMATCH",    # the PO's case size differs from ours and rounding down to our crates leaves 0: fix it in Evolution
    "VAT_MISMATCH",     # order VAT rate differs from the item's tax type
    "PARSE_ERROR",      # the line couldn't be read reliably from the PDF
    "EXCLUDED",         # crates (999000): not charged — left off the order and the PO total
]

# What a line whose PO price differs from ours is charged at:
#   EVOLUTION  our price (automatic when the PO is up to 5c above ours)
#   PO         the PO price (supervisor)
#   OVERRIDE   a price the supervisor typed
PriceChoice = Literal["PO", "EVOLUTION", "OVERRIDE"]

ReviewStatus = Literal["PENDING", "CREATED", "REJECTED"]


class ImportIssue(BaseModel):
    code: str
    message: str
    blocking: bool = True


class ImportLineResult(BaseModel):
    line_no: int
    # As printed on the customer's order
    supplier_code: str = ""
    item_code: str = ""
    description: str = ""
    qty: float = 0.0
    doc_unit_excl: float = 0.0
    doc_unit_incl: float = 0.0
    doc_vat_rate: float = 0.0
    doc_line_excl: float = 0.0
    doc_line_incl: float = 0.0
    # Packs x pack size, when the PO orders in packs (Pick n Pay). After a
    # supervisor's correction of THEIR pack (pack_fix), the corrected pack.
    doc_pack_size: Optional[float] = None
    doc_pack_qty: Optional[float] = None
    # -- PO vs our pack size (StkItem.ulIIPerCrate) ----------------------------
    # Our pack size; None until the line is matched to an item.
    our_pack_size: Optional[int] = None
    # The PO prints a case size (> 1) that isn't ours. Flagged, not blocking:
    # the units are rounded DOWN to whole crates of ours (4 x 10 = 40 -> 36
    # at 6 per crate) and qty_before_pack keeps the PO's units.
    pack_conflict: bool = False
    qty_before_pack: Optional[float] = None
    # Rounding down leaves nothing (1 x 4 with our crate of 6): blocks create
    # (PACK_MISMATCH) until the pack size is fixed in Evolution.
    pack_blocks: bool = False
    # The units aren't a whole number of our crates (warning only: the
    # order will show "off-pack").
    off_pack: bool = False
    # A remembered supervisor decision on THEIR pack (_tb_SOImportPackFix):
    #   CORRECTED  their pack is wrong: the line is read with fix_pack
    #   ACCEPTED   their case and our crate differ, and both are right
    pack_fix: Optional[Literal["CORRECTED", "ACCEPTED"]] = None
    pack_fix_id: Optional[int] = None
    # The pack the PO prints (before a correction) and its units.
    doc_pack_printed: Optional[float] = None
    doc_qty_printed: Optional[float] = None
    barcode: str = ""
    # How we resolved it
    match_source: Literal["MAPPING", "DIRECT", "NONE"] = "NONE"
    mapping_id: Optional[int] = None
    stock_code: Optional[str] = None
    stock_description: str = ""
    warehouse_code: Optional[str] = None
    tax_type_id: Optional[int] = None
    tax_rate: Optional[float] = None
    price_source: Optional[str] = None
    evo_unit_excl: Optional[float] = None
    # What the inclusive order charges per unit incl
    # (services.pricing.inclusive_unit_price).
    evo_unit_incl: Optional[float] = None
    # The incl stored on the price list / deal. Equals evo_unit_incl unless
    # the stored incl is more than 2c off excl + VAT.
    evo_list_incl: Optional[float] = None
    # Price-list row whose stored incl doesn't match excl + the item's VAT.
    # Warning only: the line still charges evo_unit_incl and can be created.
    price_list_broken: bool = False
    # -- PO vs Evolution price (rules agreed 2026-11-10) ----------------------
    #   same price            -> charged, OK
    #   PO up to 5c above ours -> our price charged automatically (price_auto)
    #   PO more than 5c above, or ours above the PO -> needs_review: the agent
    #   can only submit the order for review; a supervisor sets the price.
    # True when the PO's incl differs from evo_unit_incl (stays flagged).
    price_differs: bool = False
    needs_review: bool = False
    # What the line is charged at (None = not decided: needs review).
    price_choice: Optional[PriceChoice] = None
    price_auto: bool = False
    # Supervisor's price for this line (incl), when one was set.
    price_override: Optional[float] = None
    # What the order line charges per unit incl (sent to Evolution).
    unit_incl: Optional[float] = None
    # Not charged (crates). Left off the order; its PO amount comes off the
    # PO total the order is compared with.
    excluded: bool = False
    # Informational (not a problem): e.g. which price is charged and why.
    notes: List[str] = []
    # Our line amounts (charged price x qty)
    line_excl: float = 0.0
    line_tax: float = 0.0
    line_incl: float = 0.0
    status: LineStatus = "UNMATCHED"
    issues: List[str] = []


class ImportTotals(BaseModel):
    # Printed on the document
    doc_qty: Optional[float] = None
    doc_excl: Optional[float] = None
    doc_vat: Optional[float] = None
    doc_incl: Optional[float] = None
    # True when the PO prints no incl total (Spar: excl only) and doc_incl is
    # the sum of its lines' incl at each item's VAT.
    doc_incl_from_lines: bool = False
    # Sum of the lines we read off the document (proves nothing was missed)
    parsed_qty: float = 0.0
    parsed_incl: float = 0.0
    parsed_excl: float = 0.0
    # Our order: Evolution prices x qty, matched lines only
    order_qty: float = 0.0
    order_excl: float = 0.0
    order_tax: float = 0.0
    order_incl: float = 0.0
    # Adjustments to the PO total before comparing:
    #  - excluded lines (crates) come off;
    #  - lines charged at a price other than the PO's (ours, or a supervisor's
    #    price) move it by the difference (price_adjust_incl).
    excluded_qty: float = 0.0
    excluded_incl: float = 0.0
    price_adjust_incl: float = 0.0
    # Lines charged the PO's own price, rounded DOWN to the cent per unit
    # (Pick n Pay line totals / OK Foods excl prices are fractions of a
    # cent): what that rounding takes off. Never positive.
    rounding_incl: float = 0.0
    # Units added (+) or removed (-) by correcting a retailer's pack size:
    # the PO's printed qty total is compared after this.
    pack_adjust_qty: float = 0.0
    # PO value of the units cut by rounding down to our crates (never positive).
    pack_round_incl: float = 0.0
    expected_incl: Optional[float] = None   # PO total after the adjustments
    difference_incl: Optional[float] = None
    tolerance: float = 0.0
    agrees: bool = False


class StoreMatch(BaseModel):
    account: str
    name: str


class ImportValidation(BaseModel):
    document: ParsedOrder
    customer_account: str
    customer_name: str = ""
    customer_store_code: str = ""
    store_check: Literal["MATCH", "MISMATCH", "UNVERIFIED"] = "UNVERIFIED"
    # Customers whose ucARStoreCode equals the document's store code —
    # offered as "did you mean" when the chosen customer doesn't match.
    store_code_customers: List[StoreMatch] = []
    warehouse_code: Optional[str] = None
    price_date: date
    duplicate_orders: List[str] = []
    lines: List[ImportLineResult] = []
    totals: ImportTotals
    issues: List[ImportIssue] = []
    can_create: bool = False
    # Everything is fine except lines that need a supervisor: the agent may
    # submit the order for review.
    can_submit_review: bool = False
    # The caller may set prices (SUPERVISOR / ADMIN).
    can_override_prices: bool = False
    # Reviews already waiting for this PO number (same customer).
    pending_reviews: List[int] = []


class PackFixRequest(BaseModel):
    """A supervisor's decision on a retailer's pack size for one of its
    items: `pack` = the pack to read it with; `pack` = `doc_pack` accepts
    that their case and our crate differ."""
    customer_account: str
    supplier_code: str = ""
    item_code: str = ""
    doc_pack: float
    pack: float
    description: str = ""
    stock_code: Optional[str] = None


class PackFix(BaseModel):
    id: int
    scope: str
    supplier_code: str = ""
    item_code: str = ""
    doc_pack: float
    pack: float


class OurPackRequest(BaseModel):
    """A supervisor fixes OUR pack size (StkItem.ulIIPerCrate) for an item."""
    stock_code: str
    pack: int
    customer_account: Optional[str] = None
    po_number: Optional[str] = None
    note: Optional[str] = None


class OurPackResult(BaseModel):
    stock_code: str
    old_pack: Optional[str] = None
    new_pack: int


class ImportValidateRequest(BaseModel):
    customer_account: str
    document: ParsedOrder
    # Delivery date the user confirmed; prices / deals are resolved as of
    # this date (same rule as the session date elsewhere in the app).
    delivery_date: date
    # Supervisor prices (incl) per line_no. Ignored for agents.
    price_overrides: Dict[int, float] = {}
    # Set when a supervisor works on an order submitted for review.
    review_id: Optional[int] = None


class ImportCreateRequest(ImportValidateRequest):
    order_date: date
    due_date: Optional[date] = None
    invoice_date: Optional[date] = None
    description: Optional[str] = None
    # The user ticked "dates confirmed" on the screen.
    dates_confirmed: bool = False


class ImportReviewSubmit(ImportCreateRequest):
    note: Optional[str] = None


class ImportReviewReject(BaseModel):
    note: str


class ImportReviewSummary(BaseModel):
    id: int
    status: ReviewStatus
    customer_account: str
    customer_name: str = ""
    po_number: str = ""
    delivery_date: Optional[date] = None
    po_total_incl: Optional[float] = None
    review_lines: int = 0
    document_id: Optional[int] = None
    submitted_by: str = ""
    submitted_at: Optional[datetime] = None
    submit_note: str = ""
    reviewed_by: str = ""
    reviewed_at: Optional[datetime] = None
    review_note: str = ""
    order_num: str = ""


class ImportReviewDetail(ImportReviewSummary):
    document: ParsedOrder
    order_date: Optional[date] = None
    description: str = ""


class ItemMappingUpsert(BaseModel):
    customer_account: str
    supplier_code: str = ""
    item_code: str = ""
    description: str = ""
    stock_code: str


class ItemMapping(BaseModel):
    id: int
    scope: str
    # Customer the mapping was captured for (it applies to the whole scope).
    customer_account: str = ""
    supplier_code: str
    item_code: str
    description: str = ""
    stock_code: str


class StoreCheck(BaseModel):
    customer_account: str
    customer_name: str = ""
    customer_store_code: str = ""
    store_code: str = ""
    store_check: Literal["MATCH", "MISMATCH", "UNVERIFIED"]
    # Customers whose ucARStoreCode equals the PO's store code.
    store_code_customers: List[StoreMatch] = []
    message: Optional[str] = None

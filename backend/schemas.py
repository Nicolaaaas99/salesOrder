from datetime import date
from typing import Optional, List
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str


class CustomerSummary(BaseModel):
    account: str
    name: str


class CustomerDetail(BaseModel):
    account: str
    name: str
    delivery_address: str = ""
    postal_address: str = ""
    contact_name: str = ""
    contact_tel: str = ""
    contact_email: str = ""
    default_tax_type_id: Optional[int] = None
    credit_limit: float = 0.0
    total_outstanding: float = 0.0
    total_ordered: float = 0.0
    available_credit: float = 0.0
    over_credit_limit: bool = False
    check_terms: bool = False
    terms_code: str = ""
    terms_age_type: int = 0  # 0=Invoice date, 1=Statement (month-end)
    terms_days: int = 0
    terms_cutoff_date: Optional[date] = None
    overdue_amount: float = 0.0
    over_terms: bool = False


class InventoryItem(BaseModel):
    code: str
    description: str
    uom: str = ""
    is_uom_item: bool = False
    is_whse_item: bool = False
    default_tax_type_id: Optional[int] = None
    available: float = 0.0
    warehouse_codes: List[str] = []
    pack_size: int = 1                    # ulIIPerCrate — qty must be a multiple of this


class CustomerPrice(BaseModel):
    stock_code: str
    excl_price: float = 0.0
    incl_price: float = 0.0
    source: str = "None"        # 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'


class CustomerPricesRequest(BaseModel):
    account: str
    stock_codes: List[str]


class PackSizesRequest(BaseModel):
    stock_codes: List[str]


class Warehouse(BaseModel):
    code: str
    description: str


class TaxType(BaseModel):
    id: int
    code: str
    description: str
    rate: float


class Project(BaseModel):
    code: str
    description: str


class SettlementTerm(BaseModel):
    code: str
    description: str


class SalesOrderLineInput(BaseModel):
    stock_code: str
    description: Optional[str] = Field(None, max_length=100)
    warehouse_code: Optional[str] = None
    quantity: float = Field(gt=0)
    unit_price: float = Field(gt=0)
    tax_type_id: Optional[int] = None
    discount_percent: float = 0.0
    project_code: Optional[str] = None


class SalesOrderCreate(BaseModel):
    customer_account: str
    order_date: Optional[date] = None
    due_date: Optional[date] = None
    invoice_date: Optional[date] = None
    external_order: Optional[str] = None
    default_project_code: Optional[str] = None
    discount_percent: float = 0.0
    tax_mode: str = "Exclusive"
    description: Optional[str] = None
    lines: List[SalesOrderLineInput]


class SalesOrderCreateResponse(BaseModel):
    order_no: str
    invoice_no: Optional[str] = None


class SalesOrderSearchResult(BaseModel):
    order_no: str
    external_order: str = ""
    description: str = ""
    invoice_date: Optional[date] = None
    order_date: Optional[date] = None
    customer_account: str = ""
    customer_name: str = ""
    doc_state: int = 0
    doc_state_label: str = ""
    total_incl: float = 0.0
    is_archived: bool = False


class SalesOrderLineDetail(BaseModel):
    stock_code: str = ""
    item_description: str = ""
    description: str = ""
    warehouse_code: str = ""
    warehouse_codes: List[str] = []   # warehouses this stock is set up in
    is_whse_item: bool = False
    quantity: float = 0.0
    # Always the EXCL unit price — invariant on the frontend LineRow too.
    unit_price: float = 0.0
    # The actual INCL unit price as stored by Evolution (may differ from
    # excl × (1+rate) when the price list overrides it). Used by the frontend
    # to display Incl mode exactly without floating-point drift.
    unit_price_incl: float = 0.0
    tax_type_id: Optional[int] = None
    discount_percent: float = 0.0
    project_code: str = ""


class SalesOrderDetail(BaseModel):
    order_no: str
    customer_account: str
    customer_name: str = ""
    order_date: Optional[date] = None
    due_date: Optional[date] = None
    invoice_date: Optional[date] = None
    external_order: str = ""
    default_project_code: str = ""
    discount_percent: float = 0.0
    tax_mode: str = "Exclusive"
    description: str = ""
    doc_state: int = 0
    doc_state_label: str = ""
    is_archived: bool = False
    lines: List[SalesOrderLineDetail] = []


# ---------- Customer invoice history (telesales dashboard) ----------

class HistoryRoute(BaseModel):
    id: str
    display: str


class HistoryCustomer(BaseModel):
    id: str
    display: str


class HistoricalOrderLine(BaseModel):
    stockCode: str
    description: str
    category: str = "Uncategorized"
    quantity: float = 0.0
    exclAmount: float = 0.0
    vat: float = 0.0
    inclAmount: float = 0.0
    # True when this (order × product) sum includes any pending-but-not-invoiced
    # qty — tells the UI/suggestion logic the number isn't settled yet.
    onOrder: bool = False


class HistoricalOrder(BaseModel):
    orderNum: str
    orderDate: Optional[str] = None
    route: str = ""
    customerName: str = ""
    items: List[HistoricalOrderLine] = []
    itemCount: int = 0
    totalQuantity: float = 0.0
    totalExclAmount: float = 0.0
    totalVat: float = 0.0
    totalInclAmount: float = 0.0


class OrderSuggestion(BaseModel):
    stock_code: str
    description: str
    category: str = "Uncategorized"
    # Already-adjusted qty: `base_median − on_order_qty`, snapped to pack size.
    # Suggestions whose adjusted value rounds to 0 crates are dropped on the
    # backend, so a returned suggestion always has suggested_quantity > 0.
    suggested_quantity: float
    # EXCL unit price. `unit_price_incl` is the actual incl stored by Evolution
    # (may differ from excl × (1+rate) if the price list has an override).
    unit_price: float
    unit_price_incl: float = 0.0
    non_zero_weeks: int
    total_weeks: int
    # Pending (open-SO) qty for this stock + customer — shown next to the
    # Sug cell so the rep sees why the number is reduced.
    on_order_qty: float = 0.0
    # Invoiced qty for this stock in the CURRENT week — also subtracted from
    # the suggestion so the number doesn't jump back up the moment a pending
    # SO gets invoiced. Together with `on_order_qty` these form "already
    # covered for this week".
    current_week_invoiced_qty: float = 0.0
    # Inventory enrichment (from _vw_SOInventory)
    item_description: str = ""            # canonical description from StkItem
    uom: str = ""
    is_uom_item: bool = False
    is_whse_item: bool = False
    default_tax_type_id: Optional[int] = None
    available: float = 0.0                # summed QtyOnHand across warehouses
    warehouse_codes: List[str] = []       # warehouses this stock is set up in
    pack_size: int = 1                    # ulIIPerCrate — suggested_quantity is already snapped to a multiple of this
    price_source: str = "None"            # 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'

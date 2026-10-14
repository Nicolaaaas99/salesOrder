export type CustomerSummary = { account: string; name: string };

export type CustomerDetail = {
  account: string;
  name: string;
  delivery_address: string;
  postal_address: string;
  contact_name: string;
  contact_tel: string;
  contact_email: string;
  default_tax_type_id: number | null;
  credit_limit: number;
  total_outstanding: number;
  total_ordered: number;
  available_credit: number;
  over_credit_limit: boolean;
  check_terms: boolean;
  terms_code: string;
  terms_age_type: number;
  terms_days: number;
  terms_cutoff_date: string | null;
  overdue_amount: number;
  over_terms: boolean;
};

export type InventoryItem = {
  code: string;
  description: string;
  uom: string;
  is_uom_item: boolean;
  is_whse_item: boolean;
  default_tax_type_id: number | null;
  available: number;
  warehouse_codes: string[];
  pack_size: number;
};

export type CustomerPrice = {
  stock_code: string;
  excl_price: number;
  incl_price: number;
  source: string;    // 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'
};

export type Warehouse = { code: string; description: string };
export type TaxType = { id: number; code: string; description: string; rate: number };
export type Project = { code: string; description: string };
export type SettlementTerm = { code: string; description: string };

export type SalesOrderLineInput = {
  stock_code: string;
  description?: string | null;
  warehouse_code?: string | null;
  quantity: number;
  unit_price: number;
  tax_type_id?: number | null;
  discount_percent?: number;
  project_code?: string | null;
};

export type SalesOrderCreate = {
  customer_account: string;
  order_date?: string | null;
  due_date?: string | null;
  invoice_date?: string | null;
  delivery_date?: string | null;
  external_order?: string | null;
  default_project_code?: string | null;
  discount_percent?: number;
  tax_mode?: "Exclusive" | "Inclusive";
  description?: string | null;
  lines: SalesOrderLineInput[];
};

export type SalesOrderCreateResponse = {
  order_no: string;
  invoice_no: string | null;
};

export type SalesOrderSearchResult = {
  order_no: string;
  external_order: string;
  description: string;
  invoice_date: string | null;
  order_date: string | null;
  customer_account: string;
  customer_name: string;
  doc_state: number;
  doc_state_label: string;
  total_incl: number;
  is_archived: boolean;
};

export type SalesOrderSearchFilters = {
  account?: string;
  order_no?: string;
  ext_order?: string;
  description?: string;
  date_from?: string;
  date_to?: string;
};

export type SalesOrderLineDetail = {
  stock_code: string;
  item_description: string;
  description: string;
  warehouse_code: string;
  warehouse_codes: string[];
  is_whse_item: boolean;
  quantity: number;
  unit_price: number;        // always excl
  unit_price_incl: number;   // actual incl from Price List / Evolution
  tax_type_id: number | null;
  discount_percent: number;
  project_code: string;
};

export type SalesOrderDetail = {
  order_no: string;
  customer_account: string;
  customer_name: string;
  order_date: string | null;
  due_date: string | null;
  invoice_date: string | null;
  delivery_date: string | null;
  external_order: string;
  default_project_code: string;
  discount_percent: number;
  tax_mode: "Exclusive" | "Inclusive";
  description: string;
  doc_state: number;
  doc_state_label: string;
  is_archived: boolean;
  lines: SalesOrderLineDetail[];
};

// ---------- Customer invoice history (telesales dashboard) ----------

export type HistoryRoute = { id: string; display: string };
export type HistoryCustomer = { id: string; display: string };

export type HistoricalOrderLine = {
  stockCode: string;
  description: string;
  category: string;
  quantity: number;
  exclAmount: number;
  vat: number;
  inclAmount: number;
  onOrder: boolean;   // true if this line includes any pending-but-not-invoiced qty
};

export type HistoricalOrder = {
  orderNum: string;
  orderDate: string | null;
  route: string;
  customerName: string;
  items: HistoricalOrderLine[];
  itemCount: number;
  totalQuantity: number;
  totalExclAmount: number;
  totalVat: number;
  totalInclAmount: number;
};

export type OrderSuggestion = {
  stock_code: string;
  description: string;
  category: string;
  suggested_quantity: number;    // already-adjusted (base_median − pending), pack-snapped
  unit_price: number;            // always excl
  unit_price_incl: number;       // actual incl from Price List / Evolution
  non_zero_weeks: number;
  total_weeks: number;
  on_order_qty: number;              // pending (not-yet-invoiced) qty
  current_week_invoiced_qty: number; // invoiced this week — also subtracted

  item_description: string;
  uom: string;
  is_uom_item: boolean;
  is_whse_item: boolean;
  default_tax_type_id: number | null;
  available: number;
  warehouse_codes: string[];
  pack_size: number;
  price_source: string;   // 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'
};

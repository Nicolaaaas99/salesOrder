import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  Warehouse,
  TaxType,
  SalesOrderCreate,
  SalesOrderDetail,
  SalesOrderSearchResult,
} from "../api/types";
import { CustomerPicker } from "./CustomerPicker";
import { AddressPanel } from "./AddressPanel";
import { OrderInfoPanel } from "./OrderInfoPanel";
import { DocumentInfoPanel } from "./DocumentInfoPanel";
import { LineItemsGrid, makeEmptyLine, orderTotals, type LineRow, type TaxMode } from "./LineItemsGrid";
import { TotalsPanel } from "./TotalsPanel";
import { NumberCell } from "./NumberCell";
import { SalesOrderSearchModal } from "./SalesOrderSearchModal";
import { CreditStatus } from "./CreditStatus";
import { useAuth } from "../context/AuthContext";
import { nextBusinessDay, today } from "../utils/date";
import { fmtCurrency, extractErrorMsg } from "../utils/format";
import { useCustomerAutoRefresh } from "../utils/useCustomerAutoRefresh";

type EditingState = {
  order_no: string;
  mode: "edit" | "template";
};

type LoadedOrder = {
  detail: SalesOrderDetail;
  customer: CustomerDetail;
};

type Props = {
  initialCustomer?: CustomerDetail | null;
  initialLines?: LineRow[] | null;
  initialLoadedOrder?: LoadedOrder | null;
  /** If set, lines are controlled by the parent — every edit fires onControlledLinesChange. */
  controlledLines?: LineRow[];
  onControlledLinesChange?: (lines: LineRow[]) => void;
  lockedCustomer?: boolean;
  hideOpenButton?: boolean;
  openSearchOnMount?: boolean;
  onClose?: () => void;
  onBack?: () => void;
  onPlaced?: () => void;
};


const initialLinesFor = (defaultTaxTypeId: number | null = null): LineRow[] =>
  [makeEmptyLine(defaultTaxTypeId)];

function detailToLines(detail: SalesOrderDetail, cust: CustomerDetail): LineRow[] {
  if (detail.lines.length === 0) {
    return initialLinesFor(cust.default_tax_type_id ?? null);
  }
  return detail.lines.map((l) => ({
    stock_code: l.stock_code,
    item_description: l.item_description,
    description: l.description || l.item_description,
    warehouse_code: l.warehouse_code,
    warehouse_codes: l.warehouse_codes ?? [],
    available: 0,
    quantity: l.quantity,
    uom: "",
    is_uom_item: false,
    is_whse_item: l.is_whse_item || !!l.warehouse_code,
    // Both prices come straight from Evolution via the DB (fUnitPriceExcl /
    // fUnitPriceIncl), so no re-derivation is needed on the frontend.
    unit_price: l.unit_price,
    unit_price_incl: l.unit_price_incl,
    // Loaded orders (edit or template) keep their saved price locked so the
    // rep can't override it in the grid.
    price_source: "Loaded Order",
    price_locked: true,
    tax_type_id: l.tax_type_id,
    discount_percent: l.discount_percent || 0,
    project_code: l.project_code || "",
    // Resolved after load (see effect in SalesOrderScreen); if the loaded qty
    // turns out to be non-pack-multiple once pack_size resolves, the effect
    // flips pack_override on so the chip + unlocked snapping behaviour both
    // match the saved intent.
    pack_size: 1,
    pack_override: false,
    // Loaded prices came from Evolution directly (not re-resolved via the
    // view) so we don't know the warehouse they were priced in. Treat as
    // non-fallback; the UI only flags fresh lookups.
    is_fallback_warehouse: false,
  }));
}

export function SalesOrderScreen({
  initialCustomer = null,
  initialLines = null,
  initialLoadedOrder = null,
  controlledLines,
  onControlledLinesChange,
  lockedCustomer = false,
  hideOpenButton = false,
  openSearchOnMount = false,
  onClose,
  onBack,
  onPlaced,
}: Props = {}) {
  const isControlled =
    controlledLines !== undefined && onControlledLinesChange !== undefined;

  const seedCustomer = initialLoadedOrder?.customer ?? initialCustomer;
  const seed = initialLoadedOrder?.detail ?? null;

  // Session date was picked at login and applies to the Invoice/Delivery date
  // of every new sales order placed in this session (typically the next day).
  // Order Date and Due Date default to today — those are "when was this placed"
  // fields, not "when does it ship". Falls back to next-business-day for safety
  // if SalesOrderScreen is somehow mounted outside the AuthProvider gate.
  const { sessionDate, forceExtOrderNum } = useAuth();
  const sessionDateDefault = sessionDate ?? nextBusinessDay();
  const todayDefault = today();

  const [customer, setCustomer] = useState<CustomerDetail | null>(seedCustomer);

  // Live-refresh credit limit / terms / overdue amounts from Evolution so a
  // rep who gets blocked (OVER LIMIT / OVER TERMS) and then has accounts
  // unblock the customer in Evolution sees the change without reselecting
  // (which would wipe the draft). Fires on focus, every 5s, and on demand
  // right before saving (see onPlaceOrder).
  const { refreshNow: refreshCustomer } = useCustomerAutoRefresh(
    customer?.account ?? null,
    setCustomer,
  );

  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [taxTypes, setTaxTypes] = useState<TaxType[]>([]);

  // Order/Due default to today; Invoice/Delivery default to the session date.
  // Dates reset on every mount — the rep can still override on the preview if
  // they want the exact saved values of a loaded order.
  const [orderDate, setOrderDate] = useState(todayDefault);
  const [dueDate, setDueDate] = useState(todayDefault);
  const [invoiceDate, setInvoiceDate] = useState(sessionDateDefault);
  const [deliveryDate, setDeliveryDate] = useState(sessionDateDefault);
  const [externalOrder, setExternalOrder] = useState(seed?.external_order ?? "");
  const [documentDiscount, setDocumentDiscount] = useState(seed?.discount_percent ?? 0);
  const [description, setDescription] = useState(seed?.description || "Sales Order");
  const [taxMode, setTaxMode] = useState<TaxMode>(seed?.tax_mode ?? "Exclusive");

  const defaultTaxTypeId = customer?.default_tax_type_id ?? null;

  const [internalLines, setInternalLines] = useState<LineRow[]>(() => {
    if (initialLoadedOrder) {
      return detailToLines(initialLoadedOrder.detail, initialLoadedOrder.customer);
    }
    if (initialLines && initialLines.length > 0) return initialLines;
    return initialLinesFor(seedCustomer?.default_tax_type_id ?? null);
  });
  const lines = isControlled ? (controlledLines as LineRow[]) : internalLines;
  // Mirror `lines` into a ref so controlled-mode functional updaters always
  // see the latest value, even when fired from stale async-callback closures
  // (e.g. an item picker's price fetch resolving after the next render —
  // without this, the resolve would reset `lines` to pre-pick state and the
  // just-picked line would vanish).
  const linesRef = useRef<LineRow[]>(lines);
  useEffect(() => {
    linesRef.current = lines;
  }, [lines]);

  const setLines = (updater: LineRow[] | ((prev: LineRow[]) => LineRow[])) => {
    if (isControlled) {
      const next =
        typeof updater === "function"
          ? (updater as (p: LineRow[]) => LineRow[])(linesRef.current)
          : updater;
      // Keep the ref in sync with what the parent will have after the next
      // render — rapid-fire updaters in the same tick must see each other.
      linesRef.current = next;
      onControlledLinesChange!(next);
    } else {
      // React handles functional-form staleness for us.
      setInternalLines(updater as any);
    }
  };
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<{ title: string; body: string } | null>(null);

  const [editing, setEditing] = useState<EditingState | null>(
    seed
      ? { order_no: seed.order_no, mode: seed.is_archived ? "template" : "edit" }
      : null
  );
  const [searchOpen, setSearchOpen] = useState(openSearchOnMount);
  const [loadingOrder, setLoadingOrder] = useState(false);

  useEffect(() => {
    (async () => {
      const [w, tt] = await Promise.all([
        api.listWarehouses().catch(() => []),
        api.listTaxTypes().catch(() => []),
      ]);
      setWarehouses(w);
      setTaxTypes(tt);
    })();
  }, []);

  // Resolve pack sizes for any line whose pack_size hasn't been populated yet
  // (e.g. a line loaded from an existing SalesOrderDetail). Does NOT snap the
  // existing qty — only arms the input so future edits respect the constraint.
  useEffect(() => {
    const needed = Array.from(
      new Set(
        lines
          .filter((l) => l.stock_code && (!l.pack_size || l.pack_size === 1))
          .map((l) => l.stock_code),
      ),
    );
    if (needed.length === 0) return;
    let cancelled = false;
    api
      .getPackSizes(needed)
      .then((map) => {
        if (cancelled) return;
        setLines((prev) =>
          prev.map((l) => {
            const pack = map[l.stock_code];
            if (!pack || pack <= 1) return l;
            if (l.pack_size && l.pack_size !== 1) return l;
            const offPack = l.quantity > 0 && l.quantity % pack !== 0;
            return { ...l, pack_size: pack, pack_override: l.pack_override || offPack };
          }),
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines.map((l) => l.stock_code).join("|")]);

  async function onSelectCustomer(account: string) {
    try {
      const detail = await api.getCustomer(account);
      setCustomer(detail);

      const newDefault = detail.default_tax_type_id ?? null;
      setLines((prev) =>
        prev.map((l) => (l.tax_type_id == null ? { ...l, tax_type_id: newDefault } : l))
      );
    } catch (e: any) {
      setErrorMsg(e?.message ?? "Failed to load customer");
    }
  }

  function resetForm() {
    setCustomer(null);
    setOrderDate(todayDefault);
    setDueDate(todayDefault);
    setInvoiceDate(sessionDateDefault);
    setDeliveryDate(sessionDateDefault);
    setExternalOrder("");
    setDocumentDiscount(0);
    setDescription("Sales Order");
    setTaxMode("Exclusive");
    setLines(initialLinesFor());
    setErrorMsg(null);
    setEditing(null);
  }

  async function loadOrder(result: SalesOrderSearchResult) {
    setSearchOpen(false);
    setLoadingOrder(true);
    setErrorMsg(null);
    try {
      const [detail, cust] = await Promise.all([
        api.getSalesOrder(result.order_no),
        api.getCustomer(result.customer_account),
      ]);
      applyLoadedOrder(detail, cust);
    } catch (e: any) {
      setErrorMsg(e?.response?.data?.detail ?? e?.message ?? "Failed to load sales order");
    } finally {
      setLoadingOrder(false);
    }
  }

  function applyLoadedOrder(detail: SalesOrderDetail, cust: CustomerDetail) {
    const mode: "edit" | "template" = detail.is_archived ? "template" : "edit";
    setCustomer(cust);
    // Load an existing order → dates reset per the normal defaults (not the
    // saved values). Rep can override per-order on the preview if needed.
    setOrderDate(todayDefault);
    setDueDate(todayDefault);
    setInvoiceDate(sessionDateDefault);
    setDeliveryDate(sessionDateDefault);
    setExternalOrder(detail.external_order || "");
    setDocumentDiscount(detail.discount_percent || 0);
    setDescription(detail.description || "Sales Order");
    setTaxMode(detail.tax_mode);
    setLines(detailToLines(detail, cust));
    setEditing({ order_no: detail.order_no, mode });
  }

  async function onPlaceOrder() {
    setErrorMsg(null);

    if (!customer) { setErrorMsg("Select a customer first."); return; }

    // Just-in-time refresh — the 5s poll and focus listener cover most cases,
    // but we guarantee the credit/terms check runs against live Evolution
    // state at the moment of save. Falls back to the cached detail if the
    // fetch fails (e.g. transient network blip) so a flaky network doesn't
    // block the rep from saving.
    const fresh = await refreshCustomer();
    const current = fresh ?? customer;

    const breakdown =
      `Outstanding invoices: ${fmtCurrency(current.total_outstanding)}. ` +
      `Existing sales orders: ${fmtCurrency(current.total_ordered)}. ` +
      `Credit limit: ${fmtCurrency(current.credit_limit)}.`;

    const isUpdate = editing?.mode === "edit" && !!editing?.order_no;

    // Only hard-block when the customer is flagged CheckTerms = 1. CheckTerms = 0
    // customers can place orders even if over credit/terms — the OVER LIMIT and
    // OVER TERMS badges in the UI already warn the rep. `current` is the just-
    // fetched copy so a last-second unblock in Evolution is honoured.
    if (current.check_terms) {
      if (!isUpdate && current.over_terms) {
        const termDesc = current.terms_days > 0 ? `${current.terms_days} days` : "current period";
        setErrorMsg(
          `${current.name} is over their payment terms (${termDesc}) with ` +
          `${fmtCurrency(current.overdue_amount)} in overdue invoices. ` +
          `Sales order cannot be created.`
        );
        return;
      }

      if (current.over_credit_limit) {
        setErrorMsg(
          `${current.name} is already over their credit limit by ` +
          `${fmtCurrency(-current.available_credit)}. ${breakdown} ` +
          `Sales order cannot be created.`
        );
        return;
      }
    }

    if (forceExtOrderNum && !externalOrder.trim()) {
      setErrorMsg(
        "External Order Number is required (set by Evolution's sales-order config)."
      );
      return;
    }

    const filledLines = lines.filter((l) => l.stock_code);
    if (filledLines.length === 0) {
      setErrorMsg("Add at least one line with an item.");
      return;
    }
    // Zero-price lines are allowed (promos, samples, free lines). Quantity
    // still has to be positive — a zero-qty line is a no-op.
    const badLine = filledLines.find((l) => l.quantity <= 0);
    if (badLine) {
      setErrorMsg(`Line "${badLine.stock_code}" must have a quantity greater than 0.`);
      return;
    }

    if (current.check_terms && current.credit_limit > 0) {
      const { incl: orderIncl } = orderTotals(filledLines, taxTypes, taxMode, documentDiscount);
      if (orderIncl > current.available_credit) {
        setErrorMsg(
          `This order (${fmtCurrency(orderIncl)}) would push ${current.name} ` +
          `over their credit limit. Available credit: ${fmtCurrency(current.available_credit)}. ${breakdown}`
        );
        return;
      }
    }

    const payload: SalesOrderCreate = {
      customer_account: current.account,
      order_date: orderDate,
      due_date: dueDate,
      invoice_date: invoiceDate,
      delivery_date: deliveryDate,
      external_order: externalOrder || null,
      default_project_code: null,
      discount_percent: documentDiscount,
      // Always save Exclusive — `unit_price` on every line is excl by invariant.
      // The UI `taxMode` is a pure display toggle; it must NOT leak to the save.
      tax_mode: "Exclusive",
      description: description || null,
      lines: filledLines.map((l) => ({
        stock_code: l.stock_code,
        description: l.description || null,
        warehouse_code: l.warehouse_code || null,
        quantity: l.quantity,
        unit_price: l.unit_price,
        tax_type_id: l.tax_type_id,
        discount_percent: l.discount_percent || 0,
        project_code: null,
      })),
    };

    setSubmitting(true);
    try {
      const res = isUpdate
        ? await api.updateOrder(editing!.order_no, payload)
        : await api.placeOrder(payload);
      // Global signal so any list / badge the order touches in other screens
      // (Order Assistant, Route Overview) refreshes immediately.
      window.dispatchEvent(new CustomEvent("orders:mutated"));
      setSuccessMsg({
        title: isUpdate ? "Sales Order Updated" : "Sales Order Created",
        body: isUpdate
          ? `Order ${res.order_no} has been updated in Evolution.`
          : `Order ${res.order_no} has been created in Evolution.`,
      });
    } catch (e: any) {
      setErrorMsg(extractErrorMsg(e, "Failed to save sales order"));
    } finally {
      setSubmitting(false);
    }
  }

  function dismissModal() {
    setSuccessMsg(null);
    if (onPlaced) {
      onPlaced();
    } else {
      resetForm();
    }
  }

  const currentOrderIncl = orderTotals(
    lines.filter((l) => l.stock_code),
    taxTypes, taxMode, documentDiscount,
  ).incl;

  const headerTitle = editing
    ? editing.mode === "edit"
      ? `Edit Sales Order ${editing.order_no}`
      : `New Sales Order (from template ${editing.order_no})`
    : "New Sales Order";

  const saveLabel = editing?.mode === "edit"
    ? (submitting ? "Updating…" : "Update Order")
    : (submitting ? "Placing order…" : "Place Order");

  return (
    <div className="screen">
      <div className="screen-header">
        <div className="screen-header-title">
          {onBack && (
            <button
              className="btn btn-ghost"
              onClick={onBack}
              title="Back to Sales Orders"
            >
              <BackIcon />
              Back
            </button>
          )}
          <h1>{headerTitle}</h1>
        </div>
        <div className="screen-header-actions">
          {editing && !lockedCustomer && (
            <button className="btn btn-secondary" onClick={resetForm} title="Discard current order">
              Cancel
            </button>
          )}
          {!hideOpenButton && !onBack && (
            <button
              className="btn btn-secondary"
              onClick={() => setSearchOpen(true)}
              disabled={loadingOrder}
            >
              <OpenIcon />
              {loadingOrder ? "Loading…" : "Open"}
            </button>
          )}
          {onClose && (
            <button
              className="btn btn-secondary"
              onClick={onClose}
              title="Close"
              aria-label="Close"
            >
              <CloseIcon />
              Close
            </button>
          )}
        </div>
      </div>

      {editing?.mode === "template" && (
        <div className="banner banner-info">
          Loaded archived order <strong>{editing.order_no}</strong> as a template.
          Saving will create a new sales order.
        </div>
      )}

      <div className="two-col">
        <div className="two-col-stack">
          <section className="panel">
            <header className="panel-header">Customer</header>
            <div className="customer-row">
              <label>Customer</label>
              {lockedCustomer && customer ? (
                <div className="customer-locked" title="Customer is set by the draft">
                  <strong>{customer.account}</strong> — {customer.name}
                </div>
              ) : (
                <CustomerPicker
                  value={customer ? `${customer.account} — ${customer.name}` : ""}
                  onSelect={onSelectCustomer}
                />
              )}
            </div>
            {customer && <CreditStatus customer={customer} currentOrderIncl={currentOrderIncl} />}
          </section>
          <AddressPanel customer={customer} />
        </div>
        <div className="two-col-stack">
          <OrderInfoPanel
            externalOrder={externalOrder}
            orderDate={orderDate}
            dueDate={dueDate}
            externalOrderRequired={forceExtOrderNum}
            onChange={(p) => {
              if (p.externalOrder !== undefined) setExternalOrder(p.externalOrder);
              if (p.orderDate !== undefined) setOrderDate(p.orderDate);
              if (p.dueDate !== undefined) setDueDate(p.dueDate);
            }}
          />
          <DocumentInfoPanel
            invoiceDate={invoiceDate}
            deliveryDate={deliveryDate}
            onChange={(p) => {
              if (p.invoiceDate !== undefined) setInvoiceDate(p.invoiceDate);
              if (p.deliveryDate !== undefined) setDeliveryDate(p.deliveryDate);
            }}
          />
        </div>
      </div>

      <LineItemsGrid
        lines={lines}
        warehouses={warehouses}
        taxTypes={taxTypes}
        defaultTaxTypeId={defaultTaxTypeId}
        taxMode={taxMode}
        customerAccount={customer?.account ?? null}
        onChange={setLines}
      />

      <section className="panel bottom-bar">
        <div className="bottom-fields">
          <label>Description</label>
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />

          <label>Discount %</label>
          <NumberCell
            className="input"
            value={documentDiscount}
            onChange={setDocumentDiscount}
          />

          {/* Tax Mode toggle is DISPLAY-ONLY — it changes how the Price column
              and Line-total column are shown, but doesn't touch stored values.
              Line totals/tax/order totals all come from the invariant
              `unit_price` (always excl) so the Totals Panel is stable. */}
          <label>Tax Mode</label>
          <div className="segmented">
            <button
              type="button"
              className={taxMode === "Exclusive" ? "active" : ""}
              onClick={() => setTaxMode("Exclusive")}
            >
              Exclusive
            </button>
            <button
              type="button"
              className={taxMode === "Inclusive" ? "active" : ""}
              onClick={() => setTaxMode("Inclusive")}
            >
              Inclusive
            </button>
          </div>
        </div>
        <TotalsPanel
          lines={lines}
          taxTypes={taxTypes}
          taxMode={taxMode}
          documentDiscount={documentDiscount}
        />
      </section>

      <div className="action-bar">
        <button
          className="btn btn-primary btn-large"
          onClick={onPlaceOrder}
          disabled={submitting}
        >
          <SaveIcon />
          {saveLabel}
        </button>
      </div>

      {searchOpen && (
        <SalesOrderSearchModal
          onClose={() => setSearchOpen(false)}
          onSelect={loadOrder}
        />
      )}

      {successMsg && (
        <div className="modal-backdrop" onClick={dismissModal}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon">
              <svg
                width="28" height="28" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
              >
                <path d="M20 6L9 17l-5-5" />
              </svg>
            </div>
            <h2 className="modal-title">{successMsg.title}</h2>
            <p className="modal-body">{successMsg.body}</p>
            <button className="btn btn-primary" onClick={dismissModal}>OK</button>
          </div>
        </div>
      )}

      {errorMsg && (
        <div className="modal-backdrop" onClick={() => setErrorMsg(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-icon modal-icon--err">
              <svg
                width="28" height="28" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <h2 className="modal-title">Couldn't Place Order</h2>
            <p className="modal-body">{errorMsg}</p>
            <button className="btn btn-primary" onClick={() => setErrorMsg(null)}>OK</button>
          </div>
        </div>
      )}
    </div>
  );
}


function SaveIcon() {
  return (
    <svg
      width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    >
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
      <polyline points="17 21 17 13 7 13 7 21" />
      <polyline points="7 3 7 8 15 8" />
    </svg>
  );
}

function OpenIcon() {
  return (
    <svg
      width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    >
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    >
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg
      width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    >
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

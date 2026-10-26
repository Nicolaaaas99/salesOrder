import { useEffect, useState } from "react";
import { Pencil, X, Ban, CalendarDays } from "lucide-react";
import { api } from "../api/client";
import type { SalesOrderDetail } from "../api/types";
import { fmtCurrency } from "../utils/format";
import { DateInput } from "./DateInput";
import { today, nextBusinessDay } from "../utils/date";

type Props = {
  orderNo: string;
  onClose: () => void;
  /** Optional. If provided AND the order is Unprocessed (DocState=1) with a
   *  delivery date today or later, the modal shows an "Edit this order"
   *  button that calls back with the order number. Past-delivery orders can
   *  only be rescheduled (see onDeliveryDateChanged). */
  onEdit?: (orderNo: string) => void;
  /** Optional. If provided AND the order is Unprocessed (DocState=1), the
   *  modal shows a destructive "Cancel order" button. Fires after the
   *  backend has flipped DocState to 7 — parent should refresh its lists
   *  and close the modal. */
  onCancel?: (orderNo: string) => void;
  /** Optional. Fires after the delivery date was successfully updated so
   *  the parent can refresh its lists (OrdersModal / Route Overview). */
  onDeliveryDateChanged?: (orderNo: string) => void;
};

function fmtDmy(iso: string | null): string {
  if (!iso) return "—";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

function stateColorClass(docState: number): string {
  if (docState === 1) return "bg-blue-100 text-blue-800 border border-blue-300";
  if (docState === 3) return "bg-amber-100 text-amber-800 border border-amber-300";
  if (docState === 4) return "bg-emerald-100 text-emerald-800 border border-emerald-300";
  return "bg-gray-100 text-gray-800 border border-gray-300";
}

export function OrderSummaryModal({
  orderNo,
  onClose,
  onEdit,
  onCancel,
  onDeliveryDateChanged,
}: Props) {
  const [detail, setDetail] = useState<SalesOrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Footer is a mini state machine so inline confirmations stay in-context
  // (no browser confirm, no second overlay).
  type Mode = "view" | "confirmCancel" | "editDate";
  const [mode, setMode] = useState<Mode>("view");
  const [cancelling, setCancelling] = useState(false);
  const [savingDate, setSavingDate] = useState(false);
  const [draftDate, setDraftDate] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const d = await api.getSalesOrder(orderNo);
        if (alive) setDetail(d);
      } catch (e: any) {
        if (alive) setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load order");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [orderNo]);

  // Use Evolution's stored per-line totals so the sums match the source of
  // truth (and the collapsed "Orders on date" panel) exactly — multiplying
  // rounded unit prices on the frontend introduces 1-2c drift.
  const totals = detail
    ? detail.lines.reduce(
        (acc, l) => {
          acc.excl += l.line_total_excl || 0;
          acc.tax  += l.line_total_tax || 0;
          acc.incl += l.line_total_incl || 0;
          return acc;
        },
        { excl: 0, tax: 0, incl: 0 }
      )
    : { excl: 0, tax: 0, incl: 0 };
  const vat = totals.tax;

  const isUnprocessed = detail?.doc_state === 1;
  const todayIso = today();
  // Full edit (opening the Sales Order Entry sheet) is only allowed when the
  // delivery date is strictly after today. Orders delivering today or already
  // in the past can still be rescheduled or cancelled — but not fully edited.
  const deliveryIsFuture = !!detail?.delivery_date && detail.delivery_date > todayIso;
  const canFullyEdit = !!onEdit && isUnprocessed && deliveryIsFuture;
  const canReschedule = isUnprocessed && !deliveryIsFuture;
  const canCancel = !!onCancel && isUnprocessed;

  async function doCancel() {
    if (!detail || !onCancel) return;
    setCancelling(true);
    setError(null);
    try {
      await api.cancelSalesOrder(detail.order_no);
      // Global signal so Order Assistant + Route Overview refresh everywhere
      // the cancelled order was shown.
      window.dispatchEvent(new CustomEvent("orders:mutated"));
      onCancel(detail.order_no);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to cancel order");
      setCancelling(false);
      setMode("view");
    }
  }

  async function doSaveDate() {
    if (!detail || !draftDate) return;
    setSavingDate(true);
    setError(null);
    try {
      await api.updateSalesOrderDeliveryDate(detail.order_no, draftDate);
      window.dispatchEvent(new CustomEvent("orders:mutated"));
      onDeliveryDateChanged?.(detail.order_no);
      onClose();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Failed to update delivery date");
      setSavingDate(false);
    }
  }

  function openEditDate() {
    // Default to the next business day — same default the rest of the app
    // uses when prompting for a date.
    setDraftDate(detail?.delivery_date || nextBusinessDay());
    setMode("editDate");
    setError(null);
  }

  return (
    <div className="fixed inset-0 z-50 bg-black bg-opacity-30 flex items-center justify-center p-4">
      <div className="bg-white rounded shadow-lg max-w-4xl w-full max-h-[85vh] flex flex-col overflow-hidden">
        <div className="px-4 py-3 border-b bg-gray-50 flex items-start justify-between gap-2">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wide">Order</div>
            <div className="text-sm font-semibold text-gray-900">
              {detail?.order_no ?? orderNo}
              {detail && (
                <span className="text-gray-500 font-normal">
                  {" · "}{detail.customer_account} · {detail.customer_name}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-xl leading-none px-2"
            title="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">
              Loading…
            </div>
          )}
          {error && !loading && (
            <div className="p-4 text-sm text-red-700 bg-red-50">{error}</div>
          )}
          {detail && !loading && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-4 py-3 text-xs border-b border-gray-200">
                <HeaderCell label="Status">
                  <span
                    className={`inline-block text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full ${stateColorClass(
                      detail.doc_state
                    )}`}
                  >
                    {detail.doc_state_label}
                  </span>
                </HeaderCell>
                <HeaderCell label="Order Date" value={fmtDmy(detail.order_date)} />
                <HeaderCell label="Due Date" value={fmtDmy(detail.due_date)} />
                <HeaderCell label="Invoice Date" value={fmtDmy(detail.invoice_date)} />
                <HeaderCell label="Delivery Date" value={fmtDmy(detail.delivery_date)} />
                <HeaderCell label="External Order" value={detail.external_order || "—"} />
                <HeaderCell label="Description" value={detail.description || "—"} />
                <HeaderCell label="Tax Mode" value={detail.tax_mode} />
              </div>

              {(() => {
                // Grow the Qty column based on the largest quantity on the
                // order so big numbers aren't truncated. Mirrors the
                // DraftOrderPanel / LineItemsGrid growth heuristic.
                const maxQtyDigits = Math.max(
                  2,
                  ...detail.lines.map((l) => String(Math.floor(l.quantity || 0)).length),
                );
                const qtyColumnPx = Math.max(60, maxQtyDigits * 11 + 30);
                return (
              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-3 py-1.5 font-semibold">Stock</th>
                    <th className="text-left px-3 py-1.5 font-semibold">Description</th>
                    <th
                      className="text-right px-3 py-1.5 font-semibold whitespace-nowrap"
                      style={{ width: qtyColumnPx }}
                    >
                      Qty
                    </th>
                    <th className="text-right px-3 py-1.5 font-semibold">Price (excl)</th>
                    <th className="text-right px-3 py-1.5 font-semibold">Price (incl)</th>
                    <th className="text-right px-3 py-1.5 font-semibold">Line Total (incl)</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.lines.map((l, i) => {
                    const offPack =
                      l.pack_size > 1 && l.quantity > 0 && l.quantity % l.pack_size !== 0;
                    return (
                      <tr key={i} className="border-b border-gray-100">
                        <td className="px-3 py-1.5 font-mono text-gray-800">{l.stock_code}</td>
                        <td className="px-3 py-1.5 text-gray-700 truncate max-w-[22rem]">
                          {l.item_description || l.description}
                        </td>
                        <td
                          className="px-3 py-1.5 text-right font-mono text-gray-900 whitespace-nowrap"
                          style={{ width: qtyColumnPx }}
                        >
                          {l.quantity.toLocaleString()}
                          {offPack && (
                            <div
                              className="text-[9px] text-amber-700 font-semibold leading-tight"
                              title={`Pack size is ${l.pack_size} — this line was ordered off-pack`}
                            >
                              off-pack · ×{l.pack_size}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-gray-900">
                          {fmtCurrency(l.unit_price)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-gray-900">
                          {fmtCurrency(l.unit_price_incl)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono text-gray-900">
                          {fmtCurrency(l.line_total_incl)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot className="bg-gray-50 border-t border-gray-300">
                  <tr>
                    <td colSpan={5} className="px-3 py-1.5 text-right text-gray-600">Excl</td>
                    <td className="px-3 py-1.5 text-right font-mono text-gray-900">{fmtCurrency(totals.excl)}</td>
                  </tr>
                  <tr>
                    <td colSpan={5} className="px-3 py-1.5 text-right text-gray-600">VAT</td>
                    <td className="px-3 py-1.5 text-right font-mono text-gray-900">{fmtCurrency(vat)}</td>
                  </tr>
                  <tr className="border-t border-gray-300">
                    <td colSpan={5} className="px-3 py-1.5 text-right font-semibold">Incl</td>
                    <td className="px-3 py-1.5 text-right font-mono font-semibold text-gray-900">{fmtCurrency(totals.incl)}</td>
                  </tr>
                </tfoot>
              </table>
                );
              })()}
            </>
          )}
        </div>

        <div className="border-t bg-gray-50">
          {mode === "confirmCancel" && detail && (
            <div className="px-4 py-3 bg-red-50 border-b border-red-200 flex items-center gap-3 text-xs">
              <Ban className="w-4 h-4 text-red-600 flex-shrink-0" />
              <div className="flex-1 text-red-900">
                <div className="font-semibold">Cancel order {detail.order_no}?</div>
                <div className="text-red-800">
                  This sets its status to Cancelled in Evolution and reduces stock-on-order.
                  It cannot be undone from here.
                </div>
              </div>
              <button
                onClick={() => setMode("view")}
                disabled={cancelling}
                className="text-xs border border-gray-300 bg-white px-3 py-1.5 rounded hover:bg-gray-100 disabled:text-gray-400 flex-shrink-0"
              >
                Keep order
              </button>
              <button
                onClick={doCancel}
                disabled={cancelling}
                className="inline-flex items-center gap-1 text-xs bg-red-600 text-white px-3 py-1.5 rounded hover:bg-red-700 disabled:bg-red-300 disabled:cursor-not-allowed flex-shrink-0"
              >
                <Ban className="w-3 h-3" />
                {cancelling ? "Cancelling…" : "Confirm cancel"}
              </button>
            </div>
          )}

          {mode === "editDate" && detail && (
            <div className="px-4 py-3 bg-blue-50 border-b border-blue-200 flex items-center gap-3 text-xs">
              <CalendarDays className="w-4 h-4 text-blue-600 flex-shrink-0" />
              <div className="text-blue-900 font-semibold">New delivery date</div>
              <div className="flex-1">
                <DateInput
                  value={draftDate}
                  onChange={setDraftDate}
                  className="input w-36"
                />
              </div>
              <button
                onClick={() => setMode("view")}
                disabled={savingDate}
                className="text-xs border border-gray-300 bg-white px-3 py-1.5 rounded hover:bg-gray-100 disabled:text-gray-400 flex-shrink-0"
              >
                Keep original
              </button>
              <button
                onClick={doSaveDate}
                disabled={savingDate || !draftDate}
                className="inline-flex items-center gap-1 text-xs bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700 disabled:bg-blue-300 disabled:cursor-not-allowed flex-shrink-0"
              >
                <CalendarDays className="w-3 h-3" />
                {savingDate ? "Saving…" : "Save date"}
              </button>
            </div>
          )}

          <div className="px-4 py-3 flex items-center justify-end gap-2">
            {mode === "view" && canCancel && detail && (
              <button
                onClick={() => {
                  setError(null);
                  setMode("confirmCancel");
                }}
                className="inline-flex items-center gap-1 text-xs bg-red-600 text-white px-3 py-1.5 rounded hover:bg-red-700 mr-auto"
                title="Set this order to Cancelled in Evolution"
              >
                <Ban className="w-3 h-3" />
                Cancel order
              </button>
            )}
            {mode === "view" && canReschedule && detail && (
              <button
                onClick={openEditDate}
                className="inline-flex items-center gap-1 text-xs bg-amber-600 text-white px-3 py-1.5 rounded hover:bg-amber-700"
                title="Delivery date is in the past — only rescheduling is allowed"
              >
                <CalendarDays className="w-3 h-3" />
                Change delivery date
              </button>
            )}
            {mode === "view" && canFullyEdit && detail && (
              <button
                onClick={() => onEdit!(detail.order_no)}
                className="inline-flex items-center gap-1 text-xs bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700"
              >
                <Pencil className="w-3 h-3" />
                Edit this order
              </button>
            )}
            <button
              onClick={onClose}
              disabled={cancelling || savingDate}
              className="text-xs border border-gray-300 px-3 py-1.5 rounded hover:bg-gray-100 disabled:text-gray-400"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function HeaderCell({ label, value, children }: { label: string; value?: string; children?: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      {children ?? <div className="text-gray-900 truncate" title={value}>{value}</div>}
    </div>
  );
}

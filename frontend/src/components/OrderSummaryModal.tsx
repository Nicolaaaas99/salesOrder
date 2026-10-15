import { useEffect, useState } from "react";
import { Pencil, X } from "lucide-react";
import { api } from "../api/client";
import type { SalesOrderDetail } from "../api/types";
import { fmtCurrency } from "../utils/format";

type Props = {
  orderNo: string;
  onClose: () => void;
  /** Optional. If provided AND the order is Unprocessed (DocState=1), the
   *  modal shows an "Edit this order" button that calls back with the order
   *  number. Omit entirely to keep the modal view-only in every context. */
  onEdit?: (orderNo: string) => void;
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

export function OrderSummaryModal({ orderNo, onClose, onEdit }: Props) {
  const [detail, setDetail] = useState<SalesOrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  const canEdit = !!onEdit && detail?.doc_state === 1;

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

              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-3 py-1.5 font-semibold">Stock</th>
                    <th className="text-left px-3 py-1.5 font-semibold">Description</th>
                    <th className="text-right px-3 py-1.5 font-semibold">Qty</th>
                    <th className="text-right px-3 py-1.5 font-semibold">Price (excl)</th>
                    <th className="text-right px-3 py-1.5 font-semibold">Price (incl)</th>
                    <th className="text-right px-3 py-1.5 font-semibold">Line Total (incl)</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.lines.map((l, i) => (
                    <tr key={i} className="border-b border-gray-100">
                      <td className="px-3 py-1.5 font-mono text-gray-800">{l.stock_code}</td>
                      <td className="px-3 py-1.5 text-gray-700 truncate max-w-[22rem]">
                        {l.item_description || l.description}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-gray-900">
                        {l.quantity.toLocaleString()}
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
                  ))}
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
            </>
          )}
        </div>

        <div className="px-4 py-3 border-t bg-gray-50 flex items-center justify-end gap-2">
          {canEdit && detail && (
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
            className="text-xs border border-gray-300 px-3 py-1.5 rounded hover:bg-gray-100"
          >
            Close
          </button>
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

import { ExternalLink, X } from "lucide-react";
import type { SalesOrderSearchResult } from "../api/types";
import { today } from "../utils/date";
import { classifyDeliveryAge, TIER_CLASSES } from "../utils/deliveryAge";

type Props = {
  customer: { id: string; display: string };
  date: string;
  orders: SalesOrderSearchResult[];
  isLoading: boolean;
  onClose: () => void;
  /** Clicked when the user wants to view a specific order. Context-dependent:
   *  Route Overview opens the summary modal with an Edit button for
   *  Unprocessed orders; Order Assistant opens it view-only. */
  onViewOrder?: (orderNo: string) => void;
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}/${m}/${y.slice(2)}` : iso;
}

export function OrdersModal({ customer, date, orders, isLoading, onClose, onViewOrder }: Props) {
  const total = orders.reduce((s, o) => s + (o.total_incl || 0), 0);
  const todayIso = today();
  // Oldest/overdue first so the rep sees urgent rows at the top. Nulls last.
  const sorted = [...orders].sort((a, b) => {
    const ad = a.delivery_date ?? "";
    const bd = b.delivery_date ?? "";
    if (!ad && !bd) return 0;
    if (!ad) return 1;
    if (!bd) return -1;
    return ad.localeCompare(bd);
  });

  return (
    <div className="fixed inset-0 z-50 bg-black bg-opacity-30 flex items-center justify-center p-4">
      <div className="bg-white rounded shadow-lg max-w-4xl w-full max-h-[80vh] flex flex-col overflow-hidden">
        <div className="px-4 py-3 border-b bg-gray-50 flex items-start justify-between gap-2">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wide">Orders on {date}</div>
            <div className="text-sm font-semibold text-gray-900">
              {customer.id} · {customer.display}
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
          {isLoading ? (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">Loading…</div>
          ) : orders.length === 0 ? (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">
              No orders found for this customer on {date}.
            </div>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead className="bg-gray-100 sticky top-0 z-10">
                <tr className="text-gray-700 border-b border-gray-300">
                  <th className="text-left px-3 py-1.5 font-semibold">Order #</th>
                  <th className="text-left px-3 py-1.5 font-semibold">Description</th>
                  <th className="text-left px-3 py-1.5 font-semibold">Order date</th>
                  <th className="text-left px-3 py-1.5 font-semibold">Delivery</th>
                  <th className="text-left px-3 py-1.5 font-semibold">Status</th>
                  <th className="text-right px-3 py-1.5 font-semibold">Total (incl)</th>
                  <th className="w-24"></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((o) => {
                  const { tier, label } = classifyDeliveryAge(o.delivery_date, todayIso);
                  return (
                    <tr key={o.order_no} className="border-b border-gray-100 hover:bg-blue-50">
                      <td className="px-3 py-1.5 font-mono text-gray-800">{o.order_no}</td>
                      <td className="px-3 py-1.5 text-gray-700 truncate max-w-[14rem]" title={o.description}>
                        {o.description || "—"}
                      </td>
                      <td className="px-3 py-1.5 text-gray-700 whitespace-nowrap font-mono">
                        {fmtDate(o.order_date)}
                      </td>
                      <td className="px-3 py-1.5 text-gray-700 whitespace-nowrap">
                        <span className="font-mono">{fmtDate(o.delivery_date)}</span>
                        <span
                          className={
                            "ml-1.5 inline-block px-1.5 py-[1px] rounded-full text-[9px] font-semibold uppercase tracking-wide border " +
                            TIER_CLASSES[tier]
                          }
                        >
                          {label}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-gray-700">{o.doc_state_label}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-gray-900">
                        {o.total_incl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        {onViewOrder && (
                          <button
                            onClick={() => onViewOrder(o.order_no)}
                            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 underline"
                          >
                            <ExternalLink className="w-3 h-3" />
                            View
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-gray-300 bg-gray-50">
                  <td className="px-3 py-1.5 font-semibold text-gray-700" colSpan={5}>
                    {orders.length} order{orders.length === 1 ? "" : "s"}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono font-semibold text-gray-900">
                    {total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </td>
                  <td></td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

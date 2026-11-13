import { ListOrdered } from "lucide-react";
import type { CustomerDetail } from "../api/types";
import { fmtCurrency } from "../utils/format";
import { TIER_CLASSES, type DeliveryAgeTier } from "../utils/deliveryAge";

type Props = {
  customer: CustomerDetail;
  currentOrderIncl: number;
  compact?: boolean;
  /** Count of unprocessed sales orders for this customer (any date). When
   *  > 0 and `onShowUnprocessed` is supplied, a clickable pill shows
   *  next to the "Existing orders" line. Omitted contexts (e.g. Customer
   *  Overview) render the status as before. */
  unprocessedCount?: number;
  /** Worst delivery-date tier across those orders. Picks the pill color so
   *  an overdue order makes the badge red at a glance. */
  unprocessedWorstTier?: DeliveryAgeTier;
  onShowUnprocessed?: () => void;
  /** Tight panels (Order Assistant draft): the badge line plus the four
   *  figures packed into one always-visible row. */
  dense?: boolean;
};

export function CreditStatus({
  customer,
  currentOrderIncl,
  compact,
  unprocessedCount,
  unprocessedWorstTier,
  onShowUnprocessed,
  dense = false,
}: Props) {
  const untracked = customer.credit_limit <= 0;
  const projected = customer.available_credit - currentOrderIncl;
  const over = !untracked && projected < 0;

  const base = compact ? "credit-status credit-status--compact" : "credit-status";
  const cls = customer.over_terms
    ? `${base} credit-status--over`
    : untracked
    ? base
    : over
    ? `${base} credit-status--over`
    : `${base} credit-status--ok`;
  const badge = customer.over_terms
    ? "OVER TERMS"
    : untracked
    ? "NO LIMIT"
    : over
    ? "OVER LIMIT"
    : "OK";
  const termDesc =
    customer.terms_days > 0 ? `${customer.terms_days} days` : "current period";

  const unprocessedPill =
    unprocessedCount !== undefined && unprocessedCount > 0 && onShowUnprocessed ? (
      <button
        type="button"
        onClick={onShowUnprocessed}
        title="View all unprocessed sales orders for this customer"
        className={
          "inline-flex items-center gap-1 text-[9px] font-semibold border px-1.5 py-[1px] rounded-full " +
          TIER_CLASSES[unprocessedWorstTier ?? "none"]
        }
      >
        <ListOrdered className="w-3 h-3" />
        {unprocessedCount}
      </button>
    ) : null;
  const showLines = !untracked;

  return (
    <div className={cls}>
      <div className="credit-status-header">
        <span className="credit-status-badge">{badge}</span>
        {customer.over_terms ? (
          <span className="credit-status-detail">
            {fmtCurrency(customer.overdue_amount)} overdue past {termDesc}
          </span>
        ) : !untracked ? (
          <span className="credit-status-detail">
            {over
              ? `Over by ${fmtCurrency(-projected)}`
              : `${fmtCurrency(projected)} available`}
          </span>
        ) : null}
        {dense && unprocessedPill}
      </div>
      {showLines && dense && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] leading-snug text-gray-500 tabular-nums">
          <span className="truncate" title="Credit limit">
            Limit <span className="text-gray-800">{fmtCurrency(customer.credit_limit)}</span>
          </span>
          <span className="truncate" title="Outstanding invoices">
            Outst. <span className="text-gray-800">{fmtCurrency(customer.total_outstanding)}</span>
          </span>
          <span className="truncate" title="Existing (unprocessed) sales orders">
            Existing <span className="text-gray-800">{fmtCurrency(customer.total_ordered)}</span>
          </span>
          <span className="truncate" title="This order (incl)">
            This order <span className="text-gray-800">{fmtCurrency(currentOrderIncl)}</span>
          </span>
        </div>
      )}
      {showLines && !dense && (
        <div className="credit-status-lines">
          <span>Credit limit: {fmtCurrency(customer.credit_limit)}</span>
          <span>Outstanding: {fmtCurrency(customer.total_outstanding)}</span>
          <span className="flex items-center gap-1">
            {unprocessedPill}
            Existing orders: {fmtCurrency(customer.total_ordered)}
          </span>
          {currentOrderIncl > 0 && (
            <span>This order: {fmtCurrency(currentOrderIncl)}</span>
          )}
        </div>
      )}
    </div>
  );
}

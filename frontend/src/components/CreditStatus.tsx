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
};

export function CreditStatus({
  customer,
  currentOrderIncl,
  compact,
  unprocessedCount,
  unprocessedWorstTier,
  onShowUnprocessed,
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
      </div>
      {!untracked && (
        <div className="credit-status-lines">
          <span>Credit limit: {fmtCurrency(customer.credit_limit)}</span>
          <span>Outstanding: {fmtCurrency(customer.total_outstanding)}</span>
          <span className="flex items-center gap-1">
            {unprocessedCount !== undefined &&
              unprocessedCount > 0 &&
              onShowUnprocessed && (
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
              )}
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

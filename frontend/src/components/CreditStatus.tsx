import type { CustomerDetail } from "../api/types";
import { fmtCurrency } from "../utils/format";

type Props = {
  customer: CustomerDetail;
  currentOrderIncl: number;
  compact?: boolean;
};

export function CreditStatus({ customer, currentOrderIncl, compact }: Props) {
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
          <span>Existing orders: {fmtCurrency(customer.total_ordered)}</span>
          {currentOrderIncl > 0 && (
            <span>This order: {fmtCurrency(currentOrderIncl)}</span>
          )}
        </div>
      )}
    </div>
  );
}

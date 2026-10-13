import type { TaxType } from "../api/types";
import { orderTotals, type LineRow, type TaxMode } from "./LineItemsGrid";
import { fmtCurrency } from "../utils/format";

type Props = {
  lines: LineRow[];
  taxTypes: TaxType[];
  taxMode: TaxMode;
  documentDiscount: number;
};

export function TotalsPanel({ lines, taxTypes, taxMode, documentDiscount }: Props) {
  const { excl, tax, incl } = orderTotals(lines, taxTypes, taxMode, documentDiscount);
  const fmt = fmtCurrency;

  return (
    <section className="totals-panel">
      <div className="totals-title">Home Totals</div>
      <div className="totals-grid">
        <div className="totals-head">Ordered</div>

        <div className="totals-label">Exclusive Total</div>
        <div className="totals-value">{fmt(excl)}</div>

        <div className="totals-label">Tax Amount</div>
        <div className="totals-value">{fmt(tax)}</div>

        <div className="totals-label">Inclusive Total</div>
        <div className="totals-value strong">{fmt(incl)}</div>
      </div>
    </section>
  );
}

import { DateInput } from "./DateInput";

type Props = {
  externalOrder: string;
  orderDate: string;
  dueDate: string;
  /** When true, the External Order field is marked required: red border when
   *  empty, "Required" hint, and a `*` on the label. Driven by Evolution's
   *  OrdersDf.bForceExtOrderNum. */
  externalOrderRequired?: boolean;
  onChange: (patch: Partial<{
    externalOrder: string;
    orderDate: string;
    dueDate: string;
  }>) => void;
};

export function OrderInfoPanel({
  externalOrder,
  orderDate,
  dueDate,
  externalOrderRequired = false,
  onChange,
}: Props) {
  const extMissing = externalOrderRequired && !externalOrder.trim();
  return (
    <section className="panel">
      <header className="panel-header">Order</header>
      <div className="grid-2">
        <label>
          External Order{externalOrderRequired && <span style={{ color: "#dc2626" }}> *</span>}
        </label>
        <div>
          <input
            className="input"
            value={externalOrder}
            onChange={(e) => onChange({ externalOrder: e.target.value })}
            style={extMissing ? { borderColor: "#dc2626", backgroundColor: "#fef2f2" } : undefined}
            placeholder={externalOrderRequired ? "Required" : ""}
          />
          {extMissing && (
            <div style={{ fontSize: "11px", color: "#b91c1c", marginTop: "2px" }}>
              External Order Number is required.
            </div>
          )}
        </div>

        <label>Order Date</label>
        <DateInput value={orderDate} onChange={(v) => onChange({ orderDate: v })} />

        <label>Due Date</label>
        <DateInput value={dueDate} onChange={(v) => onChange({ dueDate: v })} />
      </div>
    </section>
  );
}

import { DateInput } from "./DateInput";

type Props = {
  externalOrder: string;
  orderDate: string;
  dueDate: string;
  onChange: (patch: Partial<{
    externalOrder: string;
    orderDate: string;
    dueDate: string;
  }>) => void;
};

export function OrderInfoPanel({ externalOrder, orderDate, dueDate, onChange }: Props) {
  return (
    <section className="panel">
      <header className="panel-header">Order</header>
      <div className="grid-2">
        <label>External Order</label>
        <input
          className="input"
          value={externalOrder}
          onChange={(e) => onChange({ externalOrder: e.target.value })}
        />

        <label>Order Date</label>
        <DateInput value={orderDate} onChange={(v) => onChange({ orderDate: v })} />

        <label>Due Date</label>
        <DateInput value={dueDate} onChange={(v) => onChange({ dueDate: v })} />
      </div>
    </section>
  );
}

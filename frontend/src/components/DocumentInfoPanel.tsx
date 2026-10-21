import { DateInput } from "./DateInput";

type Props = {
  invoiceDate: string;
  deliveryDate: string;
  onChange: (patch: Partial<{
    invoiceDate: string;
    deliveryDate: string;
  }>) => void;
};

export function DocumentInfoPanel({
  invoiceDate, deliveryDate,
  onChange,
}: Props) {
  return (
    <section className="panel">
      <header className="panel-header">Document</header>
      <div className="grid-2">
        <label>Invoice Date</label>
        <DateInput value={invoiceDate} onChange={(v) => onChange({ invoiceDate: v })} />

        <label>Delivery Date</label>
        <DateInput value={deliveryDate} onChange={(v) => onChange({ deliveryDate: v })} />
      </div>
    </section>
  );
}

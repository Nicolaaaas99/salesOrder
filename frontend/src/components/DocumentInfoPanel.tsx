import { DateInput } from "./DateInput";
import { ProjectPicker } from "./ProjectPicker";

type Props = {
  invoiceDate: string;
  deliveryDate: string;
  defaultProject: string;
  onChange: (patch: Partial<{
    invoiceDate: string;
    deliveryDate: string;
    defaultProject: string;
  }>) => void;
};

export function DocumentInfoPanel({
  invoiceDate, deliveryDate, defaultProject,
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

        <label>Default Project</label>
        <ProjectPicker
          value={defaultProject}
          onSelect={(code) => onChange({ defaultProject: code })}
        />
      </div>
    </section>
  );
}

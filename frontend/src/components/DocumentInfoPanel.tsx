import { DateInput } from "./DateInput";
import { ProjectPicker } from "./ProjectPicker";

type Props = {
  invoiceDate: string;
  defaultProject: string;
  onChange: (patch: Partial<{
    invoiceDate: string;
    defaultProject: string;
  }>) => void;
};

export function DocumentInfoPanel({
  invoiceDate, defaultProject,
  onChange,
}: Props) {
  return (
    <section className="panel">
      <header className="panel-header">Document</header>
      <div className="grid-2">
        <label>Invoice Date</label>
        <DateInput value={invoiceDate} onChange={(v) => onChange({ invoiceDate: v })} />

        <label>Default Project</label>
        <ProjectPicker
          value={defaultProject}
          onSelect={(code) => onChange({ defaultProject: code })}
        />
      </div>
    </section>
  );
}

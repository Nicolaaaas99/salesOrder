/** Shared bits for the Phase 5 management screens (Analytics → forecasting). */
import { useEffect, type ReactNode } from "react";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { SCOPE_DIMS, type Scenario } from "../../api/forecasting";

const qtyFmt = new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 1 });
export const fmtQty = (n: number | null | undefined) => (n == null ? "—" : qtyFmt.format(n));

export function fmtPct(n: number | null | undefined, digits = 0): string {
  if (n == null || !isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

/** dd/mm/yyyy from an ISO date or datetime, without timezone drift. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return d && m && y ? `${d}/${m}/${y}` : iso;
}

/** Monday on or before an ISO date -- ISO weeks, Mon-Sun (matches
 *  backend weeks.week_start). */
export function weekStartIso(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7));
  return dt.toISOString().slice(0, 10);
}

export function addDaysIso(iso: string, days: number): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

export function ScreenFrame({
  icon,
  title,
  onBack,
  toolbar,
  children,
  maxWidth = "1200px",
}: {
  icon: ReactNode;
  title: string;
  onBack?: () => void;
  toolbar?: ReactNode;
  children: ReactNode;
  maxWidth?: string;
}) {
  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="mx-auto w-full flex-1 min-h-0 flex flex-col gap-2" style={{ maxWidth }}>
        {onBack && (
          <div className="flex items-center flex-shrink-0">
            <button
              onClick={onBack}
              className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to Analytics
            </button>
          </div>
        )}
        <div className="flex items-center gap-2 flex-wrap flex-shrink-0 bg-white rounded border p-2 shadow-sm">
          {icon}
          <div className="text-sm font-semibold text-gray-900">{title}</div>
          <div className="flex-1" />
          {toolbar}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`px-2 py-1 ${i > 0 ? "border-l border-gray-300" : ""} ${
            value === o.value
              ? "bg-blue-600 text-white font-semibold"
              : "bg-white text-gray-700 hover:bg-gray-50"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ErrorBanner({ message, onClose }: { message: string | null; onClose?: () => void }) {
  if (!message) return null;
  return (
    <div className="flex items-start gap-2 bg-red-50 border border-red-200 text-red-700 text-xs rounded p-2">
      <span className="flex-1 whitespace-pre-line">{message}</span>
      {onClose && (
        <button onClick={onClose} className="text-red-500 hover:text-red-700" aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  );
}

/** Committed-vs-target bar; green once met. */
export function ProgressBar({ value, target }: { value: number; target: number }) {
  const pct = target > 0 ? Math.min(100, (value / target) * 100) : value > 0 ? 100 : 0;
  const met = target > 0 ? value >= target - 1e-6 : true;
  return (
    <div className="flex items-center gap-1.5 min-w-[7rem]">
      <div className="flex-1 h-1.5 rounded bg-gray-200 overflow-hidden">
        <div className={`h-full rounded ${met ? "bg-emerald-500" : "bg-blue-500"}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-[11px] font-mono w-10 text-right ${met ? "text-emerald-700" : "text-gray-600"}`}>
        {target > 0 ? `${Math.round((value / target) * 100)}%` : "—"}
      </span>
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-700 border-gray-200",
  PREVIEW: "bg-amber-100 text-amber-800 border-amber-200",
  ACTIVE: "bg-emerald-100 text-emerald-800 border-emerald-200",
  COMPLETED: "bg-blue-100 text-blue-700 border-blue-200",
  CANCELLED: "bg-red-50 text-red-700 border-red-200",
};

export function StatusChip({ status }: { status: string }) {
  return (
    <span className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${STATUS_CLASS[status] ?? STATUS_CLASS.DRAFT}`}>
      {status}
    </span>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** ISO-8601 week number of the week containing `iso` (the week's
 *  Thursday decides the year). */
export function isoWeekNumber(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7) + 3); // Thursday of this ISO week
  const jan4 = new Date(Date.UTC(dt.getUTCFullYear(), 0, 4));
  const jan4Thursday = jan4.getTime() + (3 - ((jan4.getUTCDay() + 6) % 7)) * 86400000;
  return 1 + Math.round((dt.getTime() - jan4Thursday) / (7 * 86400000));
}

/** "Wk 46 · 8–14 Nov" for a week starting at `weekStart`. */
export function fmtWeekLabel(weekStart: string): string {
  const end = addDaysIso(weekStart, 6);
  const [, sm, sd] = weekStart.split("-").map(Number);
  const [, em, ed] = end.split("-").map(Number);
  const range = sm === em
    ? `${sd}–${ed} ${MONTHS[em - 1]}`
    : `${sd} ${MONTHS[sm - 1]} – ${ed} ${MONTHS[em - 1]}`;
  return `Wk ${isoWeekNumber(weekStart)} · ${range}`;
}

/** Green confirmation banner that fades out on its own. Pass a new
 *  `notice` object (not just new text) to re-show the same message. */
export function SuccessNotice({
  notice,
  onDone,
  ms = 5000,
}: {
  notice: { text: string } | null;
  onDone: () => void;
  ms?: number;
}) {
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(onDone, ms);
    return () => window.clearTimeout(t);
  }, [notice, ms, onDone]);
  if (!notice) return null;
  return (
    <div className="flex items-start gap-2 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs rounded p-2">
      <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
      <span className="flex-1 whitespace-pre-line">{notice.text}</span>
      <button onClick={onDone} className="text-emerald-600 hover:text-emerald-800" aria-label="Dismiss">×</button>
    </div>
  );
}


/** "+5%", "+200 / wk", "= 250 000 L / wk". */
export function leverText(s: Pick<Scenario, "lever_type" | "lever_value"> & { measure?: string }): string {
  const v = s.lever_value;
  const unit = s.measure === "LITRES" ? " L" : "";
  if (s.lever_type === "PERCENT") return `${v > 0 ? "+" : ""}${v}%`;
  if (s.lever_type === "ABSOLUTE_DELTA") return `${v > 0 ? "+" : ""}${fmtQty(v)}${unit} / wk`;
  return `= ${fmtQty(v)}${unit} / wk`;
}

export function scopeText(s: Scenario): string {
  const parts = SCOPE_DIMS.filter((d) => s.scope[d.key].length).map(
    (d) => `${d.label}: ${s.scope[d.key].slice(0, 3).join(", ")}${s.scope[d.key].length > 3 ? ` +${s.scope[d.key].length - 3}` : ""}`,
  );
  const excl = s.scope.excludeLeaves.length ? ` · ${s.scope.excludeLeaves.length} excluded` : "";
  return (parts.join(" · ") || "Everything") + excl;
}

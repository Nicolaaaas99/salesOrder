import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Calendar, Download, RefreshCw, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import type {
  OrderIntakeDrillFilter,
  OrderIntakeMetrics,
  OrderIntakeOrder,
  OrderIntakeRow,
  OrderIntakeSource,
} from "../api/types";
import { DateInput } from "./DateInput";
import { OrderSummaryModal } from "./OrderSummaryModal";
import { today } from "../utils/date";
import { extractErrorMsg } from "../utils/format";

/** Order Intake dashboard — orders captured per hour, SO Assistant app vs
 *  Evolution. Data loads on open, on date change and on Refresh only (no
 *  timers — see PERFORMANCE.md). Filters (source / corp group / user) are
 *  applied client-side to the already-loaded hourly buckets. */

// ---------- series identity (fixed per entity — never by rank) ----------
// Evolution = WhatsApp green (#25D366), SO Assistant = blue. Validated with
// the dataviz palette checker: CVD ΔE 32.9, normal-vision ΔE 34.5. The green
// is under 3:1 contrast on white, so it always ships with the legend,
// tooltip and the user x hour table (never colour alone).
const SOURCES: OrderIntakeSource[] = ["SO Assistant", "Evolution"];
const SOURCE_COLOR: Record<OrderIntakeSource, string> = {
  "SO Assistant": "#2a78d6",
  Evolution: "#25D366",
};

type MetricKey = "orders" | "qty" | "litres" | "excl";
type Basis = "ordered" | "invoiced";

const METRICS: { key: MetricKey; label: string }[] = [
  { key: "orders", label: "Orders" },
  { key: "qty", label: "Qty" },
  { key: "litres", label: "Litres" },
  { key: "excl", label: "Amount (excl)" },
];

function metricValue(m: OrderIntakeMetrics, key: MetricKey, basis: Basis): number {
  if (key === "orders") return m.orders;
  if (key === "qty") return basis === "ordered" ? m.ordered_qty : m.invoiced_qty;
  if (key === "litres") return basis === "ordered" ? m.ordered_litres : m.invoiced_litres;
  return basis === "ordered" ? m.ordered_excl : m.invoiced_excl;
}

const EMPTY: OrderIntakeMetrics = {
  orders: 0, ordered_qty: 0, ordered_litres: 0, ordered_excl: 0,
  invoiced_qty: 0, invoiced_litres: 0, invoiced_excl: 0,
};

function addInto(acc: OrderIntakeMetrics, r: OrderIntakeMetrics) {
  acc.orders += r.orders;
  acc.ordered_qty += r.ordered_qty;
  acc.ordered_litres += r.ordered_litres;
  acc.ordered_excl += r.ordered_excl;
  acc.invoiced_qty += r.invoiced_qty;
  acc.invoiced_litres += r.invoiced_litres;
  acc.invoiced_excl += r.invoiced_excl;
}

const int = new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-ZA", { notation: "compact", maximumFractionDigits: 1 });
function fmtMetric(v: number, key: MetricKey, short = false): string {
  const n = short && Math.abs(v) >= 10000 ? compact.format(v) : int.format(v);
  return key === "excl" ? `R ${n}` : n;
}
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

type Props = {
  onBack?: () => void;
  /** Opens an order in the Sales Order Entry tab (from the summary modal). */
  onOpenOrder?: (orderNo: string) => void;
};

export function OrderIntakeScreen({ onBack, onOpenOrder }: Props) {
  const [fromDate, setFromDate] = useState(today());
  const [toDate, setToDate] = useState(today());
  const [rows, setRows] = useState<OrderIntakeRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [metric, setMetric] = useState<MetricKey>("litres");
  const [basis, setBasis] = useState<Basis>("ordered");
  const [sourceFilter, setSourceFilter] = useState<OrderIntakeSource | "ALL">("ALL");
  const [corpFilter, setCorpFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");

  const [drill, setDrill] = useState<OrderIntakeDrillFilter | null>(null);

  const load = useCallback(async () => {
    if (!fromDate || !toDate) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getOrderIntake(fromDate, toDate);
      setRows(res.rows);
    } catch (e) {
      setError(extractErrorMsg(e, "Failed to load order intake"));
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleExport() {
    setExporting(true);
    try {
      await api.exportOrderIntakeXlsx(fromDate, toDate);
    } catch (e) {
      setError(extractErrorMsg(e, "Export failed"));
    } finally {
      setExporting(false);
    }
  }

  // ---------- filter options + filtered rows ----------
  const corpGroups = useMemo(
    () => Array.from(new Set(rows.map((r) => r.corp_group))).sort(),
    [rows],
  );
  const users = useMemo(
    () => Array.from(new Set(rows.map((r) => r.user))).sort((a, b) => a.localeCompare(b)),
    [rows],
  );
  const filtered = useMemo(
    () =>
      rows.filter(
        (r) =>
          (sourceFilter === "ALL" || r.source === sourceFilter) &&
          (!corpFilter || r.corp_group === corpFilter) &&
          (!userFilter || r.user === userFilter),
      ),
    [rows, sourceFilter, corpFilter, userFilter],
  );
  const visibleSources = sourceFilter === "ALL" ? SOURCES : [sourceFilter];

  // ---------- aggregates ----------
  const bySource = useMemo(() => {
    const m = new Map<OrderIntakeSource, OrderIntakeMetrics>();
    for (const s of SOURCES) m.set(s, { ...EMPTY });
    for (const r of filtered) addInto(m.get(r.source)!, r);
    return m;
  }, [filtered]);
  const total = useMemo(() => {
    const t = { ...EMPTY };
    for (const r of filtered) addInto(t, r);
    return t;
  }, [filtered]);

  // hour -> source -> metrics
  const byHour = useMemo(() => {
    const m = new Map<number, Map<OrderIntakeSource, OrderIntakeMetrics>>();
    for (const r of filtered) {
      if (!m.has(r.hour)) m.set(r.hour, new Map());
      const hm = m.get(r.hour)!;
      if (!hm.has(r.source)) hm.set(r.source, { ...EMPTY });
      addInto(hm.get(r.source)!, r);
    }
    return m;
  }, [filtered]);

  // Show a working-day window by default, widened to wherever data exists.
  const hours = useMemo(() => {
    const present = Array.from(byHour.keys());
    const lo = Math.min(6, ...present);
    const hi = Math.max(18, ...present);
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  }, [byHour]);

  const isRange = fromDate !== toDate;

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col overflow-y-auto">
      <div className="max-w-[1600px] mx-auto w-full flex flex-col gap-2">
        {onBack && (
          <div className="flex items-center">
            <button
              onClick={onBack}
              className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to Analytics
            </button>
          </div>
        )}

        {/* Top bar: date range + actions */}
        <div className="flex items-end gap-2 flex-wrap">
          <DateBox label="Created From">
            <DateInput
              value={fromDate}
              onChange={(v) => {
                setFromDate(v);
                if (v > toDate) setToDate(v);
              }}
            />
          </DateBox>
          <DateBox label="Created To">
            <DateInput
              value={toDate}
              onChange={(v) => {
                setToDate(v);
                if (v < fromDate) setFromDate(v);
              }}
            />
          </DateBox>
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-white border rounded-md shadow-sm hover:bg-gray-50 disabled:opacity-60"
            title="Reload the figures"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-emerald-600 text-white rounded-md shadow-sm hover:bg-emerald-700 disabled:opacity-60"
            title="Download every order in this range as .xlsx"
          >
            <Download className="w-3.5 h-3.5" />
            {exporting ? "Exporting…" : "Export"}
          </button>
        </div>

        {/* Filters + metric controls — one row above the charts */}
        <div className="flex items-center gap-3 flex-wrap bg-white border rounded p-2 shadow-sm text-xs">
          <Segmented
            options={[
              { value: "ALL", label: "All sources" },
              ...SOURCES.map((s) => ({ value: s, label: s })),
            ]}
            value={sourceFilter}
            onChange={(v) => setSourceFilter(v as OrderIntakeSource | "ALL")}
          />
          <select
            className={`border rounded px-2 py-1 ${corpFilter ? "border-blue-500 bg-blue-50 text-blue-800 font-semibold" : "bg-white"}`}
            value={corpFilter}
            onChange={(e) => setCorpFilter(e.target.value)}
          >
            <option value="">All corporate groups</option>
            {corpGroups.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select
            className={`border rounded px-2 py-1 max-w-[14rem] ${userFilter ? "border-blue-500 bg-blue-50 text-blue-800 font-semibold" : "bg-white"}`}
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
          >
            <option value="">All users</option>
            {users.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
          {(sourceFilter !== "ALL" || corpFilter || userFilter) && (
            <button
              type="button"
              onClick={() => {
                setSourceFilter("ALL");
                setCorpFilter("");
                setUserFilter("");
              }}
              className="inline-flex items-center gap-1 text-blue-700 hover:text-blue-900 hover:underline"
              title="Show all sources, corporate groups and users"
            >
              <X className="w-3.5 h-3.5" />
              Clear filters
            </button>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Segmented
              options={METRICS.map((m) => ({ value: m.key, label: m.label }))}
              value={metric}
              onChange={(v) => setMetric(v as MetricKey)}
            />
            <Segmented
              options={[
                { value: "ordered", label: "Ordered" },
                { value: "invoiced", label: "Invoiced" },
              ]}
              value={basis}
              onChange={(v) => setBasis(v as Basis)}
              disabled={metric === "orders"}
            />
          </div>
        </div>

        {error && (
          <div className="p-1.5 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs">
            <XCircle className="w-3.5 h-3.5 mr-1 flex-shrink-0" />
            {error}
          </div>
        )}

        {/* KPI tiles */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          <KpiTile title="Total" metrics={total} total={total} />
          {SOURCES.map((s) => (
            <KpiTile
              key={s}
              title={s}
              color={SOURCE_COLOR[s]}
              metrics={bySource.get(s)!}
              total={total}
              muted={!visibleSources.includes(s)}
            />
          ))}
        </div>

        {/* Hourly chart */}
        <section className="bg-white border rounded shadow-sm p-3">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-gray-900">
              {METRICS.find((m) => m.key === metric)!.label}
              {metric !== "orders" && ` ${basis}`} per hour
              {isRange && <span className="font-normal text-gray-500"> · all days in range combined</span>}
            </h2>
            <Legend sources={visibleSources} />
          </div>
          {loading && rows.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-sm text-gray-400">Loading…</div>
          ) : filtered.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-sm text-gray-400">
              No orders captured in this range.
            </div>
          ) : (
            <HourlyChart
              hours={hours}
              byHour={byHour}
              sources={visibleSources}
              metric={metric}
              basis={basis}
              onPickHour={(hour) => setDrill({ hour })}
            />
          )}
        </section>

        {/* User x hour table — also the accessible table view of the chart */}
        {filtered.length > 0 && (
          <UserHourTable
            rows={filtered}
            hours={hours}
            metric={metric}
            basis={basis}
            onPick={(f) => setDrill(f)}
          />
        )}
      </div>

      {drill && (
        <DrillModal
          from={fromDate}
          to={toDate}
          filter={{
            ...drill,
            source: drill.source ?? (sourceFilter === "ALL" ? null : sourceFilter),
            corp_group: corpFilter || null,
            user: drill.user ?? (userFilter || null),
          }}
          onClose={() => setDrill(null)}
          onOpenOrder={onOpenOrder}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function DateBox({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded border p-2 shadow-sm w-44">
      <label className="flex items-center font-semibold text-gray-800 mb-1 text-xs">
        <Calendar className="w-3 h-3 mr-1 text-blue-600" />
        {label}
      </label>
      {children}
    </div>
  );
}

function Segmented({
  options, value, onChange, disabled,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`inline-flex rounded border overflow-hidden ${disabled ? "opacity-50" : ""}`}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          onClick={() => onChange(o.value)}
          className={`px-2.5 py-1 border-l first:border-l-0 ${
            value === o.value ? "bg-gray-800 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Swatch({ color }: { color: string }) {
  return <span className="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ background: color }} />;
}

function Legend({ sources }: { sources: OrderIntakeSource[] }) {
  return (
    <div className="flex items-center gap-3 text-xs text-gray-600">
      {sources.map((s) => (
        <span key={s} className="inline-flex items-center gap-1.5">
          <Swatch color={SOURCE_COLOR[s]} />
          {s}
        </span>
      ))}
    </div>
  );
}

function KpiTile({
  title, color, metrics, total, muted,
}: {
  title: string;
  color?: string;
  metrics: OrderIntakeMetrics;
  total: OrderIntakeMetrics;
  muted?: boolean;
}) {
  const share = total.orders > 0 && color ? Math.round((metrics.orders / total.orders) * 100) : null;
  return (
    <div className={`bg-white border rounded shadow-sm p-3 ${muted ? "opacity-50" : ""}`}>
      <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
        {color && <Swatch color={color} />}
        {title}
        {share !== null && <span className="ml-auto font-normal text-gray-500">{share}% of orders</span>}
      </div>
      <div className="mt-1 text-2xl font-semibold text-gray-900 tabular-nums">
        {int.format(metrics.orders)} <span className="text-sm font-normal text-gray-500">orders</span>
      </div>
      <dl className="mt-2 grid grid-cols-3 gap-x-2 text-xs tabular-nums">
        <Stat label="Qty" ordered={metrics.ordered_qty} invoiced={metrics.invoiced_qty} k="qty" />
        <Stat label="Litres" ordered={metrics.ordered_litres} invoiced={metrics.invoiced_litres} k="litres" />
        <Stat label="Amount (excl)" ordered={metrics.ordered_excl} invoiced={metrics.invoiced_excl} k="excl" />
      </dl>
    </div>
  );
}

function Stat({ label, ordered, invoiced, k }: { label: string; ordered: number; invoiced: number; k: MetricKey }) {
  return (
    <div>
      <dt className="text-gray-500">{label}</dt>
      <dd className="text-gray-900 font-medium">{fmtMetric(ordered, k, true)}</dd>
      <dd className="text-gray-500" title="Invoiced">inv {fmtMetric(invoiced, k, true)}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hourly stacked bar chart (plain SVG)
// ---------------------------------------------------------------------------

function niceStep(max: number, ticks = 4): number {
  if (max <= 0) return 1;
  const raw = max / ticks;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * pow >= raw) return m * pow;
  return 10 * pow;
}

/** Bar segment with a 4px rounded data-end on top, square at the base. */
function topRoundedPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(800);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

function HourlyChart({
  hours, byHour, sources, metric, basis, onPickHour,
}: {
  hours: number[];
  byHour: Map<number, Map<OrderIntakeSource, OrderIntakeMetrics>>;
  sources: OrderIntakeSource[];
  metric: MetricKey;
  basis: Basis;
  onPickHour: (hour: number) => void;
}) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  const height = 260;
  const m = { top: 12, right: 8, bottom: 24, left: 64 };
  const plotW = Math.max(100, width - m.left - m.right);
  const plotH = height - m.top - m.bottom;

  const val = (h: number, s: OrderIntakeSource) => {
    const x = byHour.get(h)?.get(s);
    return x ? metricValue(x, metric, basis) : 0;
  };
  const totals = hours.map((h) => sources.reduce((a, s) => a + Math.max(0, val(h, s)), 0));
  const step = niceStep(Math.max(...totals, 0));
  const yMax = Math.max(step, Math.ceil(Math.max(...totals, 0) / step) * step);
  const y = (v: number) => m.top + plotH - (v / yMax) * plotH;
  const ticks = Array.from({ length: Math.round(yMax / step) + 1 }, (_, i) => i * step);

  const band = plotW / hours.length;
  const barW = Math.min(24, band * 0.6);
  // "06:00" at 11px is ~32 px wide — skip every other label before they touch.
  const labelEvery = band < 40 ? 2 : 1;
  const GAP = 2;

  const hoverIdx = hover === null ? -1 : hours.indexOf(hover);

  return (
    <div ref={wrapRef} className="relative">
      <svg width={width} height={height} role="img" aria-label="Orders per hour, stacked by source">
        {/* grid + y axis */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={m.left} x2={m.left + plotW} y1={y(t)} y2={y(t)} stroke="#e5e7eb" strokeWidth={1} />
            <text x={m.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="#6b7280">
              {fmtMetric(t, metric, true)}
            </text>
          </g>
        ))}
        {/* bars */}
        {hours.map((h, i) => {
          const cx = m.left + band * i + band / 2;
          const x0 = cx - barW / 2;
          const segs = sources.map((s) => ({ s, v: Math.max(0, val(h, s)) })).filter((d) => d.v > 0);
          let acc = 0;
          return (
            <g key={h}>
              {segs.map((d, j) => {
                const yTop = y(acc + d.v);
                const yBot = y(acc);
                acc += d.v;
                // 2px surface gap between stacked segments (taken off the top
                // of the lower segment so the stack height stays honest).
                const isTop = j === segs.length - 1;
                const hgt = Math.max(0, yBot - yTop - (isTop ? 0 : GAP));
                const yy = isTop ? yTop : yTop + GAP;
                return isTop ? (
                  <path key={d.s} d={topRoundedPath(x0, yy, barW, hgt, 4)} fill={SOURCE_COLOR[d.s]} />
                ) : (
                  <rect key={d.s} x={x0} y={yy} width={barW} height={hgt} fill={SOURCE_COLOR[d.s]} />
                );
              })}
              {i % labelEvery === 0 && (
                <text x={cx} y={m.top + plotH + 16} textAnchor="middle" fontSize={11} fill="#6b7280">
                  {hh(h)}
                </text>
              )}
              {/* hit target: the whole band, bigger than the mark */}
              <rect
                x={m.left + band * i}
                y={m.top}
                width={band}
                height={plotH}
                fill={hover === h ? "rgba(17,24,39,0.04)" : "transparent"}
                style={{ cursor: totals[i] > 0 ? "pointer" : "default" }}
                onMouseEnter={() => setHover(h)}
                onMouseLeave={() => setHover(null)}
                onClick={() => totals[i] > 0 && onPickHour(h)}
              />
            </g>
          );
        })}
        <line x1={m.left} x2={m.left + plotW} y1={m.top + plotH} y2={m.top + plotH} stroke="#9ca3af" strokeWidth={1} />
      </svg>

      {hoverIdx >= 0 && (
        <div
          className="absolute pointer-events-none bg-white border shadow-md rounded px-2.5 py-2 text-xs"
          style={{
            left: Math.min(m.left + band * hoverIdx + band / 2 + 12, width - 190),
            top: 8,
            width: 180,
          }}
        >
          <div className="font-semibold text-gray-900 mb-1">
            {hh(hours[hoverIdx])}–{String(hours[hoverIdx]).padStart(2, "0")}:59
          </div>
          {sources.map((s) => (
            <div key={s} className="flex items-center gap-1.5 text-gray-700">
              <Swatch color={SOURCE_COLOR[s]} />
              <span>{s}</span>
              <span className="ml-auto tabular-nums text-gray-900">{fmtMetric(val(hours[hoverIdx], s), metric)}</span>
            </div>
          ))}
          {sources.length > 1 && (
            <div className="flex border-t mt-1 pt-1 text-gray-700">
              <span>Total</span>
              <span className="ml-auto tabular-nums font-semibold text-gray-900">
                {fmtMetric(totals[hoverIdx], metric)}
              </span>
            </div>
          )}
          {totals[hoverIdx] > 0 && <div className="mt-1 text-[10px] text-gray-400">Click to see the orders</div>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// User x hour table
// ---------------------------------------------------------------------------

function UserHourTable({
  rows, hours, metric, basis, onPick,
}: {
  rows: OrderIntakeRow[];
  hours: number[];
  metric: MetricKey;
  basis: Basis;
  onPick: (f: OrderIntakeDrillFilter) => void;
}) {
  const { users, colTotals, grand } = useMemo(() => {
    const map = new Map<string, { user: string; sources: Set<OrderIntakeSource>; cells: Map<number, number>; total: number }>();
    const colTotals = new Map<number, number>();
    let grand = 0;
    for (const r of rows) {
      const v = metricValue(r, metric, basis);
      if (!map.has(r.user)) map.set(r.user, { user: r.user, sources: new Set(), cells: new Map(), total: 0 });
      const u = map.get(r.user)!;
      u.sources.add(r.source);
      u.cells.set(r.hour, (u.cells.get(r.hour) ?? 0) + v);
      u.total += v;
      colTotals.set(r.hour, (colTotals.get(r.hour) ?? 0) + v);
      grand += v;
    }
    const users = Array.from(map.values()).sort((a, b) => b.total - a.total);
    return { users, colTotals, grand };
  }, [rows, metric, basis]);

  return (
    <section className="bg-white border rounded shadow-sm">
      <h2 className="text-sm font-semibold text-gray-900 px-3 pt-3 pb-2">
        By user and hour <span className="font-normal text-gray-500">· click a number to see the orders</span>
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-gray-500 border-b">
              <th className="text-left font-medium px-3 py-1.5 sticky left-0 bg-white">User</th>
              {hours.map((h) => (
                <th key={h} className="text-right font-medium px-2 py-1.5 whitespace-nowrap">{hh(h)}</th>
              ))}
              <th className="text-right font-semibold px-3 py-1.5 text-gray-700">Total</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.user} className="border-b last:border-b-0 hover:bg-gray-50">
                <td className="px-3 py-1.5 sticky left-0 bg-white whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    {SOURCES.filter((s) => u.sources.has(s)).map((s) => (
                      <span key={s} title={s}><Swatch color={SOURCE_COLOR[s]} /></span>
                    ))}
                    <span className="text-gray-900">{u.user}</span>
                  </span>
                </td>
                {hours.map((h) => {
                  const v = u.cells.get(h) ?? 0;
                  return (
                    <td key={h} className="text-right px-2 py-1.5">
                      {v ? (
                        <button
                          className="text-gray-900 hover:text-blue-700 hover:underline"
                          onClick={() => onPick({ hour: h, user: u.user })}
                        >
                          {fmtMetric(v, metric, true)}
                        </button>
                      ) : (
                        <span className="text-gray-300">·</span>
                      )}
                    </td>
                  );
                })}
                <td className="text-right px-3 py-1.5 font-semibold">
                  <button className="hover:text-blue-700 hover:underline" onClick={() => onPick({ user: u.user })}>
                    {fmtMetric(u.total, metric, true)}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t bg-gray-50 font-semibold text-gray-900">
              <td className="px-3 py-1.5 sticky left-0 bg-gray-50">Total</td>
              {hours.map((h) => (
                <td key={h} className="text-right px-2 py-1.5">
                  {colTotals.get(h) ? fmtMetric(colTotals.get(h)!, metric, true) : ""}
                </td>
              ))}
              <td className="text-right px-3 py-1.5">{fmtMetric(grand, metric, true)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Drill-down: the orders behind a bar / cell
// ---------------------------------------------------------------------------

function DrillModal({
  from, to, filter, onClose, onOpenOrder,
}: {
  from: string;
  to: string;
  filter: OrderIntakeDrillFilter;
  onClose: () => void;
  onOpenOrder?: (orderNo: string) => void;
}) {
  const [orders, setOrders] = useState<OrderIntakeOrder[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listOrderIntakeOrders(from, to, filter)
      .then((d) => alive && setOrders(d))
      .catch((e) => alive && setError(extractErrorMsg(e, "Failed to load orders")));
    return () => {
      alive = false;
    };
    // filter is rebuilt each render by the parent; key on its fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to, filter.hour, filter.source, filter.user, filter.corp_group]);

  const title = [
    filter.hour != null ? hh(filter.hour) : null,
    filter.user,
    filter.source,
    filter.corp_group,
  ].filter(Boolean).join(" · ") || "All orders";

  // Clicking a source card narrows the list to that source; click it again
  // (or "Show all") to clear. The cards themselves always show both.
  const [sourcePick, setSourcePick] = useState<OrderIntakeSource | null>(null);
  const shown = (orders ?? []).filter((o) => !sourcePick || o.source === sourcePick);

  const sum = (k: keyof OrderIntakeOrder) =>
    shown.reduce((a, o) => a + (o[k] as number), 0);

  // Same SO Assistant / Evolution split the bar's hover tooltip shows.
  const bySource = SOURCES.map((s) => {
    const list = (orders ?? []).filter((o) => o.source === s);
    return {
      source: s,
      orders: list.length,
      qty: list.reduce((a, o) => a + o.ordered_qty, 0),
      litres: list.reduce((a, o) => a + o.ordered_litres, 0),
      excl: list.reduce((a, o) => a + o.ordered_excl, 0),
    };
  }).filter((b) => b.orders > 0);

  return (
    <div className="fixed inset-0 z-40 bg-black/30 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded shadow-lg max-w-6xl w-full max-h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b bg-gray-50 flex items-center justify-between">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wide">Orders</div>
            <div className="text-sm font-semibold text-gray-900">{title}</div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>
        {orders && orders.length > 0 && (
          <div className="px-4 py-2 border-b flex flex-wrap items-center gap-2 text-xs">
            {bySource.map((b) => {
              const active = sourcePick === b.source;
              return (
              <button
                key={b.source}
                type="button"
                onClick={() => setSourcePick(active ? null : b.source)}
                aria-pressed={active}
                title={active ? "Show all sources" : `Show only ${b.source} orders`}
                className={`text-left border rounded px-3 py-1.5 min-w-[14rem] transition ${
                  active
                    ? "ring-2 ring-blue-500 border-transparent bg-blue-50"
                    : sourcePick
                    ? "bg-white opacity-50 hover:opacity-100"
                    : "bg-white hover:bg-gray-50"
                }`}
              >
                <div className="flex items-center gap-1.5 font-semibold text-gray-800">
                  <Swatch color={SOURCE_COLOR[b.source]} />
                  {b.source}
                  <span className="ml-auto font-normal text-gray-500">
                    {Math.round((b.orders / orders.length) * 100)}% of orders
                  </span>
                </div>
                <div className="mt-0.5 text-gray-600 tabular-nums">
                  <span className="text-gray-900 font-medium">{int.format(b.orders)}</span> orders ·{" "}
                  {int.format(b.qty)} qty · {int.format(b.litres)} L · {fmtMetric(b.excl, "excl")}
                </div>
              </button>
              );
            })}
            {sourcePick && (
              <button
                type="button"
                onClick={() => setSourcePick(null)}
                className="inline-flex items-center gap-1 text-blue-700 hover:text-blue-900 hover:underline"
              >
                <X className="w-3.5 h-3.5" />
                Show all
              </button>
            )}
          </div>
        )}
        <div className="overflow-auto">
          {error && <div className="p-3 text-xs text-red-700">{error}</div>}
          {!orders && !error && <div className="p-6 text-center text-sm text-gray-400">Loading…</div>}
          {orders && (
            <table className="w-full text-xs tabular-nums">
              <thead className="sticky top-0 bg-white">
                <tr className="text-gray-500 border-b">
                  <th className="text-left font-medium px-3 py-1.5">Captured</th>
                  <th className="text-left font-medium px-2 py-1.5">Order</th>
                  <th className="text-left font-medium px-2 py-1.5">Customer</th>
                  <th className="text-left font-medium px-2 py-1.5">Group</th>
                  <th className="text-left font-medium px-2 py-1.5">User</th>
                  <th className="text-left font-medium px-2 py-1.5">Source</th>
                  <th className="text-right font-medium px-2 py-1.5">Qty</th>
                  <th className="text-right font-medium px-2 py-1.5">Litres</th>
                  <th className="text-right font-medium px-2 py-1.5">Excl</th>
                  <th className="text-right font-medium px-2 py-1.5">Inv litres</th>
                  <th className="text-right font-medium px-3 py-1.5">Inv excl</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((o) => (
                  <tr key={o.order_no} className="border-b hover:bg-gray-50">
                    <td className="px-3 py-1.5 whitespace-nowrap">{o.created_at.replace("T", " ").slice(0, 16)}</td>
                    <td className="px-2 py-1.5">
                      <button className="text-blue-700 hover:underline" onClick={() => setViewing(o.order_no)}>
                        {o.order_no}
                      </button>
                    </td>
                    <td className="px-2 py-1.5 truncate max-w-[16rem]" title={o.customer_name}>
                      {o.customer_account} · {o.customer_name}
                    </td>
                    <td className="px-2 py-1.5">{o.corp_group}</td>
                    <td className="px-2 py-1.5">{o.user}</td>
                    <td className="px-2 py-1.5">
                      <span className="inline-flex items-center gap-1.5">
                        <Swatch color={SOURCE_COLOR[o.source]} />
                        {o.source}
                      </span>
                    </td>
                    <td className="text-right px-2 py-1.5">{int.format(o.ordered_qty)}</td>
                    <td className="text-right px-2 py-1.5">{int.format(o.ordered_litres)}</td>
                    <td className="text-right px-2 py-1.5">{fmtMetric(o.ordered_excl, "excl")}</td>
                    <td className="text-right px-2 py-1.5">{int.format(o.invoiced_litres)}</td>
                    <td className="text-right px-3 py-1.5">{fmtMetric(o.invoiced_excl, "excl")}</td>
                  </tr>
                ))}
                {orders.length === 0 && (
                  <tr><td colSpan={11} className="p-6 text-center text-gray-400">No orders.</td></tr>
                )}
              </tbody>
              {shown.length > 0 && (
                <tfoot>
                  <tr className="border-t bg-gray-50 font-semibold">
                    <td className="px-3 py-1.5" colSpan={6}>
                      {shown.length} orders{sourcePick && ` · ${sourcePick} only`}
                    </td>
                    <td className="text-right px-2 py-1.5">{int.format(sum("ordered_qty"))}</td>
                    <td className="text-right px-2 py-1.5">{int.format(sum("ordered_litres"))}</td>
                    <td className="text-right px-2 py-1.5">{fmtMetric(sum("ordered_excl"), "excl")}</td>
                    <td className="text-right px-2 py-1.5">{int.format(sum("invoiced_litres"))}</td>
                    <td className="text-right px-3 py-1.5">{fmtMetric(sum("invoiced_excl"), "excl")}</td>
                  </tr>
                </tfoot>
              )}
            </table>
          )}
        </div>
      </div>

      {viewing && (
        <div onClick={(e) => e.stopPropagation()}>
          <OrderSummaryModal
            orderNo={viewing}
            onClose={() => setViewing(null)}
            onEdit={
              onOpenOrder
                ? (no) => {
                    setViewing(null);
                    onClose();
                    onOpenOrder(no);
                  }
                : undefined
            }
          />
        </div>
      )}
    </div>
  );
}

import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Calendar, ChevronDown, ChevronRight, Download, RefreshCw, X, XCircle } from "lucide-react";
import Select, { type StylesConfig } from "react-select";
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
 *  timers — see PERFORMANCE.md). Filters (source / DC / route / corp group / agent) are
 *  applied client-side to the already-loaded hourly buckets. */

// ---------- series identity (fixed per entity — never by rank) ----------
// Evolution = WhatsApp green (#25D366), SO Assistant = blue. Validated with
// the dataviz palette checker: CVD ΔE 32.9, normal-vision ΔE 34.5. The green
// is under 3:1 contrast on white, so it always ships with the legend,
// tooltip and the agent x hour table (never colour alone).
const SOURCES: OrderIntakeSource[] = ["SO Assistant", "Evolution"];
const SOURCE_COLOR: Record<OrderIntakeSource, string> = {
  "SO Assistant": "#2a78d6",
  Evolution: "#25D366",
};

type MetricKey = "orders" | "qty" | "litres" | "excl";
// Lost = ordered - invoiced on ARCHIVED orders only (an unprocessed order
// isn't lost, it just isn't invoiced yet). Unprocessed (internally "pending")
// = ordered on orders still unprocessed in Evolution (DocState 1).
// Ordered = Invoiced + Lost + Unprocessed.
type Basis = "ordered" | "invoiced" | "lost" | "pending";
const BASES: { value: Basis; label: string }[] = [
  { value: "ordered", label: "Ordered" },
  { value: "invoiced", label: "Invoiced" },
  { value: "lost", label: "Lost" },
  { value: "pending", label: "Unprocessed" },
];

const METRICS: { key: MetricKey; label: string }[] = [
  { key: "orders", label: "Orders" },
  { key: "qty", label: "Qty" },
  { key: "litres", label: "Litres" },
  { key: "excl", label: "Amount (excl)" },
];

function metricValue(m: OrderIntakeMetrics, key: MetricKey, basis: Basis): number {
  if (key === "orders") return m.orders;
  return m[`${basis}_${key}`];
}

const EMPTY: OrderIntakeMetrics = {
  orders: 0, ordered_qty: 0, ordered_litres: 0, ordered_excl: 0,
  invoiced_qty: 0, invoiced_litres: 0, invoiced_excl: 0,
  lost_qty: 0, lost_litres: 0, lost_excl: 0,
  pending_qty: 0, pending_litres: 0, pending_excl: 0,
};

function addInto(acc: OrderIntakeMetrics, r: OrderIntakeMetrics) {
  for (const k of Object.keys(EMPTY) as (keyof OrderIntakeMetrics)[]) acc[k] += r[k];
}

const int = new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-ZA", { notation: "compact", maximumFractionDigits: 1 });
function fmtMetric(v: number, key: MetricKey, short = false): string {
  const n = short && Math.abs(v) >= 10000 ? compact.format(v) : int.format(v);
  return key === "excl" ? `R ${n}` : n;
}
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;
/** "2026-11-03" -> "Mon 03 Nov" (or "Monday 03 November 2026" when long). */
function fmtDay(iso: string, long = false): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-ZA", long
    ? { weekday: "long", day: "2-digit", month: "long", year: "numeric" }
    : { weekday: "short", day: "2-digit", month: "short" });
}

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
  const [dcFilter, setDcFilter] = useState("");
  const [routeFilter, setRouteFilter] = useState("");
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
  const dcs = useMemo(() => distinct(rows.map((r) => r.dist_centre)), [rows]);
  // Routes narrow to the picked DC.
  const routes = useMemo(
    () => distinct(rows.filter((r) => !dcFilter || r.dist_centre === dcFilter).map((r) => r.route)),
    [rows, dcFilter],
  );
  const corpGroups = useMemo(() => distinct(rows.map((r) => r.corp_group)), [rows]);
  const users = useMemo(
    () => Array.from(new Set(rows.map((r) => r.user))).sort((a, b) => a.localeCompare(b)),
    [rows],
  );
  const filtered = useMemo(
    () =>
      rows.filter(
        (r) =>
          (sourceFilter === "ALL" || r.source === sourceFilter) &&
          (!dcFilter || r.dist_centre === dcFilter) &&
          (!routeFilter || r.route === routeFilter) &&
          (!corpFilter || r.corp_group === corpFilter) &&
          (!userFilter || r.user === userFilter),
      ),
    [rows, sourceFilter, dcFilter, routeFilter, corpFilter, userFilter],
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
        {/* Frozen pane: back link, dates, filters, toggles and KPI tiles stay
            on screen while the chart and table scroll underneath. */}
        <div className="sticky -top-2 z-20 -mx-2 -mt-2 px-2 pt-2 pb-2 bg-gray-50 border-b border-gray-200 shadow-sm flex flex-col gap-2">
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

        <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2">
        {/* Left column (both rows): stacked dates + Refresh. Right column:
            toggles on top, the three KPI cards underneath. */}
        <div className="row-span-2 flex flex-col gap-1.5">
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
              className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-white border rounded-md shadow-sm hover:bg-gray-50 disabled:opacity-60"
              title="Reload the figures"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
            <button
              onClick={handleExport}
              disabled={exporting}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-emerald-600 text-white rounded-md shadow-sm hover:bg-emerald-700 disabled:opacity-60"
              title="Download every order in this range as .xlsx"
            >
              <Download className="w-3.5 h-3.5" />
              {exporting ? "Exporting…" : "Export"}
            </button>
          </div>

        {/* Filters + metric controls: top row */}
        <div className="flex items-center gap-3 flex-wrap bg-white border rounded p-2 shadow-sm text-xs">
          <Segmented
            options={[
              { value: "ALL", label: "All sources" },
              ...SOURCES.map((s) => ({ value: s, label: s })),
            ]}
            value={sourceFilter}
            onChange={(v) => setSourceFilter(v as OrderIntakeSource | "ALL")}
          />
          <FilterSelect
            allLabel="All DCs"
            value={dcFilter}
            options={dcs}
            onChange={(v) => {
              setDcFilter(v);
              // Keep the route only if it belongs to the new DC.
              if (v && routeFilter && !rows.some((r) => r.dist_centre === v && r.route === routeFilter)) {
                setRouteFilter("");
              }
            }}
            width="10rem"
          />
          <FilterSelect allLabel="All routes" value={routeFilter} options={routes} onChange={setRouteFilter} width="11rem" />
          <FilterSelect
            allLabel="All corporate groups"
            value={corpFilter}
            options={corpGroups}
            onChange={setCorpFilter}
            width="13rem"
          />
          <FilterSelect allLabel="All agents" value={userFilter} options={users} onChange={setUserFilter} width="12rem" />
          {(sourceFilter !== "ALL" || dcFilter || routeFilter || corpFilter || userFilter) && (
            <button
              type="button"
              onClick={() => {
                setSourceFilter("ALL");
                setDcFilter("");
                setRouteFilter("");
                setCorpFilter("");
                setUserFilter("");
              }}
              className="inline-flex items-center gap-1 text-blue-700 hover:text-blue-900 hover:underline"
              title="Show all sources, DCs, routes, corporate groups and agents"
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
              options={BASES}
              value={basis}
              onChange={(v) => setBasis(v as Basis)}
              disabled={metric === "orders"}
            />
          </div>
        </div>

        {/* Right column, row 2: the three KPI cards */}
        <div className="contents">
          <div className="flex-1 min-w-0 grid grid-cols-3 gap-2">
            <KpiTile title="Total" metrics={total} total={total} basis={basis} />
            {SOURCES.map((s) => (
              <KpiTile
                key={s}
                title={s}
                color={SOURCE_COLOR[s]}
                metrics={bySource.get(s)!}
                total={total}
                muted={!visibleSources.includes(s)}
                basis={basis}
              />
            ))}
          </div>
        </div>
        </div>

        {error && (
          <div className="p-1.5 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs">
            <XCircle className="w-3.5 h-3.5 mr-1 flex-shrink-0" />
            {error}
          </div>
        )}
        </div>

        {/* Hourly chart */}
        <section className="bg-white border rounded shadow-sm p-3">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-gray-900">
              {METRICS.find((m) => m.key === metric)!.label}
              {metric !== "orders" && ` ${BASES.find((b) => b.value === basis)!.label.toLowerCase()}`} per hour
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

        {/* Agent x hour table — also the accessible table view of the chart */}
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
            dist_centre: dcFilter || null,
            route: routeFilter || null,
            user: drill.user ?? (userFilter || null),
          }}
          pendingOnlyAtStart={basis === "pending"}
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
  options, value, onChange, disabled, small,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  small?: boolean;
}) {
  return (
    <div className={`inline-flex rounded border overflow-hidden ${disabled ? "opacity-50" : ""}`}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          onClick={() => onChange(o.value)}
          className={`${small ? "px-2 py-0.5" : "px-2.5 py-1"} border-l first:border-l-0 ${
            value === o.value ? "bg-gray-800 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

const distinct = (vals: string[]) => Array.from(new Set(vals)).sort((a, b) => a.localeCompare(b));

type FilterOpt = { value: string; label: string };

/** Compact react-select, 26px tall to sit in the filter row. Turns blue
 *  while a value is picked, like the active filter style elsewhere. */
function filterStyles(active: boolean): StylesConfig<FilterOpt, false> {
  return {
    control: (b, st) => ({
      ...b,
      minHeight: 26,
      height: 26,
      fontSize: 12,
      borderRadius: 4,
      borderColor: active || st.isFocused ? "#3b82f6" : "#e5e7eb",
      backgroundColor: active ? "#eff6ff" : "#ffffff",
      boxShadow: "none",
      "&:hover": { borderColor: "#3b82f6" },
    }),
    valueContainer: (b) => ({ ...b, padding: "0 6px" }),
    input: (b) => ({ ...b, margin: 0, padding: 0 }),
    placeholder: (b) => ({ ...b, color: "#374151" }),
    singleValue: (b) => ({ ...b, color: active ? "#1e40af" : b.color, fontWeight: active ? 600 : 400 }),
    indicatorsContainer: (b) => ({ ...b, height: 24 }),
    indicatorSeparator: () => ({ display: "none" }),
    dropdownIndicator: (b) => ({ ...b, padding: 3 }),
    clearIndicator: (b) => ({ ...b, padding: 3 }),
    menu: (b) => ({ ...b, fontSize: 12, minWidth: "100%", width: "max-content", maxWidth: "24rem" }),
    menuPortal: (b) => ({ ...b, zIndex: 60 }),
    option: (b) => ({ ...b, padding: "4px 8px" }),
  };
}

/** Searchable single-pick filter; "" = all. */
function FilterSelect({
  value, options, onChange, allLabel, width,
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
  allLabel: string;
  width: string;
}) {
  const opts = useMemo(() => options.map((o) => ({ value: o, label: o })), [options]);
  return (
    <div style={{ width }}>
      <Select<FilterOpt, false>
        options={opts}
        value={value ? { value, label: value } : null}
        onChange={(o) => onChange(o?.value ?? "")}
        placeholder={allLabel}
        aria-label={allLabel}
        isClearable
        isSearchable
        menuPortalTarget={document.body}
        styles={filterStyles(!!value)}
      />
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
  title, color, metrics, total, muted, basis,
}: {
  title: string;
  basis: Basis;
  color?: string;
  metrics: OrderIntakeMetrics;
  total: OrderIntakeMetrics;
  muted?: boolean;
}) {
  const share = total.orders > 0 && color ? Math.round((metrics.orders / total.orders) * 100) : null;
  return (
    <div className={`bg-white border rounded shadow-sm px-3 py-2 ${muted ? "opacity-50" : ""}`}>
      <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
        {color && <Swatch color={color} />}
        {title}
        {share !== null && <span className="ml-auto font-normal text-gray-500">{share}% of orders</span>}
      </div>
      <div className="mt-0.5 text-sm font-medium text-gray-700 tabular-nums">
        {int.format(metrics.orders)} <span className="font-normal text-gray-500">orders</span>
      </div>
      <dl className="mt-1.5 grid grid-cols-3 gap-x-3 text-sm tabular-nums">
        <Stat label="Qty" ordered={metrics.ordered_qty} invoiced={metrics.invoiced_qty} lost={metrics.lost_qty} pending={metrics.pending_qty} k="qty" basis={basis} />
        <Stat label="Litres" ordered={metrics.ordered_litres} invoiced={metrics.invoiced_litres} lost={metrics.lost_litres} pending={metrics.pending_litres} k="litres" basis={basis} />
        <Stat label="Amount (excl)" ordered={metrics.ordered_excl} invoiced={metrics.invoiced_excl} lost={metrics.lost_excl} pending={metrics.pending_excl} k="excl" basis={basis} />
      </dl>
    </div>
  );
}

function Stat({
  label, ordered, invoiced, lost, pending, k, basis,
}: {
  label: string; ordered: number; invoiced: number; lost: number; pending: number; k: MetricKey; basis: Basis;
}) {
  // The figure picked on the Ordered / Invoiced / Lost toggle is the big
  // bold one; the others sit underneath as small labelled lines.
  const lines: { key: Basis; tag: string; v: number; title: string }[] = [
    { key: "ordered", tag: "Ord", v: ordered, title: "Ordered" },
    { key: "invoiced", tag: "Inv", v: invoiced, title: "Invoiced" },
    { key: "lost", tag: "Lost", v: lost, title: "Lost: ordered but not invoiced on processed (archived) orders" },
    { key: "pending", tag: "Unpr", v: pending,
      title: "Unprocessed: on orders not yet processed in Evolution, so not invoiced yet (ordered = inv + lost + unprocessed)" },
  ];
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      {lines.map((l) =>
        l.key === basis ? (
          <dd key={l.key} className="text-xl font-semibold text-gray-900 leading-tight whitespace-nowrap" title={l.title}>
            <span className="mr-1 text-xs font-semibold text-gray-600">{l.tag}</span>
            {fmtMetric(l.v, k)}
          </dd>
        ) : null,
      )}
      {lines.map((l) =>
        l.key !== basis ? (
          <dd key={l.key} className="text-gray-600 whitespace-nowrap" title={l.title}>
            <span className="font-medium text-gray-700">{l.tag}</span> {fmtMetric(l.v, k)}
          </dd>
        ) : null,
      )}
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
// Agent x hour table
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
        By agent and hour <span className="font-normal text-gray-500">· click a number to see the orders</span>
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-gray-500 border-b">
              <th className="text-left font-medium px-3 py-1.5 sticky left-0 bg-white">Agent</th>
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

/** Drill-modal tree levels, outermost first. The last level (customer)
 *  opens onto the orders themselves. */
const LEVELS: { tag: string; key: (o: OrderIntakeOrder) => string; label: (o: OrderIntakeOrder) => string }[] = [
  { tag: "DC", key: (o) => o.dist_centre, label: (o) => o.dist_centre },
  { tag: "Route", key: (o) => o.route, label: (o) => o.route },
  { tag: "Group", key: (o) => o.corp_group, label: (o) => o.corp_group },
  { tag: "Customer", key: (o) => o.customer_account, label: (o) => `${o.customer_account} · ${o.customer_name}` },
];
/** Row look per level: darker = higher up the tree. */
const LEVEL_ROW = [
  "bg-slate-200 hover:bg-slate-300 font-semibold text-slate-900 border-slate-300",
  "bg-slate-100 hover:bg-slate-200 font-semibold text-slate-800 border-slate-200",
  "bg-slate-50 hover:bg-slate-100 font-medium text-slate-800",
  "bg-white hover:bg-gray-50 font-medium text-gray-800",
];

type TreeNode = { id: string; label: string; depth: number; orders: OrderIntakeOrder[]; children: TreeNode[] };

function buildTree(list: OrderIntakeOrder[], depth: number, parent: string): TreeNode[] {
  if (depth >= LEVELS.length) return [];
  const groups = new Map<string, OrderIntakeOrder[]>();
  for (const o of list) {
    const k = LEVELS[depth].key(o);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(o);
  }
  const litres = (l: OrderIntakeOrder[]) => l.reduce((a, o) => a + o.ordered_litres, 0);
  return Array.from(groups, ([k, orders]) => {
    const id = `${parent}/${k}`;
    return { id, label: LEVELS[depth].label(orders[0]), depth, orders, children: buildTree(orders, depth + 1, id) };
  }).sort((x, y) => litres(y.orders) - litres(x.orders));
}

type TotalKey =
  | "ordered_qty" | "ordered_litres" | "ordered_excl"
  | "invoiced_litres" | "invoiced_excl" | "lost_litres" | "lost_excl";

/** The 7 metric cells of a drill-modal row, summed over a list of orders. */
function Totals({ list, bold }: { list: OrderIntakeOrder[]; bold?: boolean }) {
  const t = (k: TotalKey) => list.reduce((a, o) => a + o[k], 0);
  const cls = `text-right px-2 py-1.5 ${bold ? "font-semibold text-gray-900" : "text-gray-800"}`;
  return (
    <>
      <td className={cls}>{int.format(t("ordered_qty"))}</td>
      <td className={cls}>{int.format(t("ordered_litres"))}</td>
      <td className={cls}>{fmtMetric(t("ordered_excl"), "excl")}</td>
      <td className={cls}>{int.format(t("invoiced_litres"))}</td>
      <td className={cls}>{fmtMetric(t("invoiced_excl"), "excl")}</td>
      <td className={cls}>{int.format(t("lost_litres"))}</td>
      <td className={`${cls} pr-3`}>{fmtMetric(t("lost_excl"), "excl")}</td>
    </>
  );
}

function DrillModal({
  from, to, filter, pendingOnlyAtStart, onClose, onOpenOrder,
}: {
  from: string;
  to: string;
  filter: OrderIntakeDrillFilter;
  /** Opened while the Unprocessed toggle is on: start on unprocessed orders only. */
  pendingOnlyAtStart?: boolean;
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
  }, [from, to, filter.hour, filter.source, filter.user, filter.corp_group, filter.dist_centre, filter.route]);

  const title = [
    filter.hour != null ? hh(filter.hour) : null,
    filter.user,
    filter.source,
    filter.dist_centre,
    filter.route,
    filter.corp_group,
  ].filter(Boolean).join(" · ") || "All orders";

  // Clicking a source card narrows the list to that source; click it again
  // (or "Show all") to clear. The cards themselves always show both.
  const [sourcePick, setSourcePick] = useState<OrderIntakeSource | null>(null);
  // Day chips (only when the orders span more than one day) narrow the list
  // to one capture date; the source cards then show that day's split.
  const [dayPick, setDayPick] = useState<string | null>(null);
  // Unprocessed only = orders still unprocessed in Evolution (not invoiced yet).
  const [pendingOnly, setPendingOnly] = useState(!!pendingOnlyAtStart);
  const pendingCount = (orders ?? []).filter((o) => o.doc_state === 1).length;
  const dayOf = (o: OrderIntakeOrder) => o.created_at.slice(0, 10);
  const days = Array.from(
    (orders ?? []).reduce((m, o) => m.set(dayOf(o), (m.get(dayOf(o)) ?? 0) + 1), new Map<string, number>()),
  ).sort(([a], [b]) => a.localeCompare(b));
  const multiDay = days.length > 1;
  const dayOrders = (orders ?? []).filter((o) => !dayPick || dayOf(o) === dayPick);
  const shown = useMemo(
    () =>
      (orders ?? [])
        .filter(
          (o) =>
            (!dayPick || dayOf(o) === dayPick) &&
            (!sourcePick || o.source === sourcePick) &&
            (!pendingOnly || o.doc_state === 1),
        )
        .sort((x, y) => x.created_at.localeCompare(y.created_at)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [orders, dayPick, sourcePick, pendingOnly],
  );

  // Hierarchy: DC -> route -> corporate group -> customer -> orders. Every
  // level is ordered by litres ordered (biggest first), orders by capture time.
  const tree = useMemo(() => buildTree(shown, 0, ""), [shown]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  const expandAll = () => {
    const ids: string[] = [];
    const walk = (ns: TreeNode[]) =>
      ns.forEach((n) => {
        ids.push(n.id);
        walk(n.children);
      });
    walk(tree);
    setOpen(new Set(ids));
  };
  // Every level starts collapsed — the user opens what they want to see.

  const renderNode = (n: TreeNode): React.ReactNode => {
    const isOpen = open.has(n.id);
    const customers = new Set(n.orders.map((o) => o.customer_account)).size;
    const isCustomer = n.depth === LEVELS.length - 1;
    return (
      <Fragment key={n.id}>
        <tr className={`border-b cursor-pointer ${LEVEL_ROW[n.depth]}`} onClick={() => toggle(n.id)}>
          <td
            className="py-1.5 pr-2 truncate max-w-[26rem]"
            style={{ paddingLeft: 12 + n.depth * 18 }}
            title={n.label}
          >
            <span className="inline-flex items-center gap-1">
              {isOpen ? (
                <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />
              )}
              {!isCustomer && (
                <span className="text-[10px] uppercase tracking-wide font-normal text-slate-500 mr-0.5">
                  {LEVELS[n.depth].tag}
                </span>
              )}
              {n.label}
            </span>
          </td>
          <td className="px-2 py-1.5 text-slate-600 font-normal" colSpan={3}>
            {n.orders.length} {n.orders.length === 1 ? "order" : "orders"}
            {!isCustomer && ` · ${customers} ${customers === 1 ? "customer" : "customers"}`}
          </td>
          <Totals list={n.orders} bold={n.depth < 2} />
        </tr>
        {isOpen &&
          (isCustomer
            ? n.orders.map((o) => (
                <tr key={o.order_no} className="border-b bg-white hover:bg-blue-50/40">
                  <td className="py-1.5 pr-2" style={{ paddingLeft: 12 + LEVELS.length * 18 + 4 }}>
                    <button className="text-blue-700 hover:underline" onClick={() => setViewing(o.order_no)}>
                      {o.order_no}
                    </button>
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {multiDay ? o.created_at.replace("T", " ").slice(0, 16) : o.created_at.slice(11, 16)}
                  </td>
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
                  <td className="text-right px-2 py-1.5">{fmtMetric(o.invoiced_excl, "excl")}</td>
                  {o.doc_state === 1 ? (
                    <td
                      colSpan={2}
                      className="text-right px-3 py-1.5 text-gray-400"
                      title="Unprocessed in Evolution, so nothing is lost yet"
                    >
                      not invoiced yet
                    </td>
                  ) : (
                    <>
                      <td className="text-right px-2 py-1.5">{int.format(o.lost_litres)}</td>
                      <td className="text-right px-3 py-1.5">{fmtMetric(o.lost_excl, "excl")}</td>
                    </>
                  )}
                </tr>
              ))
            : n.children.map(renderNode))}
      </Fragment>
    );
  };

  // Same SO Assistant / Evolution split the bar's hover tooltip shows.
  const bySource = SOURCES.map((s) => {
    const list = dayOrders.filter((o) => o.source === s);
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
        className="bg-white rounded shadow-lg max-w-[90rem] w-full max-h-[85vh] flex flex-col overflow-hidden"
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
        {orders && multiDay && (
          <div className="px-4 py-2 border-b flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-gray-500 mr-1">Day:</span>
            {[["", orders.length] as [string, number], ...days].map(([d, n]) => {
              const active = (dayPick ?? "") === d;
              return (
                <button
                  key={d || "all"}
                  type="button"
                  onClick={() => setDayPick(d || null)}
                  aria-pressed={active}
                  className={`px-2 py-0.5 rounded-full border tabular-nums ${
                    active
                      ? "bg-gray-800 text-white border-gray-800"
                      : "bg-white text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  {d ? fmtDay(d) : "All days"} <span className={active ? "text-gray-300" : "text-gray-400"}>{n}</span>
                </button>
              );
            })}
          </div>
        )}
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
                    {Math.round((b.orders / Math.max(1, dayOrders.length)) * 100)}% of orders
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
        {orders && orders.length > 0 && (
          <div className="px-4 py-1.5 border-b flex items-center gap-3 text-xs">
            <Segmented
              small
              options={[
                { value: "all", label: "All orders" },
                { value: "pending", label: `Unprocessed only (${pendingCount})` },
              ]}
              value={pendingOnly ? "pending" : "all"}
              onChange={(v) => setPendingOnly(v === "pending")}
            />
            <button type="button" className="text-blue-700 hover:underline" onClick={expandAll}>
              Expand all
            </button>
            <button type="button" className="text-blue-700 hover:underline" onClick={() => setOpen(new Set())}>
              Collapse all
            </button>
          </div>
        )}
        <div className="overflow-auto">
          {error && <div className="p-3 text-xs text-red-700">{error}</div>}
          {!orders && !error && <div className="p-6 text-center text-sm text-gray-400">Loading…</div>}
          {orders && (
            <table className="w-full text-xs tabular-nums">
              <thead className="sticky top-0 bg-white z-10">
                <tr className="text-gray-500 border-b">
                  <th className="text-left font-medium px-3 py-1.5">DC / route / group / customer / order</th>
                  <th className="text-left font-medium px-2 py-1.5">Captured</th>
                  <th className="text-left font-medium px-2 py-1.5">Agent</th>
                  <th className="text-left font-medium px-2 py-1.5">Source</th>
                  <th className="text-right font-medium px-2 py-1.5">Qty</th>
                  <th className="text-right font-medium px-2 py-1.5">Litres</th>
                  <th className="text-right font-medium px-2 py-1.5">Excl</th>
                  <th className="text-right font-medium px-2 py-1.5">Inv litres</th>
                  <th className="text-right font-medium px-2 py-1.5">Inv excl</th>
                  <th className="text-right font-medium px-2 py-1.5">Lost litres</th>
                  <th className="text-right font-medium px-3 py-1.5">Lost excl</th>
                </tr>
              </thead>
              <tbody>
                {tree.map(renderNode)}
                {shown.length === 0 && (
                  <tr><td colSpan={11} className="p-6 text-center text-gray-400">
                    {orders.length === 0
                      ? "No orders."
                      : pendingOnly
                      ? "No unprocessed orders here."
                      : "No orders for this day / source."}
                  </td></tr>
                )}
              </tbody>
              {shown.length > 0 && (
                <tfoot className="sticky bottom-0">
                  <tr className="border-t bg-gray-50 font-semibold">
                    <td className="px-3 py-1.5" colSpan={4}>
                      {shown.length} orders{sourcePick && ` · ${sourcePick} only`}
                      {dayPick && ` · ${fmtDay(dayPick)}`}
                      {pendingOnly && " · unprocessed only"}
                    </td>
                    <Totals list={shown} bold />
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

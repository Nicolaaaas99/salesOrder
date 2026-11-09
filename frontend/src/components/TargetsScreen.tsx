import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronLeft, ChevronRight, Flag, History, Loader2, Lock, Pencil, Plus, Target, X,
} from "lucide-react";
import {
  forecastingApi,
  type AnchorLevel,
  type Measure,
  type Scenario,
  type ScopeOption,
  type WorkbenchLevel,
  type WorkbenchResponse,
  type WorkbenchRow,
} from "../api/forecasting";
import { useAuth } from "../context/AuthContext";
import { extractErrorMsg } from "../utils/format";
import { DateInput } from "./DateInput";
import { ScenarioModal } from "./ScenarioModal";
import { OverrideDialog } from "./forecasting/OverrideDialog";
import { SetTargetPanel, type Proposal, type TargetNode } from "./forecasting/SetTargetPanel";
import {
  ErrorBanner, ProgressBar, ScreenFrame, Segmented, StatusChip, SuccessNotice, addDaysIso, fmtDate,
  fmtPct, fmtQty, fmtWeekLabel, leverText, scopeText, weekStartIso,
} from "./forecasting/ui";

/** Analytics → Targets (SUPERVISOR+). One place for forecasts and targets:
 *  drill Company → DC → Route → Customer → Category → Product for one week,
 *  set a target on any row (or the node you're in), preview the split in a
 *  "Proposed" column, review warnings by group, approve. Every scenario that
 *  touches the current view is in the Changes drawer. Replaces the old
 *  Target Workbench + Scenarios screens. */

const ALL_LEVELS: WorkbenchLevel[] = ["dist_centre", "route", "customer", "category", "sku"];
const LEVEL_LABEL: Record<WorkbenchLevel, string> = {
  dist_centre: "Distribution centre", route: "Route", customer: "Customer", category: "Category", sku: "Product",
};

type Crumb = { level: WorkbenchLevel; key: string; label: string };

const normKey = (k: string) => (k || "").trim().toUpperCase();

export function TargetsScreen({ onBack }: { onBack?: () => void }) {
  const { sessionDate } = useAuth();
  const [week, setWeek] = useState(() => weekStartIso(sessionDate ?? new Date().toISOString()));
  const [measure, setMeasure] = useState<Measure>("units");
  const [path, setPath] = useState<Crumb[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [categories, setCategories] = useState<ScopeOption[]>([]);
  const [data, setData] = useState<WorkbenchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string } | null>(null);
  const [flashKeys, setFlashKeys] = useState<Set<string>>(new Set());
  const [panelNode, setPanelNode] = useState<TargetNode | null>(null);
  const [proposal, setProposal] = useState<Proposal>(null);
  const [overriding, setOverriding] = useState<WorkbenchRow | null>(null);
  const [changesOpen, setChangesOpen] = useState(false);
  const clearNotice = useCallback(() => setNotice(null), []);

  // With a category filter the Category level is skipped in the drill.
  const levels = useMemo(() => ALL_LEVELS.filter((l) => !(category && l === "category")), [category]);
  const level = levels[path.length];
  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    path.forEach((c) => (f[c.level] = c.key));
    if (category) f.category = category;
    return f;
  }, [path, category]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    forecastingApi
      .workbench({ week, level, measure, ...filters })
      .then(setData)
      .catch((e) => setError(extractErrorMsg(e, "Failed to load targets.")))
      .finally(() => setLoading(false));
  }, [week, level, measure, filters]);

  useEffect(load, [load]);
  useEffect(() => {
    forecastingApi.scopeOptions("category", undefined, 200).then(setCategories).catch(() => setCategories([]));
  }, []);

  // Anything that changes what the table shows invalidates an open preview.
  const closePanelOnNavigate = () => {
    if (panelNode) {
      setPanelNode(null);
      setProposal(null);
    }
  };
  const navigate = (fn: () => void) => {
    closePanelOnNavigate();
    fn();
  };

  const drill = (row: WorkbenchRow) => {
    if (level === "sku" || panelNode) return;
    setPath((p) => [...p, { level, key: row.key, label: row.label || row.key }]);
  };

  const nodeLevel = (): AnchorLevel => (path.length ? path[path.length - 1].level : category ? "category" : "company");
  const nodeLabel = () => (path.length ? path[path.length - 1].label : category ? `${category} (company)` : "Company");

  function openPanelForNode() {
    if (!data) return;
    setPanelNode({
      level: nodeLevel(), path, categoryFilter: category, label: nodeLabel(),
      forecast: data.total.forecast, target: data.total.target,
    });
  }

  function openPanelForRow(r: WorkbenchRow) {
    if (level === "sku" && r.target_id != null && r.source !== "FORECAST") {
      setOverriding(r);          // an existing target row: set it directly
      return;
    }
    setPanelNode({
      level, path: [...path, { level, key: r.key, label: r.label || r.key }], categoryFilter: category,
      label: r.label || r.key, forecast: r.forecast, target: r.target,
    });
  }

  function flash(keys: string[]) {
    setFlashKeys(new Set(keys));
    window.setTimeout(() => setFlashKeys(new Set()), 2500);
  }

  const total = data?.total;
  const unit = measure === "litres" ? "L" : "units";
  const showProposed = proposal != null;
  const deltaFor = (r: WorkbenchRow) => proposal?.deltas[normKey(r.key)] ?? 0;
  const colCount = 9 + (showProposed ? 1 : 0);

  return (
    <ScreenFrame
      icon={<Target className="w-4 h-4 text-amber-600" />}
      title="Targets"
      onBack={onBack}
      maxWidth="1560px"
      toolbar={
        <>
          <div className="flex items-center gap-1 text-xs">
            <button className="p-1 rounded hover:bg-gray-100" title="Previous week"
                    onClick={() => navigate(() => setWeek((w) => addDaysIso(w, -7)))}>
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-gray-700 font-medium whitespace-nowrap min-w-[9.5rem] text-center">{fmtWeekLabel(week)}</span>
            <div className="w-36">
              <DateInput value={week} onChange={(v) => v && navigate(() => setWeek(weekStartIso(v)))} />
            </div>
            <button className="p-1 rounded hover:bg-gray-100" title="Next week"
                    onClick={() => navigate(() => setWeek((w) => addDaysIso(w, 7)))}>
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
          <select
            className="input text-xs !w-44"
            value={category ?? ""}
            title="Show one category at every level"
            onChange={(e) => navigate(() => {
              const v = e.target.value || null;
              setCategory(v);
              setPath((p) => p.filter((c) => c.level !== "category"));
            })}
          >
            <option value="">All categories</option>
            {categories.map((c) => <option key={c.value} value={c.value}>{c.label || c.value}</option>)}
          </select>
          <Segmented<Measure>
            value={measure}
            onChange={(m) => navigate(() => setMeasure(m))}
            options={[{ value: "units", label: "Units" }, { value: "litres", label: "Litres" }]}
          />
          <button className="btn btn-secondary text-xs flex items-center gap-1" onClick={() => setChangesOpen(true)}>
            <History className="w-3.5 h-3.5" /> Changes
          </button>
        </>
      }
    >
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <SuccessNotice notice={notice} onDone={clearNotice} />

      {/* Breadcrumb */}
      <div className="flex items-center gap-1 text-xs flex-wrap">
        <button className={path.length ? "text-blue-600 hover:underline" : "font-semibold text-gray-800"}
                onClick={() => navigate(() => setPath([]))}>Company</button>
        {path.map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="w-3 h-3 text-gray-400" />
            <button className={i === path.length - 1 ? "font-semibold text-gray-800" : "text-blue-600 hover:underline"}
                    onClick={() => navigate(() => setPath(path.slice(0, i + 1)))}>
              {c.label}
            </button>
          </span>
        ))}
        {category && (
          <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 text-amber-800 px-2 py-0.5">
            {category}
            <button onClick={() => navigate(() => setCategory(null))} title="Show all categories"><X className="w-3 h-3" /></button>
          </span>
        )}
      </div>

      {/* Totals for the node you're in */}
      {total && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2 flex-shrink-0">
          <Stat label={`Forecast (${unit})`} value={fmtQty(total.forecast)} />
          <Stat label={`Target (${unit})`} value={fmtQty(total.target)}
                sub={total.forecast > 0 ? `${fmtPct((total.target / total.forecast - 1) * 100, 1)} vs forecast` : undefined} />
          <Stat label={`Committed (${unit})`} value={fmtQty(total.committed)}
                sub={`${fmtQty(total.delivered)} delivered · ${fmtQty(total.on_order)} on order`} />
          <div className="bg-white rounded border shadow-sm p-2">
            <div className="text-[11px] text-gray-500 mb-1">Committed vs target</div>
            <ProgressBar value={total.committed} target={total.target} />
          </div>
          <Stat label="Customer-products" value={fmtQty(total.leaf_count)}
                sub={`${total.targeted_count} with a target · ${total.flagged_count} flagged`} />
          <button
            className="rounded border-2 border-dashed border-amber-300 bg-amber-50 hover:bg-amber-100 text-amber-900 p-2 text-left disabled:opacity-50"
            onClick={openPanelForNode}
            disabled={!!panelNode}
            title={`Set a target for ${nodeLabel()}`}
          >
            <div className="text-[11px] flex items-center gap-1"><Plus className="w-3 h-3" /> Set target for</div>
            <div className="text-sm font-semibold truncate">{nodeLabel()}</div>
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 flex gap-2">
        {/* Rows */}
        <div className="flex-1 min-w-0 bg-white rounded border shadow-sm overflow-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-600 border-b sticky top-0 z-10">
              <tr>
                <th className="text-left px-3 py-2 font-medium">{LEVEL_LABEL[level]}</th>
                <th className="text-right px-2 py-2 font-medium">Forecast</th>
                <th className="text-right px-2 py-2 font-medium">Target</th>
                <th className="text-right px-2 py-2 font-medium">vs fc</th>
                {showProposed && <th className="text-right px-2 py-2 font-medium bg-blue-50 text-blue-800">Proposed</th>}
                <th className="text-right px-2 py-2 font-medium">Delivered</th>
                <th className="text-right px-2 py-2 font-medium">On order</th>
                <th className="text-left px-2 py-2 font-medium">Committed / target</th>
                <th className="text-right px-2 py-2 font-medium" title="Customer-products · with a target · flagged">Items</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {loading && !data && (
                <tr><td colSpan={colCount} className="py-8 text-center text-gray-400"><Loader2 className="w-4 h-4 animate-spin inline" /></td></tr>
              )}
              {data && data.rows.length === 0 && (
                <tr><td colSpan={colCount} className="py-8 text-center text-gray-500">
                  No forecasts or targets for this week yet.
                </td></tr>
              )}
              {data?.rows.map((r) => {
                const d = deltaFor(r);
                const proposed = r.target + d;
                const clickable = level !== "sku" && !panelNode;
                return (
                  <tr key={r.key}
                      className={`border-b last:border-0 transition-colors duration-700 ${clickable ? "cursor-pointer hover:bg-blue-50" : ""} ${loading ? "opacity-60" : ""} ${flashKeys.has(normKey(r.key)) ? "bg-emerald-100" : ""}`}
                      onClick={() => clickable && drill(r)}>
                    <td className="px-3 py-1.5">
                      <div className="font-medium text-gray-900 truncate max-w-[20rem]">{r.label || r.key || "(none)"}</div>
                      {r.label && r.label !== r.key && <div className="text-[11px] text-gray-500 font-mono">{r.key}</div>}
                    </td>
                    <td className="px-2 py-1.5 text-right font-mono">{fmtQty(r.forecast)}</td>
                    <td className="px-2 py-1.5 text-right font-mono font-semibold">{fmtQty(r.target)}</td>
                    <td className={`px-2 py-1.5 text-right font-mono ${r.target > r.forecast + 1e-6 ? "text-blue-700" : r.target < r.forecast - 1e-6 ? "text-amber-700" : "text-gray-400"}`}>
                      {r.forecast > 0 ? fmtPct((r.target / r.forecast - 1) * 100) : "—"}
                    </td>
                    {showProposed && (
                      <td className="px-2 py-1.5 text-right font-mono bg-blue-50/60 whitespace-nowrap">
                        {Math.abs(d) < 0.005 ? <span className="text-gray-400">{fmtQty(r.target)}</span> : (
                          <>
                            <span className="font-semibold text-blue-900">{fmtQty(proposed)}</span>
                            <span className={`ml-1 text-[10px] rounded px-1 ${d > 0 ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
                              {d > 0 ? "+" : "−"}{fmtQty(Math.abs(d))}
                            </span>
                          </>
                        )}
                      </td>
                    )}
                    <td className="px-2 py-1.5 text-right font-mono">{fmtQty(r.delivered)}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{fmtQty(r.on_order)}</td>
                    <td className="px-2 py-1.5"><ProgressBar value={r.committed} target={r.target} /></td>
                    <td className="px-2 py-1.5 text-right whitespace-nowrap text-gray-600">
                      {level !== "sku" ? (
                        <>
                          {r.leaf_count}
                          {r.targeted_count > 0 && <span className="ml-1 text-blue-700">· 🎯{r.targeted_count}</span>}
                          {r.flagged_count > 0 && <span className="ml-1 text-amber-700">· <Flag className="w-3 h-3 inline" />{r.flagged_count}</span>}
                        </>
                      ) : (
                        <span className="inline-flex items-center gap-1">
                          {r.flagged_count > 0 && <Flag className="w-3 h-3 text-amber-600" />}
                          {r.is_locked && <Lock className="w-3 h-3 text-gray-500" />}
                          {r.override != null && <span className="text-purple-700">manual</span>}
                          {r.target_id == null || r.source === "FORECAST" ? <span className="text-gray-400">forecast</span> : null}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right" onClick={(e) => e.stopPropagation()}>
                      <button
                        className="inline-flex items-center gap-1 rounded border border-amber-300 text-amber-800 hover:bg-amber-50 px-1.5 py-0.5 disabled:opacity-40 whitespace-nowrap"
                        disabled={!!panelNode || (level === "sku" && measure === "litres" && r.target_id != null && r.source !== "FORECAST")}
                        title={level === "sku" && measure === "litres" && r.target_id != null ? "Switch to Units to change one product" : `Set a target for ${r.label || r.key}`}
                        onClick={() => openPanelForRow(r)}
                      >
                        {level === "sku" && r.target_id != null && r.source !== "FORECAST" ? <Pencil className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
                        Target
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {panelNode && (
          <SetTargetPanel
            key={`${panelNode.label}|${week}|${measure}`}
            node={panelNode}
            week={week}
            measure={measure}
            view={{ level, filters }}
            onProposal={setProposal}
            onApproved={(text) => {
              const keys = Object.keys(proposal?.deltas ?? {});
              setPanelNode(null);
              setProposal(null);
              setNotice({ text });
              flash(keys);
              load();
            }}
            onClose={() => {
              setPanelNode(null);
              setProposal(null);
            }}
          />
        )}
      </div>

      {overriding && (
        <OverrideDialog
          row={overriding}
          customerLabel={path.find((c) => c.level === "customer")?.label ?? ""}
          onClose={() => setOverriding(null)}
          onSaved={(text) => {
            const key = normKey(overriding.key);
            setOverriding(null);
            setNotice({ text });
            flash([key]);
            load();
          }}
        />
      )}

      {changesOpen && (
        <ChangesDrawer
          week={week}
          filters={filters}
          where={[...path.map((c) => c.label), category].filter(Boolean).join(" › ") || "the whole company"}
          defaultWeek={week}
          onClose={() => setChangesOpen(false)}
          onChanged={(msg) => {
            if (msg) setNotice({ text: msg });
            load();
          }}
        />
      )}
    </ScreenFrame>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white rounded border shadow-sm p-2">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-gray-900 font-mono leading-tight">{value}</div>
      {sub && <div className="text-[11px] text-gray-500 truncate">{sub}</div>}
    </div>
  );
}

// --- Changes drawer ------------------------------------------------------------

const SCOPE_FOR: Record<string, keyof Scenario["scope"]> = {
  dist_centre: "distCentre", route: "route", customer: "customer", category: "category", sku: "sku",
};

/** A scenario touches the view if its period covers the week and none of
 *  its scope dimensions rules out where you are. */
function touchesView(s: Scenario, week: string, filters: Record<string, string>): boolean {
  if (s.period_to < week || s.period_from > addDaysIso(week, 6)) return false;
  return Object.entries(filters).every(([lvl, v]) => {
    const vals = s.scope[SCOPE_FOR[lvl]] as string[];
    return !vals.length || vals.some((x) => x.trim().toUpperCase() === v.trim().toUpperCase());
  });
}

function ChangesDrawer({
  week,
  filters,
  where,
  defaultWeek,
  onClose,
  onChanged,
}: {
  week: string;
  filters: Record<string, string>;
  where: string;
  defaultWeek: string;
  onClose: () => void;
  onChanged: (message?: string) => void;
}) {
  const [all, setAll] = useState<Scenario[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<{ id: number; reason: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Scenario | "new" | null>(null);

  const reload = useCallback(() => {
    forecastingApi.listScenarios().then(setAll).catch((e) => { setAll([]); setError(extractErrorMsg(e, "Failed to load changes.")); });
  }, []);
  useEffect(reload, [reload]);

  const rows = (all ?? []).filter((s) =>
    (showAll ? s.status !== "CANCELLED" : s.status === "ACTIVE") && touchesView(s, week, filters));

  async function cancel() {
    if (!cancelling || !cancelling.reason.trim()) return;
    setBusy(true);
    try {
      const r = await forecastingApi.cancelScenario(cancelling.id, cancelling.reason.trim());
      setCancelling(null);
      reload();
      onChanged(`"${r.scenario.name}" removed. ${fmtQty(r.targets_recomputed)} targets recalculated, ${fmtQty(r.targets_deleted)} back to the forecast.`);
    } catch (e) {
      setError(extractErrorMsg(e, "Failed to remove the target."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 bg-black/30 flex justify-end" onClick={onClose}>
      <div className="w-[480px] max-w-full h-full bg-white shadow-xl flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="p-3 border-b flex items-start gap-2">
          <div className="flex-1">
            <div className="text-sm font-semibold text-gray-900">Targets affecting {where}</div>
            <div className="text-xs text-gray-500">{fmtWeekLabel(week)}</div>
          </div>
          <button className="p-1 rounded hover:bg-gray-100" onClick={onClose}><X className="w-4 h-4" /></button>
        </div>
        <div className="px-3 py-2 border-b flex items-center gap-2 text-xs">
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            Include drafts
          </label>
          <div className="flex-1" />
          <button className="btn btn-ghost text-xs" onClick={() => setOpen("new")}
                  title="For targets by sales rep, telesales, or several customers at once">
            Advanced target…
          </button>
        </div>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <div className="flex-1 overflow-auto p-3 space-y-2 text-xs">
          {all === null && <div className="text-center text-gray-400 py-6"><Loader2 className="w-4 h-4 animate-spin inline" /></div>}
          {all !== null && rows.length === 0 && (
            <div className="text-center text-gray-500 py-6">No targets set here for this week — it follows the forecast.</div>
          )}
          {rows.map((s) => (
            <div key={s.id} className="rounded border p-2 space-y-1">
              <div className="flex items-start gap-2">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-900">{s.name}</div>
                  <div className="text-gray-600">{scopeText(s)}</div>
                </div>
                <StatusChip status={s.status} />
              </div>
              <div className="text-gray-600 flex flex-wrap gap-x-3">
                <span className="font-mono">{leverText(s)}</span>
                <span>{fmtDate(s.period_from)} – {fmtDate(s.period_to)}</span>
                <span>by {s.approved_by ?? s.created_by}</span>
              </div>
              {cancelling?.id === s.id ? (
                <div className="flex gap-1 pt-1">
                  <input autoFocus className="input flex-1" placeholder="Why remove it?" value={cancelling.reason}
                         onChange={(e) => setCancelling({ id: s.id, reason: e.target.value })}
                         onKeyDown={(e) => e.key === "Enter" && cancel()} />
                  <button className="btn btn-primary text-xs" disabled={busy || !cancelling.reason.trim()} onClick={cancel}>
                    {busy ? "Removing…" : "Remove"}
                  </button>
                  <button className="btn btn-ghost text-xs" onClick={() => setCancelling(null)}>Keep</button>
                </div>
              ) : (
                <div className="flex gap-2 pt-1">
                  <button className="text-blue-600 hover:underline" onClick={() => setOpen(s)}>Details</button>
                  <button className="text-red-600 hover:underline" onClick={() => setCancelling({ id: s.id, reason: "" })}>
                    Remove target
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {open && (
        <div onClick={(e) => e.stopPropagation()}>
          <ScenarioModal
            scenario={open === "new" ? null : open}
            defaultWeek={defaultWeek}
            onClose={() => setOpen(null)}
            onChanged={() => { reload(); onChanged(); }}
          />
        </div>
      )}
    </div>
  );
}

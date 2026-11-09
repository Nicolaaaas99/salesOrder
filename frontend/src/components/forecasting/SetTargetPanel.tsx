import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import {
  FLAG_GROUPS,
  FLAG_LABELS,
  emptyScope,
  forecastingApi,
  type AnchorLevel,
  type FlaggedLeaf,
  type FlagReview,
  type Measure,
  type ReviewDecision,
  type Scenario,
  type ScenarioCascade,
  type ScenarioInput,
  type WorkbenchLevel,
} from "../../api/forecasting";
import { extractErrorMsg } from "../../utils/format";
import { ErrorBanner, addDaysIso, fmtPct, fmtQty, fmtWeekLabel } from "./ui";

/** A node on the Targets screen: the drill path to it (plus an optional
 *  category filter), its label, and its current numbers in the screen's
 *  measure. */
export type TargetNode = {
  level: AnchorLevel;
  path: { level: WorkbenchLevel; key: string; label: string }[];
  categoryFilter: string | null;
  label: string;
  forecast: number;
  target: number;
};

/** What the table is showing right now -- the preview's "Proposed" column
 *  is rolled up to exactly these rows. */
export type TableView = { level: WorkbenchLevel; filters: Record<string, string> };

export type Proposal = { deltas: Record<string, number>; total: number } | null;

type Mode = "total" | "percent";
const MAX_WEEKS = 12;
const SCOPE_KEY: Record<WorkbenchLevel, keyof ReturnType<typeof emptyScope>> = {
  dist_centre: "distCentre", route: "route", customer: "customer", category: "category", sku: "sku",
};

export function SetTargetPanel({
  node,
  week,
  measure,
  view,
  onProposal,
  onApproved,
  onClose,
}: {
  node: TargetNode;
  week: string;
  measure: Measure;
  view: TableView;
  onProposal: (p: Proposal) => void;
  onApproved: (message: string) => void;
  onClose: () => void;
}) {
  const unit = measure === "litres" ? "L" : "units";
  const [mode, setMode] = useState<Mode>("total");
  const [value, setValue] = useState(String(Math.round(node.target)));
  const [weekCount, setWeekCount] = useState(1);
  const [name, setName] = useState("");
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [preview, setPreview] = useState<ScenarioCascade | null>(null);
  const [busy, setBusy] = useState<null | "preview" | "approve" | "discard">(null);
  const [error, setError] = useState<string | null>(null);
  const [groupDecisions, setGroupDecisions] = useState<Record<string, ReviewDecision>>({});
  const [leafDecisions, setLeafDecisions] = useState<Map<string, ReviewDecision>>(new Map());
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [showWarnings, setShowWarnings] = useState(false);

  const num = Number(value);
  const valid = value.trim() !== "" && isFinite(num) && (mode === "percent" ? num > -100 : num >= 0);
  const newTarget = !valid ? null : mode === "total" ? num : node.target * (1 + num / 100);
  const weekShort = fmtWeekLabel(week).split(" · ")[0];
  const autoName = `${node.label} ${mode === "percent" ? `${num > 0 ? "+" : ""}${num}%` : `= ${fmtQty(num)} ${unit}`}`
    + ` · ${weekShort}${weekCount > 1 ? ` +${weekCount - 1} wk` : ""}`;

  function input(): ScenarioInput {
    const scope = emptyScope();
    for (const c of node.path) (scope[SCOPE_KEY[c.level]] as string[]).push(c.key);
    if (node.categoryFilter && !scope.category.length) scope.category.push(node.categoryFilter);
    return {
      name: (name.trim() || autoName).slice(0, 100),
      description: null,
      scope,
      lever_type: mode === "percent" ? "PERCENT" : "FIXED_QTY",
      lever_value: num,
      period_from: week,
      period_to: addDaysIso(week, 7 * weekCount - 1),
      // A % change scales every product, litre size or not; a weekly
      // total follows the screen's measure.
      measure: mode === "total" && measure === "litres" ? "LITRES" : "UNITS",
      anchor_level: node.level,
    };
  }

  function invalidate() {
    if (preview) {
      setPreview(null);
      onProposal(null);
    }
  }

  async function runPreview() {
    if (!valid) return setError(mode === "percent" ? "Enter a percentage above -100." : "Enter a weekly total of 0 or more.");
    setBusy("preview");
    setError(null);
    try {
      const body = input();
      const sc = scenario ? await forecastingApi.updateScenario(scenario.id, body)
                          : await forecastingApi.createScenario(body);
      setScenario(sc);
      const p = await forecastingApi.previewScenario(sc.id, 0, {
        rollup_level: view.level, rollup_week: week, measure, ...view.filters,
      });
      setPreview(p);
      setGroupDecisions({});
      setLeafDecisions(new Map());
      onProposal({ deltas: p.rollup_deltas, total: p.rollup_total_delta });
    } catch (e) {
      setError(extractErrorMsg(e, "Couldn't work out the split."));
    } finally {
      setBusy(null);
    }
  }

  // --- warning groups ------------------------------------------------------
  const flagged = preview?.flagged_leaves ?? [];
  const groups = useMemo(() => {
    const m = new Map<string, FlaggedLeaf[]>();
    for (const f of flagged) for (const c of f.flag_codes) m.set(c, [...(m.get(c) ?? []), f]);
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [flagged]);
  const lk = (f: FlaggedLeaf) => `${f.customer_account}\u0000${f.stock_code}`;
  /** Same rule as the backend: own decision wins; any excluded group
   *  excludes; accepted only when every group is accepted. */
  const decisionFor = (f: FlaggedLeaf): ReviewDecision | null => {
    const own = leafDecisions.get(lk(f));
    if (own) return own;
    const ds = f.flag_codes.map((c) => groupDecisions[c]);
    if (ds.includes("EXCLUDE")) return "EXCLUDE";
    return ds.length && ds.every((d) => d === "ACCEPT") ? "ACCEPT" : null;
  };
  const undecided = flagged.filter((f) => !decisionFor(f)).length;
  const excludedCount = flagged.filter((f) => decisionFor(f) === "EXCLUDE").length;
  const conflicts = preview?.conflicts ?? [];

  async function approve() {
    if (!scenario || !preview) return;
    setBusy("approve");
    setError(null);
    try {
      const reviews: FlagReview[] = [...leafDecisions.entries()].map(([k, decision]) => {
        const [customer_account, stock_code] = k.split("\u0000");
        return { customer_account, stock_code, decision };
      });
      const r = await forecastingApi.approveScenario(scenario.id, reviews, groupDecisions);
      onProposal(null);
      onApproved(`Target set: ${r.scenario.name}. ${fmtQty(r.rows_written ?? 0)} customer-product targets updated`
        + (excludedCount ? `, ${excludedCount} left out.` : "."));
    } catch (e: any) {
      const d = e?.response?.data?.detail;
      setError(typeof d === "object" && d?.message ? d.message : extractErrorMsg(e, "Couldn't approve the target."));
    } finally {
      setBusy(null);
    }
  }

  async function discard() {
    if (scenario && scenario.status !== "ACTIVE") {
      setBusy("discard");
      try {
        await forecastingApi.cancelScenario(scenario.id, "Discarded on the Targets screen");
      } catch {
        /* a leftover draft is harmless */
      } finally {
        setBusy(null);
      }
    }
    onProposal(null);
    onClose();
  }

  const scopeDelta = preview?.rollup_scope_delta ?? 0;
  const outsideDelta = preview?.rollup_outside_delta ?? 0;
  const where = [...node.path.map((c) => c.label), node.categoryFilter].filter(Boolean).join(" › ") || "Whole company";

  return (
    <aside className="w-[390px] flex-shrink-0 bg-white rounded border shadow-sm flex flex-col min-h-0">
      <div className="flex items-start gap-2 p-3 border-b">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-gray-900">Set target</div>
          <div className="text-xs text-gray-600 truncate" title={where}>{where}</div>
        </div>
        <button className="p-1 rounded hover:bg-gray-100 text-gray-500" onClick={discard} title="Close (discards an unapproved target)">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-3 space-y-3 text-xs">
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded bg-gray-50 border p-2">
            <div className="text-gray-500">Forecast · {weekShort}</div>
            <div className="font-mono text-sm">{fmtQty(node.forecast)} {unit}</div>
          </div>
          <div className="rounded bg-gray-50 border p-2">
            <div className="text-gray-500">Current target</div>
            <div className="font-mono text-sm">{fmtQty(node.target)} {unit}</div>
          </div>
        </div>

        <div className="inline-flex rounded border border-gray-300 overflow-hidden">
          {(["total", "percent"] as Mode[]).map((m) => (
            <button key={m} type="button"
                    className={`px-2.5 py-1 ${m === "percent" ? "border-l border-gray-300" : ""} ${mode === m ? "bg-blue-600 text-white font-semibold" : "bg-white text-gray-700 hover:bg-gray-50"}`}
                    onClick={() => { setMode(m); setValue(m === "total" ? String(Math.round(node.target)) : "10"); invalidate(); }}>
              {m === "total" ? `Weekly total (${unit})` : "% change"}
            </button>
          ))}
        </div>

        <label className="block">
          <span className="text-gray-700">{mode === "total" ? `New weekly target in ${unit}` : "Change on the forecast, in %"}</span>
          <input type="number" step="any" className="input w-full mt-1 text-sm" value={value} autoFocus
                 onChange={(e) => { setValue(e.target.value); invalidate(); }}
                 onKeyDown={(e) => e.key === "Enter" && runPreview()} />
          {newTarget != null && (
            <span className="text-gray-500">
              {mode === "percent" ? `≈ ${fmtQty(newTarget)} ${unit} this week` : node.forecast > 0 ? `${fmtPct((newTarget / node.forecast - 1) * 100, 1)} on the forecast` : ""}
            </span>
          )}
        </label>

        <label className="block">
          <span className="text-gray-700">For how many weeks?</span>
          <select className="input w-full mt-1" value={weekCount}
                  onChange={(e) => { setWeekCount(Number(e.target.value)); invalidate(); }}>
            {Array.from({ length: MAX_WEEKS }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>
                {n === 1 ? `This week only (${weekShort})` : `${n} weeks, ${weekShort} – ${fmtWeekLabel(addDaysIso(week, 7 * (n - 1))).split(" · ")[0]}`}
              </option>
            ))}
          </select>
          {weekCount > 1 && mode === "total" && (
            <span className="text-gray-500">The same weekly total applies to each week.</span>
          )}
        </label>

        <label className="block">
          <span className="text-gray-700">Name <span className="text-gray-400">(optional)</span></span>
          <input className="input w-full mt-1" value={name} maxLength={100} placeholder={autoName}
                 onChange={(e) => setName(e.target.value)} />
        </label>

        <ErrorBanner message={error} onClose={() => setError(null)} />

        {busy === "preview" && (
          <div className="flex items-center gap-2 text-gray-600 bg-blue-50 border border-blue-100 rounded p-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            Working out the split… a whole DC can take up to half a minute.
          </div>
        )}

        {preview && busy !== "preview" && (
          <div className="space-y-3 border-t pt-3">
            <div className="rounded border border-blue-200 bg-blue-50 p-2 space-y-1">
              <div className="font-semibold text-blue-900">What changes ({weekShort})</div>
              <div>
                <span className="text-gray-700">{node.label}:</span>{" "}
                <span className="font-mono">{fmtQty(node.target)}</span> →{" "}
                <span className="font-mono font-semibold">{fmtQty(node.target + scopeDelta)}</span> {unit}
              </div>
              {Math.abs(outsideDelta) > 0.05 && (
                <div className="text-gray-700">
                  To keep an outer target's total, {fmtQty(preview.rebalanced_count)} other customer-product week(s)
                  {outsideDelta < 0 ? " give up " : " gain "}
                  <span className="font-mono">{fmtQty(Math.abs(outsideDelta))}</span> {unit}.
                </div>
              )}
              {preview.locked_skipped > 0 && (
                <div className="text-gray-600">{preview.locked_skipped} locked customer-product week(s) keep their target.</div>
              )}
            </div>

            {conflicts.length > 0 && (
              <div className="rounded border border-red-200 bg-red-50 p-2 text-red-800 space-y-1">
                <div className="font-semibold flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> This target can't be met</div>
                {conflicts.slice(0, 4).map((c, i) => <div key={i}>{c.message}</div>)}
                <div className="text-red-700">Lower the targets inside it, or raise this one.</div>
              </div>
            )}

            {groups.length > 0 && (
              <div className="space-y-2">
                <div className="font-semibold text-gray-800">
                  Check {fmtQty(flagged.length)} warning{flagged.length === 1 ? "" : "s"}
                  {undecided > 0 && <span className="ml-1 text-amber-700 font-normal">· {fmtQty(undecided)} still to decide</span>}
                </div>
                {groups.map(([code, leaves]) => {
                  const g = FLAG_GROUPS[code] ?? { title: FLAG_LABELS[code] ?? code, explain: "" };
                  const gd = groupDecisions[code];
                  const open = openGroup === code;
                  return (
                    <div key={code} className="rounded border">
                      <div className="p-2 flex items-start gap-2">
                        <button className="mt-0.5 text-gray-500" onClick={() => setOpenGroup(open ? null : code)}
                                title={open ? "Hide products" : "Show products"}>
                          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                        </button>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium text-gray-900">{g.title} <span className="text-gray-500 font-normal">· {fmtQty(leaves.length)}</span></div>
                          {g.explain && <div className="text-gray-500">{g.explain}</div>}
                        </div>
                        <DecisionToggle value={gd ?? null}
                                        onChange={(d) => setGroupDecisions((p) => ({ ...p, [code]: d }))} />
                      </div>
                      {open && (
                        <div className="border-t max-h-56 overflow-auto">
                          {leaves.slice(0, 300).map((f) => (
                            <div key={lk(f)} className="flex items-center gap-2 px-2 py-1 border-b last:border-0">
                              <div className="flex-1 min-w-0 truncate" title={f.messages.join("\n")}>
                                <span className="font-mono">{f.customer_account}</span> · <span className="font-mono">{f.stock_code}</span>
                                {f.flag_codes.length > 1 && <span className="text-gray-400"> · +{f.flag_codes.length - 1} more</span>}
                              </div>
                              <DecisionToggle small value={decisionFor(f)}
                                              onChange={(d) => setLeafDecisions((m) => new Map(m).set(lk(f), d))} />
                            </div>
                          ))}
                          {leaves.length > 300 && (
                            <div className="px-2 py-1 text-gray-500">Showing 300 of {fmtQty(leaves.length)} — use the group buttons for the rest.</div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
                <div className="text-gray-500">
                  <b>Accept</b> keeps the target. <b>Exclude</b> leaves those customer-products out of this target
                  (they keep their current target) and the rest share their part.
                </div>
              </div>
            )}

            {preview.warnings.length > 0 && (
              <div>
                <button className="text-gray-600 hover:text-gray-900 flex items-center gap-1" onClick={() => setShowWarnings((v) => !v)}>
                  {showWarnings ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                  {preview.warnings.length} note{preview.warnings.length === 1 ? "" : "s"}
                </button>
                {showWarnings && (
                  <ul className="mt-1 list-disc pl-5 text-gray-600 space-y-0.5">
                    {preview.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="p-3 border-t flex items-center gap-2">
        <button className="btn btn-ghost text-xs" onClick={discard} disabled={busy !== null}>
          {preview ? "Discard" : "Cancel"}
        </button>
        <div className="flex-1" />
        {!preview ? (
          <button className="btn btn-primary text-xs" onClick={runPreview} disabled={busy !== null || !valid}>
            {busy === "preview" ? "Working…" : "Preview"}
          </button>
        ) : (
          <button className="btn btn-primary text-xs" onClick={approve}
                  disabled={busy !== null || conflicts.length > 0 || undecided > 0}
                  title={conflicts.length ? "Fix the conflict first" : undecided ? `${undecided} warning(s) still need Accept or Exclude` : "Save this target"}>
            {busy === "approve" ? "Saving…" : "Approve target"}
          </button>
        )}
      </div>
    </aside>
  );
}

function DecisionToggle({
  value,
  onChange,
  small,
}: {
  value: ReviewDecision | null;
  onChange: (d: ReviewDecision) => void;
  small?: boolean;
}) {
  const pad = small ? "px-1.5 py-0" : "px-2 py-0.5";
  return (
    <div className="inline-flex rounded border border-gray-300 overflow-hidden flex-shrink-0">
      <button type="button" className={`${pad} ${value === "ACCEPT" ? "bg-emerald-600 text-white" : "bg-white text-gray-700 hover:bg-emerald-50"}`}
              onClick={() => onChange("ACCEPT")}>Accept</button>
      <button type="button" className={`${pad} border-l border-gray-300 ${value === "EXCLUDE" ? "bg-gray-700 text-white" : "bg-white text-gray-700 hover:bg-gray-100"}`}
              onClick={() => onChange("EXCLUDE")}>Exclude</button>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";
import {
  FLAG_LABELS,
  SCOPE_DIMS,
  emptyScope,
  forecastingApi,
  type FlagReview,
  type LeverType,
  type Scenario,
  type ScenarioCascade,
  type ScenarioScope,
  type ScopeDim,
  type ScopeOption,
  type UnreviewedError,
} from "../api/forecasting";
import { extractErrorMsg } from "../utils/format";
import { DateInput } from "./DateInput";
import { ErrorBanner, StatusChip, addDaysIso, fmtDate, fmtPct, fmtQty, weekStartIso } from "./forecasting/ui";

/** Create / edit / preview / review flags / approve / cancel one scenario.
 *
 *  Lifecycle: DRAFT → (preview) PREVIEW → (approve) ACTIVE. Editing a
 *  DRAFT/PREVIEW scenario sends it back to DRAFT. Every flagged
 *  customer × product must get an Accept or Exclude decision before
 *  approval; the backend re-checks this. */

const LEVER_OPTIONS: { value: LeverType; label: string; hint: string }[] = [
  { value: "PERCENT", label: "% change", hint: "Raise (or lower, with a minus) every product in scope by this percentage." },
  { value: "ABSOLUTE_DELTA", label: "Add per week", hint: "Add this many units to the weekly total. Each customer-product gets a share in line with its forecast." },
  { value: "FIXED_QTY", label: "Weekly total", hint: "Set the weekly total to this number. Each customer-product gets a share in line with its forecast." },
];

type Decision = "ACCEPT" | "EXCLUDE";
const leafKey = (c: string, s: string) => `${c}\u0000${s}`;

export function ScenarioModal({
  scenario: initial,
  defaultWeek,
  onClose,
  onChanged,
}: {
  scenario: Scenario | null;
  defaultWeek: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const nextWeek = addDaysIso(weekStartIso(defaultWeek), 7);
  const [scenario, setScenario] = useState<Scenario | null>(initial);
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [leverType, setLeverType] = useState<LeverType>(initial?.lever_type ?? "PERCENT");
  const [leverValue, setLeverValue] = useState<string>(initial ? String(initial.lever_value) : "5");
  const [periodFrom, setPeriodFrom] = useState(initial?.period_from ?? nextWeek);
  const [periodTo, setPeriodTo] = useState(initial?.period_to ?? addDaysIso(nextWeek, 27));
  const [scope, setScope] = useState<ScenarioScope>(initial?.scope ?? emptyScope());
  const [dirty, setDirty] = useState(initial == null);

  const [preview, setPreview] = useState<ScenarioCascade | null>(null);
  const [decisions, setDecisions] = useState<Map<string, Decision>>(new Map());
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cancelReason, setCancelReason] = useState<string | null>(null);

  const status = scenario?.status ?? "DRAFT";
  const editable = status === "DRAFT" || status === "PREVIEW";
  const touch = () => { setDirty(true); setPreview(null); };

  const scopeIsEmpty = SCOPE_DIMS.every((d) => scope[d.key].length === 0);

  function body() {
    return {
      name: name.trim(),
      description: description.trim() || null,
      scope,
      lever_type: leverType,
      lever_value: Number(leverValue),
      period_from: periodFrom,
      period_to: periodTo,
    };
  }

  function validate(): string | null {
    if (!name.trim()) return "Give the scenario a name.";
    if (leverValue.trim() === "" || !isFinite(Number(leverValue))) return "Enter a lever value.";
    if (periodTo < periodFrom) return "The period end is before its start.";
    return null;
  }

  async function save(): Promise<Scenario | null> {
    const v = validate();
    if (v) { setError(v); return null; }
    setBusy("save");
    setError(null);
    try {
      const saved = scenario
        ? await forecastingApi.updateScenario(scenario.id, body())
        : await forecastingApi.createScenario(body());
      setScenario(saved);
      setDirty(false);
      onChanged();
      return saved;
    } catch (e) {
      setError(extractErrorMsg(e, "Failed to save the scenario."));
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function runPreview() {
    let s = scenario;
    if (!s || dirty) s = await save();
    if (!s) return;
    setBusy("preview");
    setError(null);
    setNotice(null);
    try {
      const p = await forecastingApi.previewScenario(s.id);
      setPreview(p);
      setScenario(p.scenario);
      setHighlight(new Set());
      // Keep decisions for leaves that are still flagged.
      const still = new Set(p.flagged_leaves.map((f) => leafKey(f.customer_account, f.stock_code)));
      setDecisions((prev) => new Map([...prev].filter(([k]) => still.has(k))));
      onChanged();
    } catch (e) {
      setError(extractErrorMsg(e, "Preview failed."));
    } finally {
      setBusy(null);
    }
  }

  const flagged = preview?.flagged_leaves ?? [];
  const undecided = flagged.filter((f) => !decisions.has(leafKey(f.customer_account, f.stock_code))).length;

  async function approve() {
    if (!scenario || !preview) return;
    setBusy("approve");
    setError(null);
    const reviews: FlagReview[] = flagged.flatMap((f) => {
      const d = decisions.get(leafKey(f.customer_account, f.stock_code));
      return d ? [{ customer_account: f.customer_account, stock_code: f.stock_code, decision: d }] : [];
    });
    try {
      const res = await forecastingApi.approveScenario(scenario.id, reviews);
      setScenario(res.scenario);
      setNotice(`Approved — ${res.rows_written ?? 0} targets written. Reps see them in the Order Assistant.`);
      setPreview(null);
      onChanged();
    } catch (e: any) {
      const detail = e?.response?.data?.detail as UnreviewedError | string | undefined;
      if (detail && typeof detail === "object" && "unreviewed" in detail) {
        setError(detail.message);
        setHighlight(new Set(detail.unreviewed.map((u) => leafKey(u.customer_account, u.stock_code))));
      } else {
        setError(extractErrorMsg(e, "Approval failed."));
      }
    } finally {
      setBusy(null);
    }
  }

  async function cancelScenario() {
    if (!scenario || !cancelReason?.trim()) return;
    setBusy("cancel");
    setError(null);
    try {
      const res = await forecastingApi.cancelScenario(scenario.id, cancelReason.trim());
      setScenario(res.scenario);
      setCancelReason(null);
      setPreview(null);
      setNotice(
        status === "ACTIVE"
          ? `Cancelled — ${res.targets_recomputed} targets recomputed, ${res.targets_deleted} removed.`
          : "Cancelled.",
      );
      onChanged();
    } catch (e) {
      setError(extractErrorMsg(e, "Cancel failed."));
    } finally {
      setBusy(null);
    }
  }

  const setAll = (d: Decision) =>
    setDecisions(new Map(flagged.map((f) => [leafKey(f.customer_account, f.stock_code), d])));

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[92vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-2 px-5 py-3 border-b">
          <h3 className="font-semibold text-gray-900">
            {scenario ? `Scenario #${scenario.id}` : "New scenario"}
          </h3>
          {scenario && <StatusChip status={status} />}
          {scenario && (
            <span className="text-[11px] text-gray-500">
              by {scenario.created_by} · {fmtDate(scenario.created_at)}
              {scenario.approved_by && ` · approved by ${scenario.approved_by} ${fmtDate(scenario.approved_at)}`}
            </span>
          )}
          <div className="flex-1" />
          <button onClick={onClose} className="p-1 rounded hover:bg-gray-100" aria-label="Close"><X className="w-4 h-4" /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Definition */}
          <fieldset disabled={!editable} className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="text-xs">
                <span className="text-gray-700">Name</span>
                <input className="input mt-1" maxLength={100} value={name}
                       placeholder="e.g. Q4 Milk push at Polokwane"
                       onChange={(e) => { setName(e.target.value); touch(); }} />
              </label>
              <label className="text-xs">
                <span className="text-gray-700">Description (optional)</span>
                <input className="input mt-1" maxLength={500} value={description}
                       onChange={(e) => { setDescription(e.target.value); touch(); }} />
              </label>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
              <label className="text-xs">
                <span className="text-gray-700">Lever</span>
                <select className="input mt-1" value={leverType}
                        onChange={(e) => { setLeverType(e.target.value as LeverType); touch(); }}>
                  {LEVER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="text-xs">
                <span className="text-gray-700">{leverType === "PERCENT" ? "Change %" : "Units per week"}</span>
                <input type="number" step="any" className="input mt-1" value={leverValue}
                       onChange={(e) => { setLeverValue(e.target.value); touch(); }} />
              </label>
              <label className="text-xs">
                <span className="text-gray-700">From</span>
                <div className="mt-1"><DateInput value={periodFrom} onChange={(v) => { setPeriodFrom(v); touch(); }} /></div>
              </label>
              <label className="text-xs">
                <span className="text-gray-700">To</span>
                <div className="mt-1"><DateInput value={periodTo} onChange={(v) => { setPeriodTo(v); touch(); }} /></div>
              </label>
            </div>
            <p className="text-[11px] text-gray-500">{LEVER_OPTIONS.find((o) => o.value === leverType)?.hint}</p>

            <div>
              <div className="text-xs text-gray-700 mb-1">
                Scope <span className="text-gray-500">— all conditions must match; any value within one</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {SCOPE_DIMS.map((d) => (
                  <ScopeField
                    key={d.key}
                    dim={d.key}
                    label={d.label}
                    values={scope[d.key]}
                    disabled={!editable}
                    onChange={(vals) => { setScope({ ...scope, [d.key]: vals }); touch(); }}
                  />
                ))}
              </div>
              {scopeIsEmpty && (
                <p className="text-[11px] text-amber-700 mt-1">No scope set — this scenario applies to every customer and product.</p>
              )}
              {scope.excludeLeaves.length > 0 && (
                <div className="mt-2 text-xs">
                  <span className="text-gray-700">Excluded customer-products:</span>{" "}
                  {scope.excludeLeaves.map((l, i) => (
                    <span key={i} className="inline-flex items-center gap-1 rounded bg-gray-100 border px-1.5 py-0.5 mr-1 mb-1 font-mono">
                      {l.customer} × {l.sku}
                      {editable && (
                        <button type="button" className="text-gray-500 hover:text-red-600"
                                onClick={() => { setScope({ ...scope, excludeLeaves: scope.excludeLeaves.filter((_, j) => j !== i) }); touch(); }}>×</button>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </fieldset>

          {scenario?.cancel_reason && (
            <p className="text-xs text-red-700">Cancelled {fmtDate(scenario.cancelled_at)}: {scenario.cancel_reason}</p>
          )}
          <ErrorBanner message={error} onClose={() => setError(null)} />
          {notice && (
            <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs rounded p-2">
              <CheckCircle2 className="w-4 h-4" /> {notice}
            </div>
          )}

          {/* Preview */}
          {preview && (
            <div className="space-y-3 border-t pt-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <Box label="Customer-products / targets" value={`${fmtQty(preview.leaf_count)} / ${fmtQty(preview.leaf_week_count)}`} />
                <Box label="Forecast" value={fmtQty(preview.total_forecast_qty)} />
                <Box label="Target" value={fmtQty(preview.total_target_qty)}
                     sub={preview.total_forecast_qty > 0 ? `${fmtPct((preview.total_target_qty / preview.total_forecast_qty - 1) * 100, 1)} vs forecast` : undefined} />
                <Box label="Flagged customer-products" value={String(flagged.length)}
                     sub={flagged.length ? `${undecided} still to review` : "nothing to review"} />
              </div>
              {preview.warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" /> {w}
                </div>
              ))}

              {flagged.length > 0 && (
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <div className="text-xs font-semibold text-gray-800">Review each flagged customer-product</div>
                    <div className="flex-1" />
                    {editable && (
                      <>
                        <button className="text-xs text-blue-700 hover:underline" onClick={() => setAll("ACCEPT")}>Accept all</button>
                        <button className="text-xs text-gray-600 hover:underline" onClick={() => setAll("EXCLUDE")}>Exclude all</button>
                      </>
                    )}
                  </div>
                  <div className="border rounded max-h-64 overflow-auto">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 text-gray-600 sticky top-0">
                        <tr>
                          <th className="text-left px-2 py-1 font-medium">Customer × product</th>
                          <th className="text-left px-2 py-1 font-medium">Flags</th>
                          <th className="text-left px-2 py-1 font-medium">Why</th>
                          <th className="text-right px-2 py-1 font-medium">Decision</th>
                        </tr>
                      </thead>
                      <tbody>
                        {flagged.map((f) => {
                          const k = leafKey(f.customer_account, f.stock_code);
                          const d = decisions.get(k);
                          return (
                            <tr key={k} className={`border-t ${highlight.has(k) && !d ? "bg-red-50" : ""}`}>
                              <td className="px-2 py-1 font-mono whitespace-nowrap">{f.customer_account} × {f.stock_code}</td>
                              <td className="px-2 py-1">
                                {f.flag_codes.map((c) => (
                                  <span key={c} className="inline-block rounded bg-amber-100 text-amber-800 px-1 mr-1 mb-0.5">{FLAG_LABELS[c] ?? c}</span>
                                ))}
                              </td>
                              <td className="px-2 py-1 text-gray-600" title={f.messages.join("\n")}>
                                <span className="line-clamp-2">{f.messages[0]}</span>
                                {f.weeks.length > 1 && <span className="text-gray-400"> (+{f.weeks.length - 1} more weeks)</span>}
                              </td>
                              <td className="px-2 py-1 text-right whitespace-nowrap">
                                {(["ACCEPT", "EXCLUDE"] as Decision[]).map((opt) => (
                                  <button
                                    key={opt}
                                    disabled={!editable}
                                    onClick={() => setDecisions(new Map(decisions).set(k, opt))}
                                    className={`ml-1 px-2 py-0.5 rounded border ${
                                      d === opt
                                        ? opt === "ACCEPT" ? "bg-emerald-600 border-emerald-600 text-white" : "bg-gray-600 border-gray-600 text-white"
                                        : "bg-white hover:bg-gray-50"
                                    }`}
                                  >
                                    {opt === "ACCEPT" ? "Accept" : "Exclude"}
                                  </button>
                                ))}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-[11px] text-gray-500 mt-1">
                    Accept keeps the target as computed. Exclude removes that customer × product from this scenario (it keeps its forecast).
                  </p>
                </div>
              )}

              {preview.targets.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-gray-700">
                    Sample targets ({Math.min(preview.targets.length, 100)} of {fmtQty(preview.leaf_week_count)})
                  </summary>
                  <div className="border rounded max-h-56 overflow-auto mt-1">
                    <table className="w-full">
                      <thead className="bg-gray-50 text-gray-600 sticky top-0">
                        <tr>
                          <th className="text-left px-2 py-1 font-medium">Customer</th>
                          <th className="text-left px-2 py-1 font-medium">Product</th>
                          <th className="text-left px-2 py-1 font-medium">Week</th>
                          <th className="text-right px-2 py-1 font-medium">Forecast</th>
                          <th className="text-right px-2 py-1 font-medium">Target</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.targets.slice(0, 100).map((t, i) => (
                          <tr key={i} className="border-t">
                            <td className="px-2 py-0.5 font-mono">{t.customer_account}</td>
                            <td className="px-2 py-0.5 font-mono">{t.stock_code}</td>
                            <td className="px-2 py-0.5">{fmtDate(t.week_start)}</td>
                            <td className="px-2 py-0.5 text-right font-mono">{fmtQty(t.forecast_qty)}</td>
                            <td className={`px-2 py-0.5 text-right font-mono ${t.flags.length ? "text-amber-700" : ""}`}>
                              {fmtQty(t.target_qty)}{t.override_qty != null && <span className="text-purple-700"> (override {fmtQty(t.override_qty)})</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
            </div>
          )}

          {cancelReason !== null && (
            <div className="flex gap-2 items-center border-t pt-3">
              <input autoFocus className="input flex-1" maxLength={500} placeholder="Why cancel this scenario?"
                     value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} />
              <button className="btn btn-primary text-xs" disabled={!cancelReason.trim() || busy === "cancel"} onClick={cancelScenario}>
                Confirm cancel
              </button>
              <button className="btn btn-ghost text-xs" onClick={() => setCancelReason(null)}>Keep</button>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 px-5 py-3 border-t bg-gray-50 rounded-b-lg">
          {scenario && status !== "CANCELLED" && status !== "COMPLETED" && cancelReason === null && (
            <button className="btn btn-ghost text-xs text-red-600" onClick={() => setCancelReason("")}>Cancel scenario</button>
          )}
          <div className="flex-1" />
          {busy && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
          {editable && dirty && (
            <button className="btn btn-secondary text-xs" disabled={!!busy} onClick={save}>Save draft</button>
          )}
          {(editable || status === "ACTIVE") && (
            <button className="btn btn-secondary text-xs" disabled={!!busy} onClick={runPreview}>
              {status === "ACTIVE" ? "View cascade" : preview ? "Re-run preview" : "Preview"}
            </button>
          )}
          {editable && (
            <button
              className="btn btn-primary text-xs"
              disabled={!!busy || !preview || dirty || status !== "PREVIEW" || undecided > 0}
              title={
                !preview ? "Preview first" : undecided > 0 ? `${undecided} flagged customer-product(s) still need a decision` : "Write the targets"
              }
              onClick={approve}
            >
              Approve{preview && preview.leaf_week_count ? ` (${fmtQty(preview.leaf_week_count)} targets)` : ""}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Box({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border p-2">
      <div className="text-[11px] text-gray-500">{label}</div>
      <div className="font-mono font-semibold text-gray-900">{value}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}

/** One scope dimension: chips + type-ahead backed by /forecasting/scope-options. */
function ScopeField({
  dim,
  label,
  values,
  disabled,
  onChange,
}: {
  dim: ScopeDim;
  label: string;
  values: string[];
  disabled: boolean;
  onChange: (vals: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<ScopeOption[]>([]);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    const t = setTimeout(() => {
      forecastingApi
        .scopeOptions(dim, q.trim() || undefined, 30)
        .then(setOptions)
        .catch(() => setOptions([]))
        .finally(() => setLoading(false));
    }, 200);
    return () => clearTimeout(t);
  }, [q, open, dim]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const chosen = useMemo(() => new Set(values), [values]);

  return (
    <div ref={boxRef} className="relative flex items-start gap-2 text-xs">
      <span className="w-20 pt-1 text-gray-600 flex-shrink-0">{label}</span>
      <div className="flex-1 min-w-0 flex flex-wrap items-center gap-1 border rounded px-1 py-0.5 bg-white min-h-[28px]">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded bg-blue-50 border border-blue-200 text-blue-800 px-1.5 py-0.5">
            {v}
            {!disabled && (
              <button type="button" className="hover:text-red-600" onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
            )}
          </span>
        ))}
        {!disabled && (
          <input
            className="flex-1 min-w-[6rem] outline-none py-0.5"
            placeholder={values.length ? "" : "any"}
            value={q}
            onFocus={() => setOpen(true)}
            onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          />
        )}
      </div>
      {open && !disabled && (
        <div className="absolute z-10 left-[5.5rem] right-0 top-full mt-0.5 bg-white border rounded shadow-lg max-h-52 overflow-auto">
          {loading && <div className="px-2 py-1 text-gray-400">Searching…</div>}
          {!loading && options.length === 0 && <div className="px-2 py-1 text-gray-400">No matches in the current forecasts</div>}
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              disabled={chosen.has(o.value)}
              className="block w-full text-left px-2 py-1 hover:bg-blue-50 disabled:text-gray-300"
              onClick={() => { onChange([...values, o.value]); setQ(""); }}
            >
              <span className="font-mono">{o.value}</span>
              {o.label && <span className="text-gray-500"> — {o.label}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

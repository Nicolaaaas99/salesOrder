import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { List, Loader2, Phone, Pin, PinOff, TrendingUp, X } from "lucide-react";
import { api, auth } from "../api/client";
import type { AgentPerformance, TargetWithActual } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { TARGETS_FEATURE_ENABLED } from "../featureFlags";

/** Fire this on any mutation that could change the logged-in agent's
 *  numbers — order save, order cancel, contact-log write. The strip
 *  listens for the event and refetches. Kept as a loose event rather
 *  than a context so the trigger sites don't need prop-drilling. */
export const PERFORMANCE_REFRESH_EVENT = "performance:refresh";

export function notifyPerformanceRefresh() {
  window.dispatchEvent(new Event(PERFORMANCE_REFRESH_EVENT));
}

/** Fire this when a target is created / edited / deleted. The strip
 *  refetches so the Order Assistant stays in sync without the user
 *  having to reload. */
export const TARGETS_MUTATED_EVENT = "targets:mutated";

export function notifyTargetsMutated() {
  window.dispatchEvent(new Event(TARGETS_MUTATED_EVENT));
}

/** Fallback poll — picks up the "effective_to passed" case where no
 *  mutation event fires but a target quietly expires. 60s feels like
 *  the right trade between freshness and chatter; the strip is light. */
const TARGETS_POLL_MS = 60_000;

/** Hard cap on how many target cards the strip shows at once. Keeps
 *  the top bar readable + leaves room for the Route / Customer pickers
 *  to flex. User can rearrange via the pin icons in the modal. */
const MAX_PINNED_TARGETS = 3;

type Window = "today" | "week" | "month";

const WINDOW_LABELS: Record<Window, string> = {
  today: "Today",
  week: "This week",
  month: "This month",
};

function fmtInt(n: number): string {
  return Math.round(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}/${m}/${y.slice(2)}` : iso;
}

const PERIOD_LABEL: Record<string, string> = {
  DAILY: "Daily",
  WEEKLY: "Weekly",
  MONTHLY: "Monthly",
  CUSTOM: "Custom",
};

function scopeChips(t: TargetWithActual): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  // "Placed by" chip prefers the saved display name; falls back to the
  // raw agent id when the username column is empty (legacy / id-only
  // targets).
  if (t.username || t.agent_id != null) {
    out.push({ label: "Placed by", value: t.username ?? `#${t.agent_id}` });
  }
  if (t.telesale) out.push({ label: "Telesales", value: t.telesale });
  if (t.sales_rep) out.push({ label: "Sales rep", value: t.sales_rep });
  if (t.route) out.push({ label: "Route", value: t.route });
  if (t.customer_account) out.push({ label: "Customer", value: t.customer_account });
  if (t.category) out.push({ label: "Category", value: t.category });
  if (t.stock_code) out.push({ label: "Stock", value: t.stock_code });
  return out;
}

/** Short, display-friendly summary of a target's scope for strip
 *  cards. The chip-row from the modal is too dense for a card header,
 *  so we condense to the first dim's value — "DAIRY", "NEL",
 *  "SPA02D" — which is almost always distinctive enough. */
function compactTargetLabel(t: TargetWithActual): string {
  const chips = scopeChips(t);
  if (chips.length === 0) return "Target";
  if (chips.length === 1) return chips[0].value;
  // Multiple scope dims: lead with the "sold what" or specific anchor
  // (category / stock / customer) if present — those are the most
  // meaningful on a compound target.
  const anchor = chips.find((c) =>
    ["Category", "Stock", "Customer"].includes(c.label),
  );
  if (anchor) return anchor.value;
  return chips[0].value;
}

/** Smoothly interpolate from the previous value to the current one over
 *  `durationMs`. Used to animate the card litres + the target donut so
 *  a fresh order visibly "pours in" rather than snapping. Linear easing
 *  — the numbers are big enough that fancier easing looks fussy. */
function useCountUp(value: number, durationMs = 600): number {
  const [display, setDisplay] = useState(value);
  const prevRef = useRef(value);
  const startRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const from = prevRef.current;
    const to = value;
    if (from === to) {
      setDisplay(to);
      return;
    }
    startRef.current = null;
    const step = (ts: number) => {
      if (startRef.current == null) startRef.current = ts;
      const t = Math.min(1, (ts - startRef.current) / durationMs);
      setDisplay(from + (to - from) * t);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        prevRef.current = to;
        rafRef.current = null;
      }
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      prevRef.current = to;
    };
  }, [value, durationMs]);

  return display;
}

/** Per-user, device-local set of pinned target ids. Lives in
 *  localStorage so pins survive logout/reload on this browser. We
 *  intentionally key by lowercase username — the backend accepts
 *  case-insensitive matches on cUsername, so pin state follows the
 *  logical user even if their login casing changes. */
function usePinnedTargets(username: string | null) {
  const key = username ? `targets:pinned:${username.toLowerCase()}` : null;

  const [ids, setIds] = useState<number[]>(() => {
    if (!key) return [];
    try {
      const raw = localStorage.getItem(key);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.filter((n) => typeof n === "number") : [];
    } catch {
      return [];
    }
  });

  // Re-read when the username changes (logout/login into a different
  // account on the same browser).
  useEffect(() => {
    if (!key) {
      setIds([]);
      return;
    }
    try {
      const raw = localStorage.getItem(key);
      const parsed = raw ? JSON.parse(raw) : [];
      setIds(Array.isArray(parsed) ? parsed.filter((n) => typeof n === "number") : []);
    } catch {
      setIds([]);
    }
  }, [key]);

  // Persist on any change.
  useEffect(() => {
    if (!key) return;
    try {
      localStorage.setItem(key, JSON.stringify(ids));
    } catch {
      /* storage full / disabled — degrade silently */
    }
  }, [key, ids]);

  const toggle = useCallback(
    (id: number) =>
      setIds((prev) => {
        if (prev.includes(id)) return prev.filter((x) => x !== id);
        // Append rather than prepend so pin order matches the order
        // the user clicked them in. Enforce the max.
        return [...prev, id].slice(-MAX_PINNED_TARGETS);
      }),
    [],
  );

  const isPinned = useCallback((id: number) => ids.includes(id), [ids]);

  return { pinnedIds: ids, toggle, isPinned };
}

type Props = {
  /** Optional nodes rendered on the same flex row as the three cards,
   *  BEFORE them. The Order Assistant uses this to inline the compact
   *  Route + Customer pickers next to the cards. */
  leading?: React.ReactNode;
};

export function PerformanceStrip({ leading }: Props = {}) {
  const { username } = useAuth();
  const { pinnedIds, toggle: togglePin, isPinned } = usePinnedTargets(username);

  const [data, setData] = useState<AgentPerformance | null>(null);
  const [loading, setLoading] = useState(true);
  const [myTargets, setMyTargets] = useState<TargetWithActual[] | null>(null);
  const [showAll, setShowAll] = useState(false);

  const refetch = useCallback(() => {
    setLoading(true);
    Promise.allSettled([auth.getMyPerformance(), api.getMyTargets()])
      .then(([perfRes, targetsRes]) => {
        if (perfRes.status === "fulfilled") setData(perfRes.value);
        if (targetsRes.status === "fulfilled") setMyTargets(targetsRes.value.targets);
      })
      .finally(() => setLoading(false));
  }, []);

  const silentRefetch = useCallback(() => {
    Promise.allSettled([auth.getMyPerformance(), api.getMyTargets()]).then(
      ([perfRes, targetsRes]) => {
        if (perfRes.status === "fulfilled") setData(perfRes.value);
        if (targetsRes.status === "fulfilled") setMyTargets(targetsRes.value.targets);
      },
    );
  }, []);

  useEffect(() => {
    refetch();
    // orders:mutated gets a short delay before refetching so the
    // strip animation starts a beat AFTER the success modal + the
    // SalesOrderScreen have unmounted — otherwise the user is still
    // staring at the modal when the numbers change underneath and
    // misses the climb.
    const orderMutatedHandler = () => {
      window.setTimeout(silentRefetch, 400);
    };
    window.addEventListener(PERFORMANCE_REFRESH_EVENT, silentRefetch);
    window.addEventListener(TARGETS_MUTATED_EVENT, silentRefetch);
    window.addEventListener("orders:mutated", orderMutatedHandler);
    const pollId = window.setInterval(silentRefetch, TARGETS_POLL_MS);
    return () => {
      window.removeEventListener(PERFORMANCE_REFRESH_EVENT, silentRefetch);
      window.removeEventListener(TARGETS_MUTATED_EVENT, silentRefetch);
      window.removeEventListener("orders:mutated", orderMutatedHandler);
      window.clearInterval(pollId);
    };
  }, [refetch, silentRefetch]);

  // Primary = the first pure-agent-only target. Used as the default
  // "pin" when the user hasn't explicitly pinned anything. "Agent-only"
  // means either the stable agent_id or the legacy username column is
  // set and every other scope dim is null.
  const primaryTarget = useMemo(() => {
    if (!myTargets) return null;
    return (
      myTargets.find(
        (t) =>
          (t.agent_id != null || !!t.username) &&
          !t.telesale &&
          !t.sales_rep &&
          !t.route &&
          !t.customer_account &&
          !t.category &&
          !t.stock_code,
      ) ?? null
    );
  }, [myTargets]);

  // What actually renders in the strip. Resolve pinned IDs against
  // the live target list, dropping any that no longer exist (deleted
  // or expired). If what's left is empty — either the user hasn't
  // pinned anything yet, or every pin went stale — fall back to the
  // primary. The stale-pin case used to silently hide the primary too,
  // which looked like "my target disappeared" after creating a fresh
  // one in a session that still had pins from deleted test targets.
  const displayedTargets = useMemo(() => {
    if (!myTargets) return [];
    const activeById = new Map(myTargets.map((t) => [t.id, t]));
    const picked = pinnedIds
      .map((id) => activeById.get(id))
      .filter((t): t is TargetWithActual => t != null);
    if (picked.length > 0) return picked.slice(0, MAX_PINNED_TARGETS);
    return primaryTarget ? [primaryTarget] : [];
  }, [myTargets, pinnedIds, primaryTarget]);

  // Prune stale pin IDs from localStorage on every refetch so the
  // persisted list stays in sync with reality. Only runs when there's
  // actually drift, to avoid write amplification.
  useEffect(() => {
    if (!myTargets || pinnedIds.length === 0) return;
    const activeIds = new Set(myTargets.map((t) => t.id));
    const stale = pinnedIds.filter((id) => !activeIds.has(id));
    if (stale.length > 0) {
      // togglePin flips the state; call once per stale id to remove.
      stale.forEach((id) => togglePin(id));
    }
  }, [myTargets, pinnedIds, togglePin]);

  const totalMyTargets = myTargets?.length ?? 0;
  // "All my targets" only makes sense when there's more than one
  // target scoped to the user. With just one, the strip already
  // shows it — a button would open a modal with a single row.
  const showAllButton = totalMyTargets > 1;

  return (
    <div className="flex gap-1.5 items-stretch flex-wrap">
      {leading}
      {(["today", "week", "month"] as const).map((w) => (
        <Card
          key={w}
          label={WINDOW_LABELS[w]}
          litres={data?.litres[w] ?? 0}
          called={data?.customers_called[w] ?? 0}
          loading={loading && !data}
        />
      ))}
      {/* Targets UI gated by the TARGETS_FEATURE_ENABLED flag — see
          src/featureFlags.ts. Hidden for a client demo while the
          design is being revised; backend endpoints still work. */}
      {TARGETS_FEATURE_ENABLED && displayedTargets.map((t) => (
        <TargetDonutCard key={t.id} target={t} />
      ))}
      {TARGETS_FEATURE_ENABLED && showAllButton && (
        <AllMyTargetsButton
          count={totalMyTargets}
          onClick={() => setShowAll(true)}
        />
      )}

      {TARGETS_FEATURE_ENABLED && showAll && (
        <AllMyTargetsModal
          targets={myTargets ?? []}
          loading={loading && myTargets === null}
          isPinned={isPinned}
          onTogglePin={togglePin}
          onClose={() => setShowAll(false)}
        />
      )}
    </div>
  );
}

/** Primary agent target rendered as a radial donut — same card
 *  footprint as the Today/Week/Month cards, so the whole strip reads
 *  left-to-right as "what I did today / this week / this month → the
 *  goal". The ring fills clockwise around a centred % number; colour
 *  grades red → amber → green → blue (over target). */
function TargetDonutCard({ target }: { target: TargetWithActual }) {
  const pct = Math.max(0, target.progress_pct);
  const animatedPct = useCountUp(Math.min(1, pct), 700);
  const animatedActual = useCountUp(target.actual_litres, 700);
  const periodLabel = PERIOD_LABEL[target.period] ?? target.period;

  // Primary agent target → "Daily/Weekly/… target". Any other scope
  // → lead with the condensed scope label (e.g. "DAIRY", "NEL") so
  // multiple pinned cards don't all read "Daily target".
  const isPurePrimary =
    (target.agent_id != null || !!target.username) &&
    !target.telesale &&
    !target.sales_rep &&
    !target.route &&
    !target.customer_account &&
    !target.category &&
    !target.stock_code;
  const headerLabel = isPurePrimary
    ? `${periodLabel} target`
    : `${compactTargetLabel(target)} · ${periodLabel.toLowerCase()}`;

  return (
    <div
      className="bg-white rounded border shadow-sm px-2 py-1 flex items-center gap-3 min-w-[12rem]"
      title={`${fmtInt(target.actual_litres)} of ${fmtInt(target.target_litres)} ${periodLabel.toLowerCase()} litres (${Math.round(pct * 100)}%)`}
    >
      <Donut fillPct={animatedPct} over={pct >= 1} label={`${Math.round(pct * 100)}%`} />
      <div className="flex flex-col min-w-0 flex-1">
        <div className="text-[9px] uppercase tracking-wide text-gray-500 font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
          {headerLabel}
        </div>
        <div className="flex items-baseline gap-1 mt-0.5 whitespace-nowrap">
          <span className="font-mono font-semibold text-gray-900 text-xs">
            {fmtInt(animatedActual)}
          </span>
          <span className="text-[10px] text-gray-500">
            / {fmtInt(target.target_litres)} L
          </span>
        </div>
      </div>
    </div>
  );
}

/** Dedicated "All my targets" affordance. Shown only when the user
 *  has more than one active target — with just one, the strip
 *  already shows it in full, so a button would be noise. */
function AllMyTargetsButton({
  count,
  onClick,
}: {
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="bg-white rounded border shadow-sm px-2 py-1 min-w-[7rem] text-left hover:bg-blue-50 flex flex-col justify-center"
      title="See every target scoped to you — pin up to 3 to the top bar"
    >
      <div className="text-[9px] uppercase tracking-wide text-gray-500 font-semibold">
        My targets
      </div>
      <div className="mt-0.5 flex items-center gap-1 text-blue-700 font-semibold text-xs">
        <List className="w-3 h-3" />
        View all ({count})
      </div>
    </button>
  );
}

/** SVG radial donut. Ring fills clockwise from the top. `fillPct` is
 *  clamped to [0, 1] on display — an over-target target shows a full
 *  ring; the `over` flag colours it blue so the overflow is still
 *  obvious at a glance. */
function Donut({
  fillPct,
  over,
  label,
}: {
  fillPct: number;
  over: boolean;
  label: string;
}) {
  const level = Math.max(0, Math.min(1, fillPct));
  const color = over
    ? "#3b82f6" // blue-500 — over target
    : level >= 0.75
      ? "#10b981" // emerald-500
      : level >= 0.4
        ? "#f59e0b" // amber-500
        : "#ef4444"; // red-500

  const r = 16;
  const circumference = 2 * Math.PI * r;
  const dash = level * circumference;

  return (
    <div className="relative w-10 h-10 flex-shrink-0" aria-hidden="true">
      {/* The ring is one SVG and the label is a separate SVG so the
          ring's -90° rotation doesn't tumble the text. Two SVGs
          stacked exactly on top of each other via absolute inset-0.
          Placing the label as SVG `<text>` with text-anchor + central
          baseline puts it on the geometric centre regardless of font
          metrics — no translate nudging needed. */}
      <svg
        viewBox="0 0 40 40"
        width="40"
        height="40"
        style={{ transform: "rotate(-90deg)" }}
        className="absolute inset-0"
      >
        <circle cx="20" cy="20" r={r} fill="none" stroke="#e5e7eb" strokeWidth="5" />
        <circle
          cx="20"
          cy="20"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference - dash}`}
          style={{ transition: "stroke-dasharray 0.6s ease, stroke 0.3s ease" }}
        />
      </svg>
      <svg
        viewBox="0 0 40 40"
        width="40"
        height="40"
        className="absolute inset-0"
      >
        <text
          x="20"
          y="20"
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="10"
          fontWeight="600"
          fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
          fill="#1f2937"
        >
          {label}
        </text>
      </svg>
    </div>
  );
}

function AllMyTargetsModal({
  targets,
  loading,
  isPinned,
  onTogglePin,
  onClose,
}: {
  targets: TargetWithActual[];
  loading: boolean;
  isPinned: (id: number) => boolean;
  onTogglePin: (id: number) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pinnedCount = targets.filter((t) => isPinned(t.id)).length;
  const atCap = pinnedCount >= MAX_PINNED_TARGETS;

  // Pinned first, then furthest-from-target. Groups by state so the
  // modal reads as "here's what you've chosen to watch, then what's at
  // risk" rather than one undifferentiated list.
  const sorted = useMemo(() => {
    const copy = [...targets];
    copy.sort((a, b) => {
      const aPinned = isPinned(a.id) ? 0 : 1;
      const bPinned = isPinned(b.id) ? 0 : 1;
      if (aPinned !== bPinned) return aPinned - bPinned;
      return a.progress_pct - b.progress_pct;
    });
    return copy;
  }, [targets, isPinned]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded shadow-lg w-full max-w-3xl max-h-[85vh] flex flex-col"
      >
        <div className="flex items-start justify-between px-4 py-2 border-b">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">All my targets</div>
            <div className="text-xs text-gray-600 mt-0.5">
              Pin up to {MAX_PINNED_TARGETS} targets to the top bar — pinned {pinnedCount}/{MAX_PINNED_TARGETS}.
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-100 text-gray-500 flex-shrink-0"
            title="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-3 overflow-auto">
          {loading ? (
            <div className="h-24 flex items-center justify-center text-gray-500 text-sm">
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Loading…
            </div>
          ) : sorted.length === 0 ? (
            <div className="h-24 flex items-center justify-center text-sm text-gray-400 italic">
              No active targets right now.
            </div>
          ) : (
            <ul className="flex flex-col gap-2">
              {sorted.map((t) => (
                <TargetListItem
                  key={t.id}
                  target={t}
                  pinned={isPinned(t.id)}
                  atCap={atCap}
                  onTogglePin={() => onTogglePin(t.id)}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function TargetListItem({
  target,
  pinned,
  atCap,
  onTogglePin,
}: {
  target: TargetWithActual;
  pinned: boolean;
  atCap: boolean;
  onTogglePin: () => void;
}) {
  const pct = Math.max(0, target.progress_pct);
  const barPct = Math.min(100, pct * 100);
  const over = pct >= 1;
  const barClass = over
    ? "bg-blue-500"
    : pct >= 0.75
      ? "bg-emerald-500"
      : pct >= 0.4
        ? "bg-amber-500"
        : "bg-red-500";
  const chips = scopeChips(target);
  const disabled = !pinned && atCap;
  const pinTitle = pinned
    ? "Unpin from top bar"
    : atCap
      ? `Already pinned ${MAX_PINNED_TARGETS} targets — unpin one to swap`
      : "Pin to top bar";
  return (
    <li
      className={`border rounded p-2 ${pinned ? "border-blue-200 bg-blue-50/40" : "border-gray-200 bg-gray-50/60"}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap gap-1">
          {chips.map((c, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 bg-white border border-slate-200 rounded px-1.5 py-0.5 text-[10px]"
            >
              <span className="text-gray-500">{c.label}:</span>
              <span className="font-mono text-gray-800">{c.value}</span>
            </span>
          ))}
        </div>
        <div className="flex items-start gap-2 flex-shrink-0">
          <div className="text-[10px] text-gray-500 whitespace-nowrap">
            {PERIOD_LABEL[target.period] ?? target.period} ·{" "}
            {fmtDate(target.window_from)} → {fmtDate(target.window_to)}
          </div>
          <button
            onClick={onTogglePin}
            disabled={disabled}
            className={`p-1 rounded border text-xs flex-shrink-0 ${
              pinned
                ? "border-blue-300 bg-blue-100 text-blue-700 hover:bg-blue-200"
                : disabled
                  ? "border-gray-200 bg-gray-50 text-gray-300 cursor-not-allowed"
                  : "border-gray-300 bg-white text-gray-500 hover:bg-blue-50 hover:text-blue-700"
            }`}
            title={pinTitle}
          >
            {pinned ? <PinOff className="w-3 h-3" /> : <Pin className="w-3 h-3" />}
          </button>
        </div>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="font-mono text-[11px] font-semibold text-gray-900 whitespace-nowrap">
          {fmtInt(target.actual_litres)} / {fmtInt(target.target_litres)} L
        </span>
        <div className="flex-1 h-2 bg-gray-100 rounded overflow-hidden">
          <div
            className={`h-full ${barClass} transition-all`}
            style={{ width: `${barPct}%` }}
          />
        </div>
        <span className="text-[11px] font-mono font-semibold text-gray-700 w-10 text-right">
          {Math.round(pct * 100)}%
        </span>
      </div>
    </li>
  );
}

function Card({
  label,
  litres,
  called,
  loading,
}: {
  label: string;
  litres: number;
  called: number;
  loading: boolean;
}) {
  const animatedLitres = useCountUp(litres, 500);
  const animatedCalled = useCountUp(called, 500);
  return (
    <div className="bg-white rounded border shadow-sm px-2 py-1 min-w-[8rem]">
      <div className="text-[9px] uppercase tracking-wide text-gray-500 font-semibold">
        {label}
      </div>
      {loading ? (
        <div className="h-[32px] flex items-center text-gray-400">
          <Loader2 className="w-3 h-3 animate-spin" />
        </div>
      ) : (
        <div className="flex flex-col mt-0.5 gap-0.5">
          <div className="flex items-baseline gap-1" title="Litres ordered">
            <TrendingUp className="w-3 h-3 text-emerald-600 self-center" />
            <span className="font-mono font-semibold text-gray-900 text-xs">
              {fmtInt(animatedLitres)}
            </span>
            <span className="text-[10px] text-gray-500">litres</span>
          </div>
          <div className="flex items-baseline gap-1" title="Unique customers called">
            <Phone className="w-3 h-3 text-blue-600 self-center" />
            <span className="font-mono font-semibold text-gray-900 text-xs">
              {fmtInt(animatedCalled)}
            </span>
            <span className="text-[10px] text-gray-500">called</span>
          </div>
        </div>
      )}
    </div>
  );
}

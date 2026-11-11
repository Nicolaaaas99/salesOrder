import { Fragment, useEffect, useState } from "react";
import { FileText, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import type {
  BrokenPriceRow,
  ImportReviewStatus,
  ImportReviewSummary,
  PoDiffLine,
  PoDiffPo,
} from "../api/types";
import { useAuth } from "../context/AuthContext";
import { extractErrorMsg, fmtCurrency, fmtNumber } from "../utils/format";

/**
 * Price Review (Sales Orders → Price review).
 *
 *  1. Orders for review — imported POs an agent handed to a supervisor
 *     because a PO price is too far from ours. Supervisors see all and open
 *     them to set prices / create / reject; agents see their own.
 *  2. PO price differences — imported PO lines whose PO price disagreed
 *     with ours, as group → customer → PO (supervisors). Settled lines are
 *     archived so only what still needs fixing loads.
 *  3. Broken price-list rows — stored incl doesn't match excl + VAT
 *     (supervisors).
 *
 * PERFORMANCE: every list loads only when its tab is opened or Refresh is
 * clicked — no timers.
 */

type Tab = "reviews" | "po" | "broken";

type Props = {
  onBack: () => void;
  onOpenReview: (id: number) => void;
};

export function PriceReviewScreen({ onBack, onOpenReview }: Props) {
  const { role } = useAuth();
  const isSupervisor = role === "SUPERVISOR" || role === "ADMIN";
  const [tab, setTab] = useState<Tab>("reviews");

  return (
    <div className="screen">
      <div className="screen-header">
        <div className="screen-header-title">
          <button className="btn btn-ghost" onClick={onBack} title="Back to Sales Orders">
            ← Back
          </button>
          <h1>Price review</h1>
        </div>
      </div>
      <div className="flex gap-1 px-3 border-b border-gray-200">
        <TabButton active={tab === "reviews"} onClick={() => setTab("reviews")}>
          Orders for review
        </TabButton>
        {isSupervisor && (
          <>
            <TabButton active={tab === "po"} onClick={() => setTab("po")}>
              PO price differences
            </TabButton>
            <TabButton active={tab === "broken"} onClick={() => setTab("broken")}>
              Broken price-list rows
            </TabButton>
          </>
        )}
      </div>
      {tab === "reviews" && (
        <ReviewsTab isSupervisor={isSupervisor} onOpenReview={onOpenReview} />
      )}
      {tab === "po" && isSupervisor && <PoDifferencesTab />}
      {tab === "broken" && isSupervisor && <BrokenRowsTab />}
    </div>
  );
}

function TabButton({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-2 text-sm border-b-2 -mb-px ${
        active
          ? "border-blue-600 text-blue-700 font-semibold"
          : "border-transparent text-gray-600 hover:text-gray-900"
      }`}
    >
      {children}
    </button>
  );
}

/** Loads on mount and on Refresh only. */
function useOnDemand<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    load()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(extractErrorMsg(e, "Could not load")))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, ...deps]);
  return { data, error, loading, refresh: () => setNonce((n) => n + 1) };
}

function Toolbar({
  loading, refresh, children,
}: { loading: boolean; refresh: () => void; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 p-3">
      {children}
      <button className="btn btn-secondary ml-auto" onClick={refresh} disabled={loading}>
        <RefreshCw className="w-4 h-4" /> {loading ? "Loading…" : "Refresh"}
      </button>
    </div>
  );
}

function fmtWhen(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

async function openPo(id: number | null) {
  if (id) await api.openImportDocument(id);
}

// ---------------------------------------------------------------------------

function ReviewsTab({
  isSupervisor, onOpenReview,
}: { isSupervisor: boolean; onOpenReview: (id: number) => void }) {
  const [status, setStatus] = useState<ImportReviewStatus | "">("PENDING");
  const { data, error, loading, refresh } = useOnDemand(
    () => api.listImportReviews(status), [status],
  );
  const rows: ImportReviewSummary[] = data ?? [];
  return (
    <div className="flex-1 overflow-auto">
      <Toolbar loading={loading} refresh={refresh}>
        <select
          className="input w-auto"
          value={status}
          onChange={(e) => setStatus(e.target.value as ImportReviewStatus | "")}
        >
          <option value="PENDING">Waiting for review</option>
          <option value="CREATED">Created</option>
          <option value="REJECTED">Rejected</option>
          <option value="">All</option>
        </select>
        <span className="text-xs text-gray-600">
          {isSupervisor
            ? "Open an order to set its prices, then create or reject it."
            : "Orders you submitted for review."}
        </span>
      </Toolbar>
      {error && <div className="px-3 text-sm text-red-700">{error}</div>}
      <table className="w-full text-xs border-collapse">
        <thead className="bg-gray-100">
          <tr className="text-gray-700 border-b border-gray-300 text-left">
            <th className="px-2 py-1.5">#</th>
            <th className="px-2 py-1.5">Submitted</th>
            <th className="px-2 py-1.5">By</th>
            <th className="px-2 py-1.5">Customer</th>
            <th className="px-2 py-1.5">PO</th>
            <th className="px-2 py-1.5">Delivery</th>
            <th className="px-2 py-1.5 text-right">PO total</th>
            <th className="px-2 py-1.5 text-right">Lines</th>
            <th className="px-2 py-1.5">Note</th>
            <th className="px-2 py-1.5">Status</th>
            <th className="px-2 py-1.5" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              className="border-b border-gray-200 align-top cursor-pointer hover:bg-blue-50"
              onClick={() => onOpenReview(r.id)}
              title="Open this order"
            >
              <td className="px-2 py-1.5">{r.id}</td>
              <td className="px-2 py-1.5 whitespace-nowrap">{fmtWhen(r.submitted_at)}</td>
              <td className="px-2 py-1.5">{r.submitted_by}</td>
              <td className="px-2 py-1.5">
                <span className="font-mono">{r.customer_account}</span> {r.customer_name}
              </td>
              <td className="px-2 py-1.5 font-mono">{r.po_number}</td>
              <td className="px-2 py-1.5 whitespace-nowrap">{r.delivery_date ?? "—"}</td>
              <td className="px-2 py-1.5 text-right">
                {r.po_total_incl != null ? fmtCurrency(r.po_total_incl) : "—"}
              </td>
              <td className="px-2 py-1.5 text-right">{r.review_lines}</td>
              <td className="px-2 py-1.5 max-w-xs">
                {r.submit_note}
                {r.review_note && (
                  <div className="text-gray-600">{r.reviewed_by}: {r.review_note}</div>
                )}
              </td>
              <td className="px-2 py-1.5 whitespace-nowrap">
                {r.status === "CREATED" ? `Created ${r.order_num}` : r.status === "PENDING" ? "Waiting" : "Rejected"}
              </td>
              <td className="px-2 py-1.5 whitespace-nowrap text-right">
                {r.document_id && (
                  <button
                    className="text-blue-700 hover:underline mr-2"
                    onClick={(e) => {
                      e.stopPropagation();
                      openPo(r.document_id);
                    }}
                    title="Open the document the order was imported from"
                  >
                    <FileText className="w-3.5 h-3.5 inline" /> Source document
                  </button>
                )}
                <span className="text-blue-700">
                  {isSupervisor && r.status === "PENDING" ? "Review ›" : "Open ›"}
                </span>
              </td>
            </tr>
          ))}
          {!loading && rows.length === 0 && (
            <tr>
              <td colSpan={11} className="px-2 py-6 text-center text-gray-500">
                Nothing here.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PO price differences: corporate group → customer → PO → lines.
//
// Opening the tab (or Refresh) re-prices the OPEN lines on the server and
// archives the settled ones (fixed / within 5c),
// so only what still needs fixing is loaded. Archived lines load per PO,
// only when one of those filters is opened inside a PO.
// ---------------------------------------------------------------------------

type DiffFilter = "PROBLEM" | "TOLERANCE" | "FIXED" | "DISMISSED" | "ALL";

const DIFF_FILTERS: { key: DiffFilter; label: string; hint: string }[] = [
  { key: "PROBLEM", label: "To fix", hint: "Ours is above the PO, or the PO is more than 5c above ours" },
  { key: "TOLERANCE", label: "Within 5c", hint: "Was already within 5c when imported (charged ours automatically). A difference, not a problem (archived)" },
  { key: "FIXED", label: "Fixed", hint: "Was a problem when imported; ours has since been fixed in Evolution to match the PO or sit up to 5c below it (archived)" },
  { key: "DISMISSED", label: "Dismissed", hint: "A supervisor decided not to fix it, with a reason" },
  { key: "ALL", label: "All", hint: "Every line of this PO" },
];

const poKey = (p: { customer_account: string; po_number: string; order_num: string }) =>
  `${p.customer_account}|${p.po_number}|${p.order_num}`;

function matchesFilter(l: PoDiffLine, f: DiffFilter) {
  if (f === "ALL") return true;
  return l.status === f;
}

function PoDifferencesTab() {
  const [allPos, setAllPos] = useState(false);
  const { data, error, loading, refresh } = useOnDemand(
    () => api.getPoPriceDifferences(allPos), [allPos],
  );
  const [openKey, setOpenKey] = useState<string | null>(null);
  const pos = data?.pos ?? [];
  const lines = data?.lines ?? [];
  const openPoRow = openKey ? pos.find((p) => poKey(p) === openKey) ?? null : null;

  if (openPoRow) {
    return (
      <PoDetail
        po={openPoRow}
        openLines={lines.filter((l) => poKey(l) === openKey)}
        onBack={() => setOpenKey(null)}
        onChanged={refresh}
      />
    );
  }

  return (
    <div className="flex-1 overflow-auto">
      <Toolbar loading={loading} refresh={refresh}>
        <label className="inline-flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={allPos} onChange={(e) => setAllPos(e.target.checked)} />
          Show POs with nothing to fix
        </label>
        <span className="text-xs text-gray-600">
          Lines leave "to fix" only when the price is fixed in Evolution (or is within 5c), or a
          supervisor dismisses them with a reason. Open a PO to see its archived lines.
        </span>
      </Toolbar>
      {error && <div className="px-3 text-sm text-red-700">{error}</div>}
      <PoTree pos={pos} onOpen={(p) => setOpenKey(poKey(p))} />
      {!loading && pos.length === 0 && !error && (
        <div className="px-3 py-6 text-center text-xs text-gray-500">
          {allPos ? "No PO price differences recorded yet." : "Nothing to fix."}
        </div>
      )}
    </div>
  );
}

type Counts = { to_fix: number; tolerance: number; fixed: number; dismissed: number; pos: number };

function sumCounts(list: PoDiffPo[]): Counts {
  return list.reduce(
    (c, p) => ({
      to_fix: c.to_fix + p.to_fix,
      tolerance: c.tolerance + p.tolerance,
      fixed: c.fixed + p.fixed,
      dismissed: c.dismissed + p.dismissed,
      pos: c.pos + 1,
    }),
    { to_fix: 0, tolerance: 0, fixed: 0, dismissed: 0, pos: 0 },
  );
}

function groupBy<T>(list: T[], key: (t: T) => string): [string, T[]][] {
  const m = new Map<string, T[]>();
  for (const t of list) {
    const k = key(t);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(t);
  }
  return [...m.entries()];
}

/** Most to fix first, then by name. */
function byToFix<T>(entries: [string, T[]][], counts: (v: T[]) => Counts) {
  return entries
    .map(([k, v]) => ({ k, v, c: counts(v) }))
    .sort((a, b) => b.c.to_fix - a.c.to_fix || a.k.localeCompare(b.k));
}

function PoTree({ pos, onOpen }: { pos: PoDiffPo[]; onOpen: (p: PoDiffPo) => void }) {
  // Everything starts collapsed: groups show their counts, click to drill in.
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [openCustomers, setOpenCustomers] = useState<Set<string>>(new Set());
  const toggle = (set: Set<string>, k: string) => {
    const n = new Set(set);
    n.has(k) ? n.delete(k) : n.add(k);
    return n;
  };
  const groups = byToFix(groupBy(pos, (p) => p.customer_group || "Non Corporate"), sumCounts);

  return (
    <div className="px-3 pb-4 text-xs">
      {groups.map(({ k: group, v: groupPos, c }) => {
        const gOpen = openGroups.has(group);
        const customers = byToFix(groupBy(groupPos, (p) => p.customer_account), sumCounts);
        return (
          <div key={group} className="border border-gray-200 rounded mb-2">
            <button
              className="w-full flex items-center gap-2 px-2 py-2 bg-gray-50 hover:bg-gray-100 text-left"
              onClick={() => setOpenGroups((s) => toggle(s, group))}
            >
              <Caret open={gOpen} />
              <span className="font-semibold text-sm">{group}</span>
              <span className="text-gray-500">
                {customers.length} customer{customers.length === 1 ? "" : "s"}, {c.pos} PO{c.pos === 1 ? "" : "s"}
              </span>
              <CountChips c={c} />
            </button>
            {gOpen &&
              customers.map(({ k: account, v: custPos, c: cc }) => {
                const cKey = `${group}|${account}`;
                const cOpen = openCustomers.has(cKey);
                const sorted = [...custPos].sort(
                  (a, b) => b.to_fix - a.to_fix || (b.imported_on ?? "").localeCompare(a.imported_on ?? ""),
                );
                return (
                  <div key={cKey} className="border-t border-gray-200">
                    <button
                      className="w-full flex items-center gap-2 pl-6 pr-2 py-1.5 hover:bg-gray-50 text-left"
                      onClick={() => setOpenCustomers((s) => toggle(s, cKey))}
                    >
                      <Caret open={cOpen} />
                      <span className="font-mono font-semibold">{account}</span>
                      <span className="text-gray-700 truncate">{custPos[0].customer_name}</span>
                      <span className="text-gray-500 whitespace-nowrap">
                        {cc.pos} PO{cc.pos === 1 ? "" : "s"}
                      </span>
                      <CountChips c={cc} />
                    </button>
                    {cOpen && (
                      <table className="w-full border-collapse">
                        <tbody>
                          {sorted.map((p) => (
                            <tr
                              key={poKey(p)}
                              className="border-t border-gray-100 cursor-pointer hover:bg-blue-50"
                              onClick={() => onOpen(p)}
                              title="Open this PO"
                            >
                              <td className="pl-12 pr-2 py-1.5 whitespace-nowrap">
                                PO <span className="font-mono font-semibold">{p.po_number}</span>
                              </td>
                              <td className="px-2 py-1.5 whitespace-nowrap text-gray-600">
                                Order <span className="font-mono">{p.order_num}</span>
                              </td>
                              <td className="px-2 py-1.5 whitespace-nowrap text-gray-600">
                                {p.imported_on ? p.imported_on.split("-").reverse().join("/") : ""}
                              </td>
                              <td className="px-2 py-1.5 w-full">
                                <CountChips c={sumCounts([p])} />
                              </td>
                              <td className="px-2 py-1.5 whitespace-nowrap text-right text-blue-700">Open ›</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                );
              })}
          </div>
        );
      })}
    </div>
  );
}

function Caret({ open }: { open: boolean }) {
  return <span className="inline-block w-3 text-gray-500">{open ? "▾" : "▸"}</span>;
}

function CountChips({ c }: { c: Counts }) {
  const chip = (n: number, label: string, cls: string, title: string) =>
    n > 0 && (
      <span
        className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}
        title={title}
      >
        {n} {label}
      </span>
    );
  return (
    <span className="ml-auto inline-flex gap-1">
      {chip(c.to_fix, "to fix", "bg-amber-100 text-amber-800 border-amber-200", "Lines that still need fixing")}
      {chip(c.tolerance, "within 5c", "bg-gray-100 text-gray-700 border-gray-300", "Archived: was already within 5c at import")}
      {chip(c.fixed, "fixed", "bg-green-100 text-green-800 border-green-200", "Archived: fixed in Evolution since the import")}
      {chip(c.dismissed, "dismissed", "bg-white text-gray-700 border-gray-400", "Archived: a supervisor decided not to fix it")}
    </span>
  );
}

function PoDetail({
  po, openLines, onBack, onChanged,
}: {
  po: PoDiffPo;
  openLines: PoDiffLine[];
  onBack: () => void;
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState<DiffFilter>(po.to_fix > 0 ? "PROBLEM" : "ALL");
  const [archived, setArchived] = useState<PoDiffLine[] | null>(null);
  const [archivedErr, setArchivedErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const totalArchived = po.tolerance + po.fixed + po.dismissed;
  // The line being dismissed: its reason is typed in a small modal.
  const [dismissing, setDismissing] = useState<{ line: PoDiffLine; reason: string } | null>(null);
  const [dismissErr, setDismissErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function confirmDismiss() {
    if (!dismissing || dismissing.reason.trim().length < 3) return;
    setSaving(true);
    setDismissErr(null);
    try {
      await api.dismissPoDiffLine(dismissing.line.id, dismissing.reason.trim());
      setDismissing(null);
      onChanged();
    } catch (e) {
      setDismissErr(extractErrorMsg(e, "Could not dismiss the line"));
    } finally {
      setSaving(false);
    }
  }

  // Archived lines: loaded once, the first time a filter needs them.
  const needArchived = filter !== "PROBLEM" && totalArchived > 0;
  useEffect(() => {
    if (!needArchived || archived) return;
    let alive = true;
    api
      .getArchivedPoDiffLines(po.customer_account, po.po_number, po.order_num)
      .then((l) => alive && setArchived(l))
      .catch((e) => alive && setArchivedErr(extractErrorMsg(e, "Could not load the archived lines")));
    return () => { alive = false; };
  }, [needArchived, archived, po]);

  // Re-fetch archived lines after the report reloads (a line may have moved):
  // `po` is a new object on every reload.
  useEffect(() => { setArchived(null); }, [po]);

  const all = [...openLines, ...(archived ?? []).filter((a) => !openLines.some((o) => o.id === a.id))];
  const rows = all.filter((l) => matchesFilter(l, filter));
  const count = (f: DiffFilter) =>
    f === "PROBLEM" ? po.to_fix
      : f === "TOLERANCE" ? po.tolerance
      : f === "FIXED" ? po.fixed
      : f === "DISMISSED" ? po.dismissed
      : po.to_fix + totalArchived;

  return (
    <div className="flex-1 overflow-auto">
      <div className="flex items-center gap-2 px-3 pt-3 text-xs text-gray-600">
        <button className="text-blue-700 hover:underline" onClick={onBack}>
          ← All POs
        </button>
        <span>›</span>
        <span>{po.customer_group}</span>
        <span>›</span>
        <span>
          <span className="font-mono">{po.customer_account}</span> {po.customer_name}
        </span>
      </div>
      <div className="flex items-center gap-3 px-3 pt-1">
        <h2 className="text-base font-semibold">
          PO <span className="font-mono">{po.po_number}</span>
        </h2>
        <span className="text-xs text-gray-600">
          Order <span className="font-mono">{po.order_num}</span>
          {po.imported_on && <> · imported {po.imported_on.split("-").reverse().join("/")}</>}
        </span>
        {po.document_id && (
          <button
            className="btn btn-secondary"
            onClick={() =>
              api.openImportDocument(po.document_id!).catch((e) =>
                setMsg(extractErrorMsg(e, "Could not open the source document")),
              )
            }
          >
            <FileText className="w-4 h-4" /> Source document
          </button>
        )}
      </div>
      <div className="flex items-center gap-2 p-3">
        <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
          {DIFF_FILTERS.map((f) => (
            <button
              key={f.key}
              className={`px-2.5 py-1 border-r last:border-r-0 border-gray-300 ${
                filter === f.key ? "bg-blue-600 text-white font-semibold" : "bg-white hover:bg-gray-50"
              }`}
              onClick={() => setFilter(f.key)}
              title={f.hint}
            >
              {f.label} ({count(f.key)})
            </button>
          ))}
        </div>
      </div>
      {(msg || archivedErr) && <div className="px-3 pb-2 text-sm text-red-700">{msg || archivedErr}</div>}
      <table className="w-full text-xs border-collapse">
        <thead className="bg-gray-100">
          <tr className="text-gray-700 border-b border-gray-300 text-left">
            <th className="px-2 py-1.5">Status</th>
            <th className="px-2 py-1.5">Item</th>
            <th className="px-2 py-1.5 text-right">Qty</th>
            <th className="px-2 py-1.5">Pulling from</th>
            <th className="px-2 py-1.5 text-right">PO</th>
            <th className="px-2 py-1.5 text-right" title="Our price in Evolution right now, deals included">Ours</th>
            <th className="px-2 py-1.5 text-right" title="PO minus ours">Diff</th>
            <th className="px-2 py-1.5" title="The price this line was charged on the order">Charged</th>
            <th className="px-2 py-1.5" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Fragment key={r.id}>
            <tr
              className={`border-b border-gray-200 align-top ${r.status === "PROBLEM" ? "bg-amber-50" : ""}`}
            >
              <td className="px-2 py-1.5">
                <DiffStatusChip row={r} />
                {r.status === "DISMISSED" && (
                  <div className="text-gray-600 mt-0.5 max-w-[14rem]" title={`Dismissed by ${r.archived_by}`}>
                    “{r.archive_note}” <span className="text-gray-500">· {r.archived_by}</span>
                  </div>
                )}
              </td>
              <td className="px-2 py-1.5">
                <span className="font-mono font-semibold">{r.stock_code}</span> {r.description}
                <div className="text-gray-500">{r.whse}</div>
              </td>
              <td className="px-2 py-1.5 text-right">{fmtNumber(r.qty)}</td>
              <td className="px-2 py-1.5">
                <SourceCell row={r} />
              </td>
              <td className="px-2 py-1.5 text-right">{fmtNumber(r.po_price)}</td>
              <td className="px-2 py-1.5 text-right">
                {r.our_price_now != null ? fmtNumber(r.our_price_now) : "—"}
                {r.our_price_now != null && Math.abs(r.our_price_now - r.our_price_then) >= 0.005 && (
                  <div className="text-gray-500" title="Ours when the order was imported">
                    was {fmtNumber(r.our_price_then)}
                  </div>
                )}
                {r.our_price_now == null && (
                  <div className="text-gray-500" title="Ours when the order was imported">
                    was {fmtNumber(r.our_price_then)}
                  </div>
                )}
              </td>
              <td
                className={`px-2 py-1.5 text-right ${
                  r.status === "PROBLEM" ? "font-semibold text-red-700" : ""
                }`}
              >
                {r.diff_now != null ? fmtNumber(r.diff_now) : "—"}
              </td>
              <td className="px-2 py-1.5 whitespace-nowrap">
                <ChargeChip row={r} />
              </td>
              <td className="px-2 py-1.5 text-right whitespace-nowrap">
                {r.status === "PROBLEM" && (
                  <button
                    className="text-gray-600 hover:text-gray-900 hover:underline"
                    onClick={() => { setDismissErr(null); setDismissing({ line: r, reason: "" }); }}
                    title="Don't fix this price: you'll be asked for a reason"
                  >
                    Dismiss
                  </button>
                )}
              </td>
            </tr>
            </Fragment>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={9} className="px-2 py-6 text-center text-gray-500">
                {needArchived && !archived && !archivedErr ? "Loading…" : "Nothing in this list."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {dismissing && (
        <div
          className="fixed inset-0 z-50 bg-black bg-opacity-30 flex items-center justify-center p-4"
          onMouseDown={(e) => { if (e.target === e.currentTarget && !saving) setDismissing(null); }}
        >
          <div className="bg-white rounded shadow-lg w-full max-w-md text-sm">
            <div className="px-4 py-3 border-b bg-gray-50 font-semibold">Dismiss price difference</div>
            <div className="px-4 py-3 space-y-3">
              <div className="text-xs text-gray-700">
                <span className="font-mono font-semibold">{dismissing.line.stock_code}</span>{" "}
                {dismissing.line.description}
                <div className="text-gray-600 mt-0.5">
                  PO {fmtNumber(dismissing.line.po_price)} · ours{" "}
                  {dismissing.line.our_price_now != null ? fmtNumber(dismissing.line.our_price_now) : "—"}
                  {dismissing.line.diff_now != null && <> · diff {fmtNumber(dismissing.line.diff_now)}</>}
                </div>
              </div>
              <label className="block">
                <span className="text-xs text-gray-700">Why not fix it?</span>
                <textarea
                  autoFocus
                  rows={2}
                  maxLength={200}
                  className="input w-full mt-1 text-xs"
                  placeholder="e.g. Customer error: PO price was never agreed"
                  value={dismissing.reason}
                  onChange={(e) => setDismissing({ ...dismissing, reason: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); confirmDismiss(); }
                    if (e.key === "Escape" && !saving) setDismissing(null);
                  }}
                />
              </label>
              {dismissErr && <div className="text-xs text-red-700">{dismissErr}</div>}
            </div>
            <div className="px-4 py-3 border-t flex justify-end gap-2">
              <button className="btn btn-secondary" onClick={() => setDismissing(null)} disabled={saving}>
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={confirmDismiss}
                disabled={saving || dismissing.reason.trim().length < 3}
              >
                {saving ? "Dismissing…" : "Dismiss"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DiffStatusChip({ row }: { row: PoDiffLine }) {
  const d = row.diff_now;
  const when = row.archived_at ? ` (${row.archived_at.slice(0, 10).split("-").reverse().join("/")})` : "";
  const [label, cls, title] =
    row.status === "FIXED"
      ? ["Fixed", "bg-green-100 text-green-800 border-green-200", `Fixed in Evolution: ours now matches the PO or is up to 5c below it${when}`]
      : row.status === "TOLERANCE"
      ? ["Within 5c", "bg-gray-100 text-gray-700 border-gray-300",
         "The PO is up to 5c above ours: imports charge ours automatically"]
      : row.status === "DISMISSED"
      ? ["Dismissed", "bg-white text-gray-700 border-gray-400",
         `Not fixed: dismissed by ${row.archived_by}${when}. ${row.archive_note}`]
      : d == null
      ? ["No price", "bg-red-100 text-red-800 border-red-200", "No price for this customer today"]
      : d < 0
      ? ["Ours above PO", "bg-red-100 text-red-800 border-red-200",
         "We'd charge more than the PO: claim risk"]
      : ["PO > 5c above", "bg-amber-100 text-amber-800 border-amber-200",
         "The PO is more than 5c above ours"];
  return (
    <span
      className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}
      title={title}
    >
      {label}
    </span>
  );
}

/** Where ours comes from: the price list, or the deal that beats it. */
function SourceCell({ row }: { row: PoDiffLine }) {
  const src = row.source_now;
  if (!src) {
    // Dismissed (archived without re-pricing): show the import's source.
    return row.source_then ? (
      <span className="text-gray-500" title="Where ours came from when the order was imported">
        {row.source_then}
      </span>
    ) : (
      <span className="text-gray-400">—</span>
    );
  }
  if (src === "None") return <span className="text-gray-500">No price</span>;
  const isDeal = src.includes("Deal");
  const fmtD = (iso: string | null) => (iso ? iso.split("-").reverse().join("/") : "?");
  return (
    <div>
      <span
        className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold ${
          isDeal
            ? "bg-violet-100 text-violet-800 border-violet-200"
            : "bg-sky-100 text-sky-800 border-sky-200"
        }`}
      >
        {src}
      </span>
      <div className="text-gray-600 mt-0.5">
        {isDeal ? (
          row.deal_for && (
            <>
              {src === "Customer Deal" ? "Customer " : "Customer group "}
              <span className="font-mono">{row.deal_for}</span>
              <div className="text-gray-500">
                {fmtD(row.deal_from)} – {fmtD(row.deal_to)}
              </div>
            </>
          )
        ) : (
          <>List: {row.price_list || "—"}</>
        )}
      </div>
      {row.source_then && row.source_then !== src && (
        <div className="text-gray-500" title="Where ours came from when the order was imported">
          was {row.source_then}
        </div>
      )}
    </div>
  );
}

function BrokenRowsTab() {
  const { data, error, loading, refresh } = useOnDemand(() => api.getBrokenPriceRows());
  const rows: BrokenPriceRow[] = data ?? [];
  return (
    <div className="flex-1 overflow-auto">
      <Toolbar loading={loading} refresh={refresh}>
        <span className="text-xs text-gray-600">
          Price-list rows (warehouses 48 / 49) whose stored incl doesn't match excl + VAT.
          Orders keyed in Evolution charge the stored incl; the app charges the correct one.
          Overcharges first. Customers with a deal on the item aren't affected.
        </span>
      </Toolbar>
      {error && <div className="px-3 text-sm text-red-700">{error}</div>}
      <table className="w-full text-xs border-collapse">
        <thead className="bg-gray-100">
          <tr className="text-gray-700 border-b border-gray-300 text-left">
            <th className="px-2 py-1.5">Effect</th>
            <th className="px-2 py-1.5">Price list</th>
            <th className="px-2 py-1.5">Whse</th>
            <th className="px-2 py-1.5">Item</th>
            <th className="px-2 py-1.5 text-right">Excl</th>
            <th className="px-2 py-1.5 text-right">Stored incl</th>
            <th className="px-2 py-1.5 text-right">VAT %</th>
            <th className="px-2 py-1.5 text-right">Correct incl</th>
            <th className="px-2 py-1.5 text-right">Diff</th>
            <th className="px-2 py-1.5 text-right">Customers</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={i}
              className={`border-b border-gray-200 ${r.diff > 0 ? "bg-red-50" : ""}`}
            >
              <td className="px-2 py-1.5">{r.effect}</td>
              <td className="px-2 py-1.5">{r.price_list}</td>
              <td className="px-2 py-1.5">{r.whse}</td>
              <td className="px-2 py-1.5">
                <span className="font-mono font-semibold">{r.stock_code}</span> {r.description}
              </td>
              <td className="px-2 py-1.5 text-right">{fmtNumber(r.stored_excl)}</td>
              <td className="px-2 py-1.5 text-right">{fmtNumber(r.stored_incl)}</td>
              <td className="px-2 py-1.5 text-right">{r.vat_pct}</td>
              <td className="px-2 py-1.5 text-right">{fmtNumber(r.correct_incl)}</td>
              <td className="px-2 py-1.5 text-right">{fmtNumber(r.diff)}</td>
              <td className="px-2 py-1.5 text-right">{r.customers}</td>
            </tr>
          ))}
          {!loading && rows.length === 0 && (
            <tr>
              <td colSpan={10} className="px-2 py-6 text-center text-gray-500">
                No broken rows.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/** The price a line was charged: PO / OURS / SET, and the amount. */
function ChargeChip({ row }: { row: PoDiffLine }) {
  const cls =
    row.charged === "PO"
      ? "bg-blue-100 text-blue-800 border-blue-200"
      : row.charged === "SET"
      ? "bg-violet-100 text-violet-800 border-violet-200"
      : "bg-gray-100 text-gray-700 border-gray-300";
  return (
    <span title={row.charged_auto ? "Charged ours automatically (PO at most 5c above ours)" : undefined}>
      <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>
        {row.charged}
      </span>{" "}
      {fmtNumber(row.charged_price)}
      {row.charged_auto && <span className="text-gray-500"> auto</span>}
    </span>
  );
}

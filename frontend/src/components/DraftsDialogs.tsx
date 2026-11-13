import { useState } from "react";
import { FileClock, Trash2 } from "lucide-react";
import type { DraftSummary } from "../api/types";
import { fmtCurrency } from "../utils/format";
import { confirmDialog } from "./ConfirmDialog";

const SOURCE_LABEL: Record<DraftSummary["source"], string> = {
  ORDER_ASSISTANT: "Order Assistant",
  SALES_ORDER_ENTRY: "Sales Order Entry",
};

function fmtDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

/** "07:56" today, "Thu 07:56" on another day. */
function fmtSaved(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString()
    ? time
    : `${d.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
}

/** Two compact lines: customer, then source · lines · total · saved. */
function DraftInfo({ d, showExpiry = false }: { d: DraftSummary; showExpiry?: boolean }) {
  return (
    <div className="min-w-0" title={`Deleted automatically on ${fmtDay(d.expires_on)}`}>
      <div className="text-sm truncate">
        <span className="font-semibold text-gray-900">{d.customer_account}</span>
        {d.customer_name && <span className="text-gray-600"> · {d.customer_name}</span>}
      </div>
      <div className="text-xs text-gray-500 truncate">
        {SOURCE_LABEL[d.source]} · {d.line_count} line{d.line_count === 1 ? "" : "s"}
        {d.total_incl != null && <> · {fmtCurrency(d.total_incl)}</>} · {fmtSaved(d.created_at)}
        {showExpiry && <span className="text-amber-700"> · deleted {fmtDay(d.expires_on)}</span>}
      </div>
    </div>
  );
}

const SMALL_BTN = "h-7 rounded text-xs font-semibold transition-colors disabled:opacity-50";

/** The agent's drafts: open one to carry on, or delete it. */
export function DraftsModal({
  drafts, error, busy, onOpen, onDelete, onClose,
}: {
  drafts: DraftSummary[];
  error: string | null;
  busy: boolean;
  onOpen: (d: DraftSummary) => void;
  onDelete: (d: DraftSummary) => void;
  onClose: () => void;
}) {
  const askDelete = async (d: DraftSummary) => {
    const ok = await confirmDialog({
      title: "Delete this draft?",
      message: (
        <>
          <strong>{d.customer_account}</strong>
          {d.customer_name && <> · {d.customer_name}</>}
          <br />
          {d.line_count} line{d.line_count === 1 ? "" : "s"}
          {d.total_incl != null && <> · {fmtCurrency(d.total_incl)}</>}
        </>
      ),
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (ok) onDelete(d);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal text-left"
        style={{ maxWidth: 520, width: "100%" }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      >
        <h2 className="modal-title">My drafts</h2>
        {error ? (
          <p className="text-sm text-red-600 mb-3">{error}</p>
        ) : drafts.length === 0 ? (
          <p className="text-sm text-gray-600 mb-3">No drafts.</p>
        ) : (
          <ul className="divide-y divide-gray-100 border border-gray-200 rounded mb-2 max-h-[60vh] overflow-y-auto">
            {drafts.map((d) => (
              <li key={d.id} className="flex items-center gap-2 px-3 py-1.5">
                <div className="flex-1 min-w-0"><DraftInfo d={d} /></div>
                <button
                  className={`${SMALL_BTN} px-3 bg-blue-600 text-white hover:bg-blue-700`}
                  disabled={busy}
                  onClick={() => onOpen(d)}
                >
                  Open
                </button>
                <button
                  className={`${SMALL_BTN} w-7 flex items-center justify-center border border-gray-300 text-gray-500 hover:text-red-600 hover:border-red-300`}
                  disabled={busy}
                  title="Delete this draft"
                  aria-label="Delete this draft"
                  onClick={() => askDelete(d)}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-3">
          <p className="flex-1 text-[11px] text-gray-500">Deleted automatically 3 days after saving.</p>
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

type Choice = "FINISH" | "DELETE";

/** Shown on a later day: every draft saved before today must be finished
 *  or deleted before carrying on. Can't be dismissed. */
export function StaleDraftsPrompt({
  drafts, busy, onDone,
}: {
  drafts: DraftSummary[];
  busy: boolean;
  /** Delete the DELETE ones; open the first FINISH one. */
  onDone: (finish: DraftSummary[], remove: DraftSummary[]) => void;
}) {
  const [choice, setChoice] = useState<Record<number, Choice>>({});
  const allChosen = drafts.every((d) => choice[d.id]);
  const finish = drafts.filter((d) => choice[d.id] === "FINISH");
  const remove = drafts.filter((d) => choice[d.id] === "DELETE");
  const seg = (d: DraftSummary, c: Choice, label: string) => (
    <button
      role="radio"
      aria-checked={choice[d.id] === c}
      className={`h-7 px-2.5 text-xs transition-colors ${c === "DELETE" ? "border-l border-gray-300" : ""} ${
        choice[d.id] === c
          ? c === "FINISH" ? "bg-blue-600 text-white font-semibold" : "bg-red-600 text-white font-semibold"
          : "bg-white text-gray-700 hover:bg-gray-50"
      }`}
      onClick={() => setChoice((p) => ({ ...p, [d.id]: c }))}
    >
      {label}
    </button>
  );

  return (
    <div className="modal-backdrop">
      <div className="modal text-left" style={{ maxWidth: 560, width: "100%" }}>
        <div className="flex items-center gap-2 mb-1">
          <FileClock className="w-5 h-5 text-amber-600" />
          <h2 className="modal-title" style={{ margin: 0 }}>
            Unfinished draft{drafts.length === 1 ? "" : "s"}
          </h2>
        </div>
        <p className="text-sm text-gray-700 mb-3">
          {drafts.length === 1
            ? "This draft is from a previous day. Finish it now or delete it."
            : "These drafts are from a previous day. Finish or delete each one."}
        </p>
        <ul className="divide-y divide-gray-100 border border-gray-200 rounded mb-3 max-h-[60vh] overflow-y-auto">
          {drafts.map((d) => (
            <li key={d.id} className="flex items-center gap-2 px-3 py-1.5">
              <div className="flex-1 min-w-0"><DraftInfo d={d} showExpiry /></div>
              <div className="inline-flex rounded border border-gray-300 overflow-hidden flex-none" role="radiogroup">
                {seg(d, "FINISH", "Finish")}
                {seg(d, "DELETE", "Delete")}
              </div>
            </li>
          ))}
        </ul>
        {finish.length > 1 && (
          <p className="text-xs text-gray-600 mb-2">
            The first one opens now; the others stay under <strong>Drafts</strong>.
          </p>
        )}
        <div className="flex justify-end">
          <button
            className="btn btn-primary"
            disabled={!allChosen || busy}
            onClick={() => onDone(finish, remove)}
            title={allChosen ? undefined : "Choose Finish or Delete for every draft"}
          >
            {finish.length > 0 ? "Continue" : remove.length === 1 ? "Delete draft" : "Delete drafts"}
          </button>
        </div>
      </div>
    </div>
  );
}

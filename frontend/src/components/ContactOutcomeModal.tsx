import { useEffect, useState } from "react";
import { AlertTriangle, XCircle } from "lucide-react";
import type { ContactOutcomeModal as Outcome } from "../api/types";

/** Modal shown when the agent leaves a customer without placing an order.
 *  The agent must either (1) pick one of the four outcomes — which logs a
 *  row and lets them leave — or (2) press "Opened by mistake", which lets
 *  them leave WITHOUT writing a row (the dropdown dot stays red). There's
 *  no close/cancel button and the backdrop isn't clickable — those exits
 *  would leave the visit in an undefined state. */

const OUTCOMES: {
  value: Outcome;
  label: string;
  description: string;
  tone: "red" | "amber";
}[] = [
  {
    value: "NO_ORDER",
    label: "No order needed",
    description: "Customer didn't need to order today.",
    tone: "amber",
  },
  {
    value: "CALLBACK",
    label: "Call back later",
    description: "Customer asked to be contacted again.",
    tone: "amber",
  },
  {
    value: "NO_ANSWER",
    label: "No answer / voicemail",
    description: "Call didn't connect.",
    tone: "amber",
  },
  {
    value: "REFUSED",
    label: "Refused / on stop / closed",
    description: "Hard stop — customer will not order.",
    tone: "red",
  },
];

export type ContactOutcomeSubmission = {
  outcome: Outcome;
  note: string;
  follow_up_date: string | null;
};

export function ContactOutcomeModal({
  customerLabel,
  isSaving,
  errorMessage,
  onSubmit,
  onMistake,
}: {
  customerLabel: string;
  isSaving: boolean;
  errorMessage: string | null;
  onSubmit: (sub: ContactOutcomeSubmission) => void;
  onMistake: () => void;
}) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [note, setNote] = useState("");
  const [followUp, setFollowUp] = useState("");

  // ESC does nothing — the agent must commit to one of the two paths.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") e.preventDefault();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  const canSubmit = outcome !== null && !isSaving;

  const handleSubmit = () => {
    if (!canSubmit) return;
    onSubmit({
      outcome: outcome!,
      note: note.trim(),
      follow_up_date: outcome === "CALLBACK" && followUp ? followUp : null,
    });
  };

  return (
    <div className="fixed inset-0 z-[60] bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-5">
        <h3 className="font-semibold text-gray-900 text-base mb-1">
          What happened with this customer?
        </h3>
        <p className="text-sm text-gray-600 mb-4 truncate">
          <strong>{customerLabel}</strong>
        </p>

        <div className="grid grid-cols-1 gap-2">
          {OUTCOMES.map((o) => {
            const active = outcome === o.value;
            const ring =
              o.tone === "red"
                ? active
                  ? "ring-red-500 bg-red-50"
                  : "hover:bg-red-50/60"
                : active
                ? "ring-amber-500 bg-amber-50"
                : "hover:bg-amber-50/60";
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => setOutcome(o.value)}
                className={`text-left border rounded-md p-2 transition ring-2 ring-transparent ${ring} ${
                  active ? "border-transparent" : "border-gray-200"
                }`}
              >
                <div className="font-medium text-sm text-gray-900">{o.label}</div>
                <div className="text-xs text-gray-500">{o.description}</div>
              </button>
            );
          })}
        </div>

        {outcome === "CALLBACK" && (
          <div className="mt-3">
            <label className="block text-xs text-gray-600 mb-1">
              Call back on (optional)
            </label>
            <input
              type="date"
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm"
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
            />
          </div>
        )}

        <div className="mt-3">
          <label className="block text-xs text-gray-600 mb-1">
            Note (optional, 200 chars)
          </label>
          <textarea
            className="w-full border border-gray-300 rounded px-2 py-1 text-sm resize-none"
            rows={2}
            maxLength={200}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Anything useful for the next call…"
          />
        </div>

        {errorMessage && (
          <div className="mt-3 flex items-start gap-2 p-2 bg-red-50 border border-red-200 rounded text-xs text-red-800">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{errorMessage}</span>
          </div>
        )}

        {outcome === null && !errorMessage && (
          <p className="mt-3 text-xs text-gray-500 italic">
            Pick one of the outcomes above to enable Save, or press
            "Opened by mistake" to exit without logging anything.
          </p>
        )}

        <div className="mt-4 flex items-center justify-between">
          <button
            type="button"
            onClick={onMistake}
            disabled={isSaving}
            className="inline-flex items-center text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50"
          >
            <XCircle className="w-4 h-4 mr-1" />
            Opened by mistake
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-blue-300"
          >
            {isSaving ? "Saving…" : "Save & continue"}
          </button>
        </div>
      </div>
    </div>
  );
}

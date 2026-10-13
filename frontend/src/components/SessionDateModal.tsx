import { useState, type FormEvent } from "react";
import { CalendarDays } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { nextBusinessDay } from "../utils/date";

/**
 * Prompts the rep to pick the transaction session date right after login.
 * The chosen date becomes the default order / due / invoice date on every
 * new sales order placed during this session. The rep can still change it
 * per-order on the preview sheet.
 */
export function SessionDateModal() {
  const { setSessionDate } = useAuth();
  const [date, setDate] = useState(() => nextBusinessDay());

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!date) return;
    setSessionDate(date);
  }

  return (
    <div className="h-screen flex items-center justify-center bg-gray-50 p-4">
      <form
        onSubmit={onSubmit}
        className="bg-white rounded border shadow-sm p-6 w-full max-w-sm flex flex-col gap-3"
      >
        <div className="flex items-center gap-2 mb-2">
          <CalendarDays className="w-5 h-5 text-blue-600" />
          <h1 className="text-base font-semibold text-gray-900">
            Transaction session date
          </h1>
        </div>
        <p className="text-xs text-gray-500 -mt-1 mb-1">
          Date that every sales order placed in this session will default to.
          You can still change the date per-order on the preview.
        </p>

        <label className="flex flex-col gap-1 text-xs text-gray-700">
          Session date
          <input
            type="date"
            required
            autoFocus
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-44 border border-gray-300 rounded px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          />
        </label>

        <button
          type="submit"
          disabled={!date}
          className="mt-1 bg-blue-600 text-white rounded px-3 py-2 text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          Continue
        </button>
      </form>
    </div>
  );
}

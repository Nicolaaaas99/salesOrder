/**
 * Next-business-day helper, used as the default for the transaction session
 * date and sales-order date fields. Dairy telesales call customers to place
 * orders for the next day's delivery; Fri rolls to Mon, Sat → Mon, Sun → Mon.
 *
 * Returned as `YYYY-MM-DD` in the user's LOCAL calendar (not UTC) so there's
 * no late-night-SAST drift. Does NOT account for public holidays.
 */
export function nextBusinessDay(): string {
  const d = new Date();
  const dow = d.getDay();   // 0 Sun, 1 Mon … 5 Fri, 6 Sat
  const addDays = dow === 5 ? 3 : dow === 6 ? 2 : 1;
  d.setDate(d.getDate() + addDays);
  return formatLocalDate(d);
}

/** Format a Date as `YYYY-MM-DD` in local calendar (no UTC conversion). */
export function formatLocalDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

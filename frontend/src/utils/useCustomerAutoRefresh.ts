import { useCallback, useEffect, useRef } from "react";
import { api } from "../api/client";
import type { CustomerDetail } from "../api/types";

const POLL_INTERVAL_MS = 5_000;

/**
 * Keep a `CustomerDetail` state fresh without clobbering the user's draft.
 *
 * The business reason: a sales rep hits an OVER LIMIT / OVER TERMS block, the
 * accounts team unblocks the customer in Evolution, and the rep expects the
 * app to notice. Previously they had to reselect the customer and lose any
 * in-progress draft.
 *
 * We refresh in three places:
 *   1. On window focus — covers the "alt-tab back from Evolution" case.
 *   2. Every 5 seconds while a customer is selected — covers the case where
 *      Evolution is on a second monitor and the rep never leaves the app.
 *   3. On demand via `refreshNow()` — call this before saving so a stale
 *      credit/terms check never lets a now-invalid order through.
 *
 * Only `customerDetail` is updated — the draft lines, dates, and every other
 * form field are untouched, so no work is lost.
 */
export function useCustomerAutoRefresh(
  account: string | null | undefined,
  onUpdated: (detail: CustomerDetail) => void,
) {
  // Stash the latest `onUpdated` in a ref so the effect doesn't re-run every
  // render just because the parent passed a new inline callback.
  const onUpdatedRef = useRef(onUpdated);
  useEffect(() => {
    onUpdatedRef.current = onUpdated;
  }, [onUpdated]);

  const inflightRef = useRef<Promise<CustomerDetail | null> | null>(null);

  // Fetch-and-coalesce. If a fetch is already in flight for this account,
  // reuse it rather than hammering the backend from focus + poll + save.
  const refresh = useCallback(async (): Promise<CustomerDetail | null> => {
    if (!account) return null;
    if (inflightRef.current) return inflightRef.current;
    const p = api
      .getCustomer(account)
      .then((d) => {
        onUpdatedRef.current(d);
        return d;
      })
      .catch(() => null)
      .finally(() => {
        inflightRef.current = null;
      });
    inflightRef.current = p;
    return p;
  }, [account]);

  useEffect(() => {
    if (!account) return;
    let cancelled = false;

    const safeRefresh = () => {
      if (cancelled) return;
      void refresh();
    };

    const onFocus = () => safeRefresh();
    const onVisibility = () => {
      if (document.visibilityState === "visible") safeRefresh();
    };

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    const interval = window.setInterval(safeRefresh, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearInterval(interval);
    };
  }, [account, refresh]);

  return { refreshNow: refresh };
}

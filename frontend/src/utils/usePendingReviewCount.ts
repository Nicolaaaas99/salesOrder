import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useFocusRefresh } from "./useFocusRefresh";

/** Fired after an import review is submitted, approved or rejected so the
 *  badge updates at once instead of waiting for the next poll. */
export const REVIEWS_CHANGED_EVENT = "import-reviews:changed";

export function notifyReviewsChanged() {
  window.dispatchEvent(new CustomEvent(REVIEWS_CHANGED_EVENT));
}

const REVIEW_COUNT_POLL_MS = 120_000;

/**
 * Number of imported orders waiting for a supervisor — the badge on the
 * Sales Order Entry tab and the Price review card. Supervisors / admins only
 * (agents never call it).
 *
 * PERFORMANCE.md: the query is a COUNT on the indexed status column of the
 * tiny _tb_SOImportReview table (~1 ms). It runs every 2 min while the tab is
 * visible, on window focus (throttled to 30 s), when the tab becomes visible
 * again, and on REVIEWS_CHANGED_EVENT — never more than one request at once.
 */
export function usePendingReviewCount(enabled: boolean): number {
  const [count, setCount] = useState(0);
  const inflight = useRef(false);

  const refresh = useCallback(() => {
    if (!enabled || inflight.current) return;
    inflight.current = true;
    api
      .getPendingReviewCount()
      .then(setCount)
      .catch(() => {})
      .finally(() => {
        inflight.current = false;
      });
  }, [enabled]);

  useFocusRefresh(refresh);

  useEffect(() => {
    if (!enabled) {
      setCount(0);
      return;
    }
    refresh();
    const poll = () => {
      if (document.visibilityState === "visible") refresh();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    const id = window.setInterval(poll, REVIEW_COUNT_POLL_MS);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener(REVIEWS_CHANGED_EVENT, refresh);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener(REVIEWS_CHANGED_EVENT, refresh);
    };
  }, [enabled, refresh]);

  return count;
}

import { useEffect, useRef } from "react";

/** Minimum gap between focus-triggered refreshes. Reps alt-tab between the
 *  app and Evolution all day; refetching on every single focus multiplies
 *  each query by (reps × alt-tabs). 30 s still catches "another rep changed
 *  something while I was away" without hammering SQL Server. */
export const FOCUS_REFRESH_MIN_GAP_MS = 30_000;

/**
 * Call `refresh` when the window regains focus — but at most once per
 * `minGapMs`. Use this instead of a raw `window.addEventListener("focus")`
 * for anything that hits the database (see PERFORMANCE.md).
 */
export function useFocusRefresh(refresh: () => void, minGapMs = FOCUS_REFRESH_MIN_GAP_MS) {
  const refreshRef = useRef(refresh);
  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    let last = Date.now();
    const onFocus = () => {
      const now = Date.now();
      if (now - last < minGapMs) return;
      last = now;
      refreshRef.current();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [minGapMs]);
}

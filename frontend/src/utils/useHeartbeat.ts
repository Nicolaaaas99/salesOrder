import { useEffect } from "react";
import { auth } from "../api/client";

const HEARTBEAT_MS = 60_000;

/** Tells the backend "this user still has the app open" so Users & Roles
 *  can show who is online. Every 60 s, only while the tab is visible, one
 *  request at a time. The endpoint touches no database (the backend just
 *  stamps the session's last-seen time in memory) — PERFORMANCE.md. */
export function useHeartbeat(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let inFlight = false;
    const beat = () => {
      if (inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      auth
        .heartbeat()
        .catch(() => {
          /* a 401 is handled by the shared http interceptor */
        })
        .finally(() => {
          inFlight = false;
        });
    };
    const id = window.setInterval(beat, HEARTBEAT_MS);
    // Back on the tab: show as online straight away.
    const onVisible = () => document.visibilityState === "visible" && beat();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled]);
}

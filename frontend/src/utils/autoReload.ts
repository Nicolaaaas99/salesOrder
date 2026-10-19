import { useEffect } from "react";

const VERSION_URL = "/version.json";
const POLL_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes

async function fetchServerVersion(): Promise<string | null> {
  try {
    // Explicit no-store + cache-busting query so even an aggressive
    // intermediate cache can't hand us a stale version file.
    const r = await fetch(`${VERSION_URL}?t=${Date.now()}`, {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!r.ok) return null;
    const data = (await r.json()) as { version?: string };
    return data?.version ?? null;
  } catch {
    return null;
  }
}

/**
 * Reload the SPA when a new build is deployed. On mount, on window focus,
 * and every POLL_INTERVAL_MS, we fetch `/version.json` and compare it to
 * the version baked into this bundle. If they differ, the page reloads so
 * the user picks up the new build without needing a manual hard-refresh.
 *
 * Mount this once near the top of the app (e.g. inside <AppShell />).
 */
export function useAutoReloadOnNewVersion() {
  useEffect(() => {
    let cancelled = false;
    let reloading = false;
    const baked = typeof __BUILD_VERSION__ === "string" ? __BUILD_VERSION__ : "";

    async function check() {
      if (cancelled || reloading) return;
      const server = await fetchServerVersion();
      if (!server || cancelled) return;
      if (server !== baked) {
        reloading = true;
        // location.reload() + the no-cache header on `index.html` is enough
        // for a modern browser to fetch the fresh shell and new hashed
        // bundles. Legacy `reload(true)` was deprecated and is a no-op.
        window.location.reload();
      }
    }

    check();

    const onFocus = () => void check();
    window.addEventListener("focus", onFocus);
    const interval = window.setInterval(check, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
      window.clearInterval(interval);
    };
  }, []);
}

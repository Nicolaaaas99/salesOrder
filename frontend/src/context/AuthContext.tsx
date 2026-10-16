import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { auth as authApi } from "../api/client";

type AuthState = {
  username: string | null;
  token: string | null;
  /** `YYYY-MM-DD` in LOCAL calendar. Null until the rep picks one at the
   *  start of a session. All sales-order date defaults use this. */
  sessionDate: string | null;
  /** The warehouse the logged-in telesales agent works out of (from
   *  _vw_SOAgentDefaultWhse). Pricing calls pass this through so the rep
   *  sees prices from their own DC. Null if the agent isn't in the view. */
  defaultWarehouseCode: string | null;
  /** Mirrors Evolution's OrdersDf.bForceExtOrderNum. When true, the UI blocks
   *  saving a sales order without an External Order number. */
  forceExtOrderNum: boolean;
};

type AuthContextValue = AuthState & {
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  setSessionDate: (date: string) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Persisted auth state. Token + username + sessionDate live in sessionStorage
 * so a reload keeps the user signed in, but closing the tab clears them. The
 * axios interceptor in api/client.ts reads the token on every request, and
 * emits an `auth:401` window event when the backend rejects a token — we
 * listen for it here to force a logout.
 *
 * `sessionDate` is cleared on every fresh login (so the rep is prompted to
 * pick a date at session start) but preserved across reloads.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(() => ({
    token: sessionStorage.getItem("auth_token"),
    username: sessionStorage.getItem("auth_username"),
    sessionDate: sessionStorage.getItem("auth_session_date"),
    defaultWarehouseCode: sessionStorage.getItem("auth_default_warehouse"),
    forceExtOrderNum: sessionStorage.getItem("auth_force_ext_order_num") === "1",
  }));

  const login = useCallback(async (username: string, password: string) => {
    const res = await authApi.login(username.trim(), password);
    sessionStorage.setItem("auth_token", res.token);
    sessionStorage.setItem("auth_username", res.username);
    // Fresh login = re-prompt for session date.
    sessionStorage.removeItem("auth_session_date");
    if (res.default_warehouse_code) {
      sessionStorage.setItem("auth_default_warehouse", res.default_warehouse_code);
    } else {
      sessionStorage.removeItem("auth_default_warehouse");
    }
    if (res.force_ext_order_num) {
      sessionStorage.setItem("auth_force_ext_order_num", "1");
    } else {
      sessionStorage.removeItem("auth_force_ext_order_num");
    }
    setState({
      token: res.token,
      username: res.username,
      sessionDate: null,
      defaultWarehouseCode: res.default_warehouse_code ?? null,
      forceExtOrderNum: !!res.force_ext_order_num,
    });
  }, []);

  const logout = useCallback(() => {
    // Fire-and-forget: we clear local state immediately regardless of whether
    // the server-side revoke succeeds (bad network shouldn't trap you logged in).
    authApi.logout().catch(() => {});
    sessionStorage.removeItem("auth_token");
    sessionStorage.removeItem("auth_username");
    sessionStorage.removeItem("auth_session_date");
    sessionStorage.removeItem("auth_default_warehouse");
    sessionStorage.removeItem("auth_force_ext_order_num");
    setState({
      token: null,
      username: null,
      sessionDate: null,
      defaultWarehouseCode: null,
      forceExtOrderNum: false,
    });
  }, []);

  const setSessionDate = useCallback((date: string) => {
    sessionStorage.setItem("auth_session_date", date);
    setState((prev) => ({ ...prev, sessionDate: date }));
  }, []);

  // On first mount with a cached token, re-fetch /auth/me so the per-session
  // tenant config (default warehouse, force-ext-order-num) stays fresh — e.g.
  // an admin flipping the Evolution setting while the rep's session is open
  // takes effect after the next page refresh instead of requiring a logout.
  useEffect(() => {
    if (!state.token) return;
    authApi
      .me()
      .then((me) => {
        if (me.default_warehouse_code) {
          sessionStorage.setItem("auth_default_warehouse", me.default_warehouse_code);
        } else {
          sessionStorage.removeItem("auth_default_warehouse");
        }
        if (me.force_ext_order_num) {
          sessionStorage.setItem("auth_force_ext_order_num", "1");
        } else {
          sessionStorage.removeItem("auth_force_ext_order_num");
        }
        setState((prev) => ({
          ...prev,
          defaultWarehouseCode: me.default_warehouse_code ?? null,
          forceExtOrderNum: !!me.force_ext_order_num,
        }));
      })
      .catch(() => {
        /* 401s already handled by the axios interceptor */
      });
    // Intentionally runs only on mount — we only want a single refresh at the
    // start of a hydrated session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onAuthFail = () =>
      setState({
        token: null,
        username: null,
        sessionDate: null,
        defaultWarehouseCode: null,
        forceExtOrderNum: false,
      });
    window.addEventListener("auth:401", onAuthFail);
    return () => window.removeEventListener("auth:401", onAuthFail);
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, login, logout, setSessionDate }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

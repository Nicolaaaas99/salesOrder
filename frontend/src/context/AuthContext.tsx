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
    setState({
      token: res.token,
      username: res.username,
      sessionDate: null,
      defaultWarehouseCode: res.default_warehouse_code ?? null,
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
    setState({
      token: null,
      username: null,
      sessionDate: null,
      defaultWarehouseCode: null,
    });
  }, []);

  const setSessionDate = useCallback((date: string) => {
    sessionStorage.setItem("auth_session_date", date);
    setState((prev) => ({ ...prev, sessionDate: date }));
  }, []);

  useEffect(() => {
    const onAuthFail = () =>
      setState({
        token: null,
        username: null,
        sessionDate: null,
        defaultWarehouseCode: null,
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

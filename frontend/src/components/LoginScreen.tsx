import { useState, type FormEvent } from "react";
import { LogIn } from "lucide-react";
import { useAuth } from "../context/AuthContext";

export function LoginScreen() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(detail ?? err?.message ?? "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="h-screen flex items-center justify-center bg-gray-50 p-4">
      <form
        onSubmit={onSubmit}
        className="bg-white rounded border shadow-sm p-6 w-full max-w-sm flex flex-col gap-3"
      >
        <div className="flex items-center gap-2 mb-2">
          <LogIn className="w-5 h-5 text-blue-600" />
          <h1 className="text-base font-semibold text-gray-900">
            Sign in to Sales Orders
          </h1>
        </div>
        <p className="text-xs text-gray-500 -mt-1 mb-1">
          Use your Evolution operator credentials.
        </p>

        <label className="flex flex-col gap-1 text-xs text-gray-700">
          Username
          <input
            type="text"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={submitting}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          />
        </label>

        <label className="flex flex-col gap-1 text-xs text-gray-700">
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          />
        </label>

        {error && (
          <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !username || !password}
          className="mt-1 bg-blue-600 text-white rounded px-3 py-2 text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

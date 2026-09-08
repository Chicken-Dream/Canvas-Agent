"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDevLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.devLogin(token.trim());
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-md space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-slate-800">Sign in</h1>
        <p className="mt-1 text-sm text-slate-500">Connect your University of Alberta Canvas account.</p>
      </div>

      <section className="rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="font-medium text-slate-800">Sign in with Canvas (OAuth)</h2>
        <p className="mt-1 text-sm text-slate-500">
          Recommended once this app has an approved UAlberta OAuth developer key.
        </p>
        <a
          href={api.oauthLoginUrl()}
          className="mt-3 inline-block rounded bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          Sign in with UAlberta Canvas
        </a>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="font-medium text-slate-800">Sign in with an Access Token (prototype)</h2>
        <p className="mt-1 text-sm text-slate-500">
          Until OAuth is approved, generate a Personal Access Token at{" "}
          <span className="font-mono text-xs">
            canvas.ualberta.ca → Account → Settings → New Access Token
          </span>{" "}
          and paste it below. It is sent once to identify you and stored so this app can call the Canvas
          API on your behalf.
        </p>
        <form onSubmit={handleDevLogin} className="mt-3 space-y-3">
          <input
            type="password"
            required
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Canvas access token"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={loading || !token.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </section>
    </div>
  );
}

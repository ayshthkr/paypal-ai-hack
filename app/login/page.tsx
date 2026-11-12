"use client";

import { API_WRITE_TIMEOUT_MS, clearReads } from "../lib/api";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import SiteHeader from "../site-header";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const response = await fetch("/api/auth/login", { method: "POST", credentials: "same-origin", signal: AbortSignal.timeout(API_WRITE_TIMEOUT_MS), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Login failed.");
      clearReads(true);
      router.push("/"); router.refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Login failed."); }
    finally { setBusy(false); }
  };

  return <div className="app-shell"><SiteHeader/><main className="main-wrap"><form className="auth-card" onSubmit={submit}><div className="eyebrow">Operator access</div><h1>Sign in</h1><p className="subheading">Access PayPal invoices and approve reminders.</p>{error && <div className="error-box" role="alert" style={{ marginTop: 16 }}>{error}</div>}<label className="field-label" htmlFor="username">Username</label><input className="input" id="username" autoComplete="username" required value={username} onChange={event => setUsername(event.target.value)}/><label className="field-label" htmlFor="password">Password</label><input className="input" id="password" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)}/><button className="btn primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></form></main></div>;
}

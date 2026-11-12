"use client";

import Link from "next/link";
import { API_WRITE_TIMEOUT_MS, clearReads } from "../lib/api";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

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

  return <div className="app-shell dashboard-app-shell operator-login-shell"><header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><nav className="top-actions" aria-label="Primary navigation"><Link href="/" className="back-link">Portfolio</Link><Link href="/operator" className="back-link">PayPal operator</Link><Link href="/login" className="back-link" aria-current="page">Operator login</Link><Link className="back-link" href="/offline-demo">Read-only fallback</Link></nav></header><main className="main-wrap operator-login-main"><div className="operator-login-aside"><div className="eyebrow">PayPal Sandbox workspace</div><h1>Keep every test payment in a safe loop.</h1><p>Sign in to verify recipient access, create a Sandbox invoice, and reconcile payment evidence.</p><ul><li>Allowlisted test recipients only</li><li>Sandbox invoice creation and sync</li><li>Recorded webhook verification</li></ul></div><form className="auth-card" onSubmit={submit}><div className="eyebrow">Operator access</div><h2>Sign in</h2><p className="subheading">Use your operator account to continue.</p>{error && <div className="error-box" role="alert" style={{ marginTop: 16 }}>{error}</div>}<label className="field-label" htmlFor="username">Username</label><input className="input" id="username" autoComplete="username" required value={username} onChange={event => setUsername(event.target.value)}/><label className="field-label" htmlFor="password">Password</label><input className="input" id="password" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)}/><button className="btn primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button><p className="fine-print" style={{ marginTop: 16 }}>Credentials are checked by the API. The session cookie is HttpOnly.</p></form></main></div>;
}

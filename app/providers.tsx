"use client";
import { createContext, useContext, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { SWRConfig } from "swr";
import { requestJson, RequestError } from "./lib/api";

const AuthContext = createContext({ authenticated: false });
function AuthProvider({ children }: { children: React.ReactNode }) {
  const { data } = useSWR("/api/auth/me", requestJson<{ authenticated: boolean }>, { revalidateOnFocus: false, revalidateIfStale: false, dedupingInterval: 60_000 });
  return <AuthContext.Provider value={{ authenticated: data?.authenticated || false }}>{children}</AuthContext.Provider>;
}
export const useAuth = () => useContext(AuthContext);
export function OperatorControls() {
  const router = useRouter();
  const { authenticated } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!authenticated) return <a className="btn" href="/login">Operator login</a>;
  const signOut = async () => {
    setBusy(true); setError("");
    try { await requestJson("/api/auth/logout", { method: "POST" }); router.push("/"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Sign out failed."); setBusy(false); }
  };
  return <span className="operator-status"><span>Authenticated · PayPal Sandbox</span><button className="btn" disabled={busy} onClick={() => void signOut()}>{busy ? "Signing out…" : "Sign out"}</button>{error && <span role="alert">{error}</span>}</span>;
}
export default function Providers({ children }: { children: React.ReactNode }) {
  return <SWRConfig value={{ fetcher: requestJson, dedupingInterval: 30_000, keepPreviousData: true, shouldRetryOnError: (error: Error) => !(error instanceof RequestError && error.status < 500), errorRetryCount: 0 }}><AuthProvider>{children}</AuthProvider></SWRConfig>;
}

import { mutate } from "swr";

export class RequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
const reads = new Map<string, { expires: number; promise: Promise<unknown> }>();
let demoSessionReady: Promise<void> | null = null;
export function ensureDemoSession(): Promise<void> {
  if (typeof document === "undefined" || document.cookie.split(";").some(value => value.trim().startsWith("latepayer_demo_session="))) {
    return Promise.resolve();
  }
  if (!demoSessionReady) {
    demoSessionReady = fetch("/api/demo/session", { cache: "no-store", credentials: "same-origin" })
      .then(response => { if (!response.ok) throw new Error("Could not start an isolated demo session."); })
      .catch(() => { demoSessionReady = null; });
  }
  return demoSessionReady;
}
export function clearReads(authChanged = false) { reads.clear(); void mutate(key => authChanged || key !== "/api/auth/me"); }
export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  await ensureDemoSession();
  const reading = !init?.method || init.method === "GET";
  const cached = reads.get(url);
  if (reading && cached && cached.expires > Date.now()) return cached.promise as Promise<T>;
  const promise = (async () => {
    const response = await fetch(url, { ...init, signal: init?.signal || AbortSignal.timeout(10_000), cache: "no-store", credentials: "same-origin", headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (!reading && response.status === 409) clearReads();
      throw new RequestError(value.detail || `Request failed (${response.status})`, response.status);
    }
    if (!reading) clearReads();
    return value as T;
  })();
  if (reading) {
    reads.set(url, { expires: Date.now() + 30_000, promise });
    if (reads.size > 150) reads.delete(reads.keys().next().value!);
    promise.catch(() => reads.delete(url));
  }
  return promise;
}

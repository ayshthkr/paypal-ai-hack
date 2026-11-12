import { mutate } from "swr";

export class RequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export function readFailureMessage(cause: unknown, subject: string) {
  if (cause instanceof RequestError) {
    if (cause.status === 401) return `Sign in as an operator to load ${subject}.`;
    if (cause.status === 404) return `${subject} could not be found.`;
    if (cause.status >= 500) return `The service could not load ${subject}. Retry the read or open the packaged fallback.`;
    return cause.message;
  }
  const message = cause instanceof Error ? cause.message : "";
  if (/timeout|timed out|aborted|network|fetch/i.test(message)) return `The service did not respond while loading ${subject}. Retry the read or open the packaged fallback.`;
  return message || `Could not load ${subject}. Retry the read or open the packaged fallback.`;
}
export function operationFailureMessage(cause: unknown, subject: string) {
  if (cause instanceof RequestError && cause.status >= 400 && cause.status < 500) return cause.message;
  return `${subject} result could not be confirmed. Check the saved record before retrying.`;
}
const reads = new Map<string, { expires: number; promise: Promise<unknown> }>();
let demoSessionReady: Promise<void> | null = null;
export const API_READ_TIMEOUT_MS = 30_000;
export const API_WRITE_TIMEOUT_MS = 30_000;
export const AI_DRAFT_TIMEOUT_MS = 45_000;
// Leave a small margin over the API's observed ~20s wake-up reads while keeping
// seven route reads (five pages and two named stories) inside the 3-minute tour.
export const TOUR_READ_TIMEOUT_MS = 23_000;
export function currentReadTimeoutMs() {
  return typeof window !== "undefined" && (window.location.pathname === "/tour" || new URLSearchParams(window.location.search).get("tour") === "1")
    ? TOUR_READ_TIMEOUT_MS
    : API_READ_TIMEOUT_MS;
}
export function ensureDemoSession(): Promise<void> {
  if (typeof document === "undefined" || document.cookie.split(";").some(value => value.trim().startsWith("latepayer_demo_session="))) {
    return Promise.resolve();
  }
  if (!demoSessionReady) {
    demoSessionReady = fetch("/api/demo/session", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(Math.min(API_WRITE_TIMEOUT_MS, currentReadTimeoutMs())) })
      .then(response => { if (!response.ok) throw new Error("Could not start an isolated demo session."); })
      .catch(() => { demoSessionReady = null; });
  }
  return demoSessionReady;
}
export function clearReads(authChanged = false) { reads.clear(); void mutate(key => authChanged || key !== "/api/auth/me"); }
export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  await ensureDemoSession();
  const reading = !init?.method || init.method === "GET";
  const timeout = /\/invoices\/[^/]+\/draft(?:\?|$)/.test(url) ? AI_DRAFT_TIMEOUT_MS : reading ? currentReadTimeoutMs() : API_WRITE_TIMEOUT_MS;
  const cached = reads.get(url);
  if (reading && cached && cached.expires > Date.now()) return cached.promise as Promise<T>;
  const promise = (async () => {
    const signal = init?.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout);
    const response = await fetch(url, { ...init, signal, cache: "no-store", credentials: "same-origin", headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
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

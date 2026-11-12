"use client";

import { AllCommunityModule, ModuleRegistry, themeQuartz } from "ag-grid-community";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { AgGridProvider, AgGridReact } from "ag-grid-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { requestJson as getJson, clearReads, ensureDemoSession, RequestError, API_WRITE_TIMEOUT_MS, currentReadTimeoutMs } from "./lib/api";
import { decisionLabel, signalLabel, statusLabel } from "./lib/labels";
import { OperatorControls, useAuth } from "./providers";
import ConfirmDialog from "./confirm-dialog";

ModuleRegistry.registerModules([AllCommunityModule]);

type Drift = {
  signal: string; signal_type: string; available: boolean; settled_count: number; message: string;
  severity_score: number; signal_strength: number; recent_median_delay_days?: number | null;
  usual_delay_days?: number | null; days_since_last_invoice?: number | null; cadence_days?: number | null;
  activity_probability?: number | null; money_at_risk_by_currency?: Record<string, number>;
};
type Forecast = {
  quantiles: Record<string, { days: number; date: string } | null>;
  probabilities: Record<string, number>; model: string; display_label?: string; limited_history?: boolean; scored_at?: string;
} | null;
type InvoiceRow = {
  id: string; invoice_number: string; client_id: string; client_name: string; due_date: string;
  days_to_due: number; outstanding: string; currency: string; status: string; source: string;
  forecast: Forecast; drift: Drift;
  disputed?: boolean;
  cash_week_contribution?: { amount: string; probability: string };
  decision: { action: string; tone: string; reason: string; expected_cash_gain: number; expected_cash_gain_currency: string | null; daily_rank?: number };
};
type AgingBucket = { count: number; amounts: Record<string, string> };
type InvoiceResult = {
  items: InvoiceRow[]; count: number; page: number; page_size: number; page_count: number; view: string;
  view_counts: Record<string, number>; currency_totals: Record<string, string>;
  aging_buckets: Record<string, AgingBucket>;
  expected_cash_by_week: { week_start: string; currency: string; amount: string; invoice_count: number }[];
  cash_horizon: Record<string, AgingBucket>;
  open_balance_reconciliation?: { count: number; amounts: Record<string, string> };
  top_late_scenario: { assumption: string; selection_rule?: string; client_count: number; clients: { id: string; name: string; late_invoice_count: number }[]; invoice_count: number; baseline_by_currency?: { currency: string; amount: string }[]; cash_by_week: { week_start: string; currency: string; amount: string; invoice_count: number; client_contributions?: { client_id: string; client_name: string; invoice_count: number; assumed_gross: string }[] }[] };
  synthetic_demo: boolean; models: string[]; ready_for_review_count: number; daily_suggestion_cap: number; drifting_client_count: number;
  habitually_late_client_count?: number;
  drifting_signal_counts?: Record<string, number>;
  as_of: string; as_of_max: string; model_timestamp?: string | null; client_signals?: ClientRow[];
};
type ScenarioWeekEntry = InvoiceResult["top_late_scenario"]["cash_by_week"][number];
type ClientRow = { id: string; name: string; drift: Drift };
type MorningBrief = {
  as_of: string; week_end: string;
  expected_cash_this_week: { currency: string; amount: string; invoice_count: number; invoice_ids?: string[] }[];
  top_candidates: { invoice_id: string; invoice_number: string; client_id: string; client_name: string; currency: string; outstanding: string; action: string; reason: string; expected_cash_gain: number }[];
  clients_slowing_down: { client_id: string; client_name: string; signal_type: string; message: string; severity_score: number }[];
};
type AskInvoice = { id: string; invoice_number: string };
type AskResult = { intent: string; answer: string; data: unknown; grounded_in?: string; as_of?: string };
type SyncStatus = {
  last_run: null | { started_at: string; completed_at: string | null; status: string; created: number; updated: number; event_refreshes: number; retry_failures: number; errors: string[] };
  webhook_failures: number;
};
type PaypalRecipient = { id: string; name: string; email: string };
type DemoStory = { key: string; title: string; summary: string; href: string };

const modules = [AllCommunityModule];
const gridTheme = themeQuartz.withParams({ accentColor: "#155eef", backgroundColor: "#ffffff", borderColor: "#e6ebee", fontFamily: "Arial, sans-serif", fontSize: "12px", headerBackgroundColor: "#f7f9fa", headerFontWeight: 700, rowHoverColor: "#f5f8fc" });
const viewTabs = [
  ["open", "Open"], ["due_this_week", "Due this week"], ["overdue", "Overdue"], ["paid", "Paid"], ["all", "All"],
] as const;
const columnChoices = [
  ["invoice_number", "Invoice"], ["client_name", "Client"], ["outstanding", "Balance"], ["due_date", "Due"],
  ["status", "Status"], ["p50", "Predicted"], ["window", "p10–p90 range"], ["p14", "Paid in 14d"],
  ["drift", "Client drift"], ["action", "Next action"], ["expected_cash", "Assumed 14-day cash uplift · 10% scenario"],
] as const;
const currencyOrder = ["EUR", "GBP", "USD"];
const defaultColumns = columnChoices.map(([id]) => id).filter(id => !["status", "window", "expected_cash"].includes(id));
const MAX_API_WAKE_RETRIES = 30;
const API_WAKE_RETRY_DELAY_MS = 2500;
const DEMO_FX_TO_USD: Record<string, number> = { USD: 1, EUR: 1.08, GBP: 1.27, CAD: 0.74 };


function SearchInput({ query, onDebouncedQuery }: { query: string; onDebouncedQuery: (value: string) => void }) {
  const [draft, setDraft] = useState(query);
  useEffect(() => {
    if (draft === query) return;
    const timer = window.setTimeout(() => onDebouncedQuery(draft), 350);
    return () => window.clearTimeout(timer);
  }, [draft, query, onDebouncedQuery]);
  return <input className="search" aria-label="Search invoices" placeholder="Search client, invoice, status" value={draft} onChange={event => setDraft(event.target.value)} />;
}

function TimeTravelControl({ currentAsOf, maxAsOf, resetBusy, onCommit, onReset }: { currentAsOf: string; maxAsOf: string; resetBusy: boolean; onCommit: (value: string) => Promise<boolean>; onReset: () => void }) {
  const [selectedDate, setSelectedDate] = useState(currentAsOf || maxAsOf);
  const [loadingDate, setLoadingDate] = useState(false);
  const currentOffset = Math.round((new Date(`${selectedDate}T00:00:00Z`).getTime() - new Date(`${maxAsOf}T00:00:00Z`).getTime()) / 86_400_000);
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const date = new Date(`${maxAsOf}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + Number(event.target.value));
    setSelectedDate(date.toISOString().slice(0, 10));
  };
  const commit = async () => {
    setLoadingDate(true);
    const succeeded = await onCommit(selectedDate);
    if (!succeeded) setSelectedDate(currentAsOf || maxAsOf);
    setLoadingDate(false);
  };
  return <section className="time-travel-panel"><div><strong>Demo snapshot date</strong><span>{loadingDate ? "Loading the selected portfolio snapshot…" : "Move the snapshot date back up to 60 days to inspect an earlier portfolio state."}</span></div><label><span>{new Date(`${selectedDate}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</span><input aria-label="Demo snapshot date, days before latest date" type="range" min={-60} max={0} value={Math.max(-60, Math.min(0, currentOffset))} onChange={onChange} onPointerUp={() => void commit()} onKeyUp={() => void commit()} disabled={loadingDate} /><button className="btn" disabled={resetBusy || loadingDate} onClick={onReset}>{resetBusy ? "Resetting…" : "Reset demo"}</button></label></section>;
}

function money(currency: string, value: number | string) {
  return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function isoPlusDays(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function calendarWeekBounds(asOf: string, weekStart: string) {
  const horizonStart = isoPlusDays(asOf, 1);
  const horizonEnd = isoPlusDays(asOf, 56);
  const calendarEnd = isoPlusDays(weekStart, 6);
  const visibleStart = weekStart < horizonStart ? horizonStart : weekStart;
  const visibleEnd = calendarEnd > horizonEnd ? horizonEnd : calendarEnd;
  return {
    visibleStart, visibleEnd,
    partial: visibleStart !== weekStart || visibleEnd !== calendarEnd,
    calendarStart: weekStart, calendarEnd,
  };
}

function shortIsoDate(value: string) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" });
}

function DemoStoriesMenu() {
  const [stories, setStories] = useState<DemoStory[] | null>(null);
  const [error, setError] = useState("");
  const load = async () => {
    if (stories) return;
    try {
      const result = await getJson<{ items: DemoStory[] }>("/api/demo-stories");
      setStories(result.items);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Demo stories are unavailable.");
    }
  };
  return <details className="column-chooser" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>Demo stories</summary>
    <div className="column-menu demo-story-menu">
      {stories?.map(story => <Link href={story.href} key={story.key}><strong>{story.title}</strong><span>{story.summary}</span></Link>)}
      {!stories && !error && <span>Loading demo stories…</span>}
      {error && <span role="alert">{error} <button className="btn" onClick={() => { setStories(null); setError(""); void load(); }}>Retry</button></span>}
    </div>
  </details>;
}

export default function PortfolioClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchKey = searchParams.toString();
  const params = useMemo(() => new URLSearchParams(searchKey), [searchKey]);
  const isTourRoute = params.get("tour") === "1";
  const currentView = params.get("view") || "open";
  const currentPage = Math.max(1, Number(params.get("page") || "1"));
  const pageSize = Number(params.get("page_size") || "25");
  const currentSort = params.get("sort") || "due";
  const currentDirection = params.get("direction") || "asc";
  const selectedClient = params.get("client_id") || "";
  const selectedAge = params.get("age_bucket") || "";
  const actionOnly = params.get("action_only") === "1";
  const driftingOnly = params.get("drifting_only") === "1";
  const [clientSearch, setClientSearch] = useState("");
  const [clientOptionsOpen, setClientOptionsOpen] = useState(false);
  const [clientOptionsLoading, setClientOptionsLoading] = useState(false);
  const clientPickerRef = useRef<HTMLDivElement | null>(null);
  const urlQuery = params.get("q") || "";
  const hasAggregateFilters = Boolean(urlQuery.trim() || selectedClient || params.get("statuses") || params.get("due_from") || params.get("due_to"));
  const aggregateScopeLabel = hasAggregateFilters ? "Filtered" : "Portfolio-wide";
  const detailHref = useCallback((id: string, query = searchKey) => `/invoices/${encodeURIComponent(id)}${query ? `?${query}` : ""}`, [searchKey]);

  const [data, setData] = useState<InvoiceResult | null>(null);
  const [appliedSearchKey, setAppliedSearchKey] = useState("");
  const [usdMode, setUsdMode] = useState(false);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [visibleColumns, setVisibleColumns] = useState<string[]>(defaultColumns);
  const [columnsReady, setColumnsReady] = useState(false);
  const [exportState, setExportState] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fallbackVisible, setFallbackVisible] = useState(false);
  const [wakeRetryNonce, setWakeRetryNonce] = useState(0);
  const { authenticated } = useAuth();
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [portfolioRefreshedAt, setPortfolioRefreshedAt] = useState<string | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [paypalRecipients, setPaypalRecipients] = useState<PaypalRecipient[]>([]);
  const [newInvoiceClient, setNewInvoiceClient] = useState("");
  const [newInvoiceAmount, setNewInvoiceAmount] = useState("");
  const [newInvoiceTerm, setNewInvoiceTerm] = useState("NET_30");
  const [newInvoicePartial, setNewInvoicePartial] = useState(false);
  const [creatingInvoice, setCreatingInvoice] = useState(false);
  const [createdInvoice, setCreatedInvoice] = useState<{ id: string; number: string } | null>(null);
  const createInvoiceKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [briefLoading, setBriefLoading] = useState(false);
  const [askQuestion, setAskQuestion] = useState("");
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [askLoading, setAskLoading] = useState(false);
  const refreshInFlight = useRef(new Map<string, Promise<void>>());
  const wakeRetries = useRef(new Map<string, number>());
  const latestQuery = useRef(searchKey);

  const updateUrl = useCallback((updates: Record<string, string | null>, mode: "push" | "replace" = "push", resetPage = true) => {
    const next = new URLSearchParams(searchKey);
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    if (resetPage && !("page" in updates)) next.set("page", "1");
    const query = next.toString();
    const href = query ? `/?${query}` : "/";
    if ("age_bucket" in updates || "predicted_week" in updates) document.getElementById("invoice-grid")?.scrollIntoView({ behavior: "smooth" });
    if (mode === "push") router.push(href, { scroll: false });
    else router.replace(href, { scroll: false });
  }, [router, searchKey]);

  const refresh = useCallback(async () => {
    setLoading(true);
    const apiParams = new URLSearchParams(searchKey);
    if (!apiParams.has("view")) apiParams.set("view", "open");
    if (!apiParams.has("page")) apiParams.set("page", "1");
    if (!apiParams.has("page_size")) apiParams.set("page_size", "25");
    if (!apiParams.has("sort")) apiParams.set("sort", "due");
    const url = `/api/portfolio?${apiParams.toString()}`;
    const existing = refreshInFlight.current.get(url);
    if (existing) return existing;
    const controller = new AbortController();
    const requestInit = { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)]) };
    const pending = Promise.all([
      getJson<InvoiceResult>(url, requestInit),
    ]).then(async ([invoices]) => {
      if (latestQuery.current !== searchKey) return;
      let operatorSync: SyncStatus | null = null;
      let recipients: PaypalRecipient[] = [];
      if (authenticated) {
        const [syncResult, recipientResult] = await Promise.all([
          getJson<SyncStatus>("/api/sync/status").catch(() => null),
          getJson<{ items: PaypalRecipient[] }>("/api/paypal/recipients").catch(() => null),
        ]);
        operatorSync = syncResult;
        recipients = recipientResult?.items || [];
      }
      if (latestQuery.current !== searchKey) return;
      setData(invoices);
      setPortfolioRefreshedAt(new Date().toISOString());
      setAppliedSearchKey(searchKey);
      setSyncStatus(operatorSync); setPaypalRecipients(recipients); setError(""); setFallbackVisible(false); wakeRetries.current.delete(searchKey);
    }).catch(cause => {
      controller.abort();
      if (latestQuery.current !== searchKey) return;
      const message = cause instanceof Error ? cause.message : "Could not load invoices.";
      const retries = wakeRetries.current.get(searchKey) || 0;
      const transientApiFailure = (cause instanceof RequestError && cause.status >= 500)
        || /unavailable|502|timeout|timed out|aborted/i.test(message);
      const retryLimit = isTourRoute ? 0 : MAX_API_WAKE_RETRIES;
      if (transientApiFailure && retries < retryLimit) {
        wakeRetries.current.set(searchKey, retries + 1);
        setError(`The API is waking up. Retrying (${retries + 1}/${MAX_API_WAKE_RETRIES})…`);
      } else if (transientApiFailure) {
        setError("Portfolio refresh is still unavailable after service checks. Select Retry or open the read-only fallback.");
        setFallbackVisible(true);
      } else setError(message);
    }).finally(() => {
      refreshInFlight.current.delete(url);
      if (latestQuery.current === searchKey) setLoading(false);
    });
    refreshInFlight.current.set(url, pending);
    return pending;
  }, [searchKey, authenticated, isTourRoute, setLoading, setData, setPortfolioRefreshedAt, setAppliedSearchKey, setSyncStatus, setPaypalRecipients, setError, setFallbackVisible]);

  useEffect(() => {
    if (!clientOptionsOpen) return;
    let active = true;
    const timer = window.setTimeout(() => {
      setClientOptionsLoading(true);
      setClients([]);
      void getJson<{ items: ClientRow[] }>(`/api/clients?options_only=true&q=${encodeURIComponent(clientSearch)}`).then(value => { if (active) setClients(value.items); }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "Client search failed"); }).finally(() => { if (active) setClientOptionsLoading(false); });
    }, 200);
    return () => { active = false; window.clearTimeout(timer); };
  }, [clientOptionsOpen, clientSearch]);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => { if (!clientPickerRef.current?.contains(event.target as Node)) setClientOptionsOpen(false); };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, []);
  useEffect(() => { latestQuery.current = searchKey; }, [searchKey]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const saved = JSON.parse(localStorage.getItem("latepayer-visible-columns") || "null");
        if (Array.isArray(saved)) setVisibleColumns(saved.filter((id): id is string => typeof id === "string" && columnChoices.some(([column]) => column === id)));
      } catch { /* An invalid preference falls back to the default columns. */ }
      setColumnsReady(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  useEffect(() => { if (columnsReady) localStorage.setItem("latepayer-visible-columns", JSON.stringify(visibleColumns)); }, [columnsReady, visibleColumns]);
  useEffect(() => {
    const savedScroll = sessionStorage.getItem("latepayer-portfolio-scroll");
    if (savedScroll) { sessionStorage.removeItem("latepayer-portfolio-scroll"); window.setTimeout(() => window.scrollTo({ top: Number(savedScroll), behavior: "instant" }), 0); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { setLoading(true); void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useEffect(() => {
    if (!error.startsWith("The API is waking up.")) return;
    const timer = window.setTimeout(() => setWakeRetryNonce(value => value + 1), API_WAKE_RETRY_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [error]);
  useEffect(() => {
    if (wakeRetryNonce === 0) return;
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh, wakeRetryNonce]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  useEffect(() => { const focus = () => { if (document.visibilityState === "visible") void refresh(); }; window.addEventListener("focus", focus); return () => window.removeEventListener("focus", focus); }, [refresh]);
  useEffect(() => { const changed = (event: StorageEvent) => { if (event.key === "latepayer-demo-payment-updated") void refresh(); }; window.addEventListener("storage", changed); return () => window.removeEventListener("storage", changed); }, [refresh]);
  const sync = async () => {
    setBusy(true); setError("");
    try {
      const result = await getJson<{ errors?: string[]; created?: number; updated?: number }>("/api/sync", { method: "POST" });
      setLastSync(new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" }));
      await refresh();
      if (result.errors?.length) setError(`Sync completed with ${result.errors.length} invoice error(s).`);
      else setError(`Sync complete · ${result.created || 0} imported, ${result.updated || 0} updated.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "PayPal sync failed."); }
    finally { setBusy(false); }
  };

  const resetDemo = async () => {
    setResetBusy(true); setError("");
    let resetConfirmed = false;
    try {
      clearReads();
      const reset = await fetch("/api/demo/session/reset", { method: "POST", cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(API_WRITE_TIMEOUT_MS) });
      if (!reset.ok) throw new Error("Could not reset this browser's synthetic demo session.");
      resetConfirmed = true;
      for (let index = sessionStorage.length - 1; index >= 0; index -= 1) {
        const key = sessionStorage.key(index) || "";
        if (key.startsWith("latepayer-draft:") || key.startsWith("latepayer-collection:") || key.startsWith("latepayer-demo-payment:") || ["latepayer-tour-stop", "latepayer-tour-active", "latepayer-portfolio-scroll"].includes(key)) sessionStorage.removeItem(key);
      }
      const next = new URLSearchParams(searchKey);
      next.delete("as_of"); next.delete("action_only"); next.delete("drifting_only"); next.delete("age_bucket");
      next.set("view", "open"); next.set("page", "1");
      const query = next.toString();
      router.replace(query ? `/?${query}` : "/", { scroll: false });
      const [invoices, clientData] = await Promise.all([
        getJson<InvoiceResult>(`/api/invoices?${query}`), getJson<{ items: ClientRow[] }>("/api/clients"),
      ]);
      setData(invoices); setClients(clientData.items);
    } catch (cause) {
      setError(resetConfirmed
        ? "Demo session reset completed, but the portfolio refresh failed. Retry the read; the session reset is already saved."
        : `Demo session reset could not be confirmed. Check the current session before retrying. ${cause instanceof Error ? cause.message : ""}`);
    }
    finally { setResetBusy(false); }
  };

  const createInvoice = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newInvoiceClient || !newInvoiceAmount) return;
    setCreatingInvoice(true); setError(""); setCreatedInvoice(null);
    const invoiceRequest = { client_id: newInvoiceClient, amount: newInvoiceAmount, payment_term_type: newInvoiceTerm, partial_payment_allowed: newInvoicePartial };
    const fingerprint = JSON.stringify(invoiceRequest);
    let pending = createInvoiceKey.current;
    if (!pending) {
      try { pending = JSON.parse(sessionStorage.getItem("latepayer-sandbox-invoice-create") || "null") as typeof pending; }
      catch { pending = null; }
    }
    if (!pending || pending.fingerprint !== fingerprint) {
      pending = { fingerprint, key: crypto.randomUUID() };
      sessionStorage.setItem("latepayer-sandbox-invoice-create", JSON.stringify(pending));
    }
    createInvoiceKey.current = pending;
    try {
      const result = await getJson<{ invoice_id: string; invoice_number: string; status: string }>("/api/paypal/invoices", {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": pending.key },
        body: JSON.stringify(invoiceRequest),
      });
      sessionStorage.removeItem("latepayer-sandbox-invoice-create");
      createInvoiceKey.current = null;
      setCreatedInvoice({ id: result.invoice_id, number: result.invoice_number });
      setNewInvoiceAmount(""); setNewInvoicePartial(false);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create Sandbox invoice."); }
    finally { setCreatingInvoice(false); }
  };

  const openBrief = async () => {
    if (briefOpen) { setBriefOpen(false); return; }
    setBriefOpen(true);
    if (briefLoading) return;
    setBriefLoading(true);
    try { setBrief(await getJson<MorningBrief>("/api/facts/morning-brief")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load the morning brief."); }
    finally { setBriefLoading(false); }
  };

  const commitDemoClock = async (value: string): Promise<boolean> => {
    if (!data) return false;
    const targetParams = new URLSearchParams(searchKey);
    if (value === data.as_of_max) targetParams.delete("as_of");
    else targetParams.set("as_of", value);
    const query = targetParams.toString();
    try {
      const snapshot = await getJson<InvoiceResult>(`/api/portfolio?${query || "view=open&page=1&page_size=25"}`,
        { signal: AbortSignal.timeout(30_000) });
      setData(snapshot);
      setError("");
      router.replace(query ? `/?${query}` : "/", { scroll: false });
      return true;
    } catch (cause) {
      setError(`Could not load the ${value} portfolio snapshot. Showing the last successful date, ${data.as_of}. ${cause instanceof Error ? cause.message : "Retry to try again."}`);
      return false;
    }
  };

  const ask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!askQuestion.trim()) return;
    setAskLoading(true); setAskResult(null); setError("");
    try {
      await ensureDemoSession();
      const response = await fetch("/api/ask", { method: "POST", cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(API_WRITE_TIMEOUT_MS),
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: askQuestion }) });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      setAskResult(value as AskResult);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Receivables question failed."); }
    finally { setAskLoading(false); }
  };

  const baseColumns = useMemo<ColDef<InvoiceRow>[]>(() => [
    { colId: "invoice_number", field: "invoice_number", headerName: "Invoice", pinned: "left", minWidth: 140, flex: .8,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <Link className="table-link" onClick={() => sessionStorage.setItem("latepayer-portfolio-scroll", String(window.scrollY))} onMouseEnter={() => { router.prefetch(detailHref(cell.data?.id || "")); void getJson(`/api/invoices/${encodeURIComponent(cell.data?.id || "")}`).catch(() => {}); }} href={detailHref(cell.data?.id || "")}>{cell.value}</Link> },
    { colId: "client_name", field: "client_name", headerName: "Client", pinned: "left", minWidth: 160, flex: 1.1,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <Link className="table-link" onClick={() => sessionStorage.setItem("latepayer-portfolio-scroll", String(window.scrollY))} onMouseEnter={() => { void getJson(`/api/facts/clients/${encodeURIComponent(cell.data?.client_id || "")}`).catch(() => {}); }} href={`/clients/${encodeURIComponent(cell.data?.client_id || "")}?return_to=${encodeURIComponent(searchKey)}`}>{cell.value}</Link> },
    { colId: "outstanding", field: "outstanding", headerName: "Balance", minWidth: 125,
      valueFormatter: cell => cell.data ? money(cell.data.currency, cell.value) : "" },
    { colId: "due_date", field: "due_date", headerName: "Due", minWidth: 115,
      valueFormatter: cell => cell.value ? new Date(`${cell.value}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" }) : "" },
    { colId: "status", field: "status", headerName: "Status", minWidth: 125,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <span className={`status-pill ${cell.data?.disputed ? "disputed" : String(cell.value).toLowerCase()}`}>{cell.data ? statusLabel(cell.data.status, Boolean(cell.data.disputed)) : "—"}</span> },
    { colId: "p50", headerName: "P50 full settlement", minWidth: 185,
      valueGetter: cell => cell.data?.forecast?.quantiles.p50?.date || null,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => {
        const forecast = cell.data?.forecast;
        if (!forecast) return "—";
        const date = cell.value ? new Date(`${cell.value}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" }) : "Beyond 180 days";
        const confidence = [forecast.display_label, forecast.limited_history ? "Limited history" : null].filter(Boolean).join(" · ");
        return <span className="forecast-cell"><span>{date}</span>{confidence && <small className="fine-print">{confidence}</small>}</span>;
      } },
    { colId: "window", headerName: "p10–p90 range", minWidth: 165,
      valueGetter: cell => cell.data?.forecast ? { p10: cell.data.forecast.quantiles.p10?.date, p90: cell.data.forecast.quantiles.p90?.date } : null,
      valueFormatter: cell => {
        if (!cell.value) return "—";
        const fmt = (value: string) => new Date(`${value}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" });
        return cell.value.p10 ? `${fmt(cell.value.p10)} – ${cell.value.p90 ? fmt(cell.value.p90) : "beyond horizon"}` : "Beyond horizon";
      } },
    { colId: "p14", headerName: "Paid in 14d", minWidth: 110,
      valueGetter: cell => cell.data?.forecast?.probabilities["14d"],
      valueFormatter: cell => cell.value == null ? "—" : cell.value > 0 && cell.value < 0.01 ? "<1%" : `${Math.round(cell.value * 100)}%` },
    { colId: "drift", headerName: "Client drift", minWidth: 135,
      valueGetter: cell => cell.data?.drift?.signal_type,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <span className={`drift-badge ${cell.value === "stable" ? "stable" : ""}`}>{String(cell.value || "—").replaceAll("_", " ")}</span> },
    { colId: "action", headerName: "Next action", minWidth: 145,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <span className={`status-pill decision-pill ${cell.data?.decision.action}`}>{cell.data ? decisionLabel(cell.data.decision.action, cell.data.status, Boolean(cell.data.disputed)) : "—"}</span> },
    { colId: "expected_cash", headerName: "Assumed 14-day cash uplift · 10% scenario", minWidth: 245,
      valueGetter: cell => cell.data?.decision.expected_cash_gain,
      valueFormatter: cell => cell.data?.decision.expected_cash_gain_currency ? money(cell.data.decision.expected_cash_gain_currency, cell.value || 0) : "—" },
    { colId: "open", headerName: "Record", pinned: "right", minWidth: 88, maxWidth: 95,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <Link className="btn row-open-action" href={detailHref(cell.data?.id || "")}>Open</Link> },
  ], [detailHref, router, searchKey]);
  const columns = useMemo(() => baseColumns.map(column => ({ ...column, hide: column.colId === "open" ? false : !visibleColumns.includes(column.colId || column.field || "") })), [baseColumns, visibleColumns]);
  const rowData = useMemo(() => data?.items || [], [data]);
  const drifting = data?.client_signals || [];
  const driftCounts = {
    payingLater: data?.drifting_signal_counts?.paying_later ?? drifting.filter(client => client.drift.signal_type === "paying_later").length,
    laterThanUsual: data?.drifting_signal_counts?.later_than_usual ?? drifting.filter(client => client.drift.signal_type === "later_than_usual").length,
    goneQuiet: data?.drifting_signal_counts?.gone_quiet ?? drifting.filter(client => client.drift.signal_type === "gone_quiet").length,
    habituallyLate: data?.habitually_late_client_count ?? 0,
  };
  const pageCount = data?.page_count || 1;
  const formatAmounts = (amounts: Record<string, string | number>) => {
    const entries = Object.entries(amounts).sort(([a], [b]) => (currencyOrder.indexOf(a) < 0 ? 99 : currencyOrder.indexOf(a)) - (currencyOrder.indexOf(b) < 0 ? 99 : currencyOrder.indexOf(b)) || a.localeCompare(b));
    if (!entries.length) return "No balance";
    if (usdMode) {
      const supported = entries.filter(([currency]) => DEMO_FX_TO_USD[currency] !== undefined);
      const unsupported = entries.filter(([currency]) => DEMO_FX_TO_USD[currency] === undefined);
      const usd = supported.reduce((sum, [currency, amount]) => sum + Number(amount) * DEMO_FX_TO_USD[currency], 0);
      const converted = supported.length ? `Illustrative USD equivalent ${money("USD", usd)} · FX not live` : "Illustrative USD equivalent unavailable";
      const unconverted = unsupported.map(([currency, amount]) => money(currency, amount));
      return [converted, ...(unconverted.length ? [`Not converted: ${unconverted.join(", ")}`] : [])].join(" · ");
    }
    return entries.map(([currency, amount]) => money(currency, amount)).join(" · ");
  };
  const formatCashEntries = (entries: { currency: string; amount: string; invoice_count: number }[]) => {
    if (!entries.length) return "No forecast cash";
    if (usdMode) {
      const supported = entries.filter(entry => DEMO_FX_TO_USD[entry.currency] !== undefined);
      const unsupported = entries.filter(entry => DEMO_FX_TO_USD[entry.currency] === undefined);
      const usd = supported.reduce((sum, entry) => sum + Number(entry.amount) * DEMO_FX_TO_USD[entry.currency], 0);
      const converted = supported.length ? `Illustrative USD equivalent ${money("USD", usd)} (${supported.reduce((sum, entry) => sum + entry.invoice_count, 0)}) · FX not live` : "Illustrative USD equivalent unavailable";
      const unconverted = unsupported.map(entry => `${money(entry.currency, entry.amount)} (${entry.invoice_count})`);
      return [converted, ...(unconverted.length ? [`Not converted: ${unconverted.join(", ")}`] : [])].join(" · ");
    }
    return [...entries].sort((a, b) => (currencyOrder.indexOf(a.currency) < 0 ? 99 : currencyOrder.indexOf(a.currency)) - (currencyOrder.indexOf(b.currency) < 0 ? 99 : currencyOrder.indexOf(b.currency)) || a.currency.localeCompare(b.currency)).map(entry => `${money(entry.currency, entry.amount)} (${entry.invoice_count})`).join(" · ");
  };
  const scenarioWeeks = useMemo(() => {
    const grouped = new Map<string, { week_start: string; entries: ScenarioWeekEntry[]; invoice_count: number }>();
    for (const entry of data?.top_late_scenario.cash_by_week || []) {
      const group = grouped.get(entry.week_start) || { week_start: entry.week_start, entries: [], invoice_count: 0 };
      group.entries.push(entry); group.invoice_count += entry.invoice_count; grouped.set(entry.week_start, group);
    }
    return [...grouped.values()].sort((a, b) => a.week_start.localeCompare(b.week_start));
  }, [data]);
  const scenarioWeekValue = (entries: ScenarioWeekEntry[]) => {
    if (!usdMode) return [...entries].sort((a, b) => (currencyOrder.indexOf(a.currency) < 0 ? 99 : currencyOrder.indexOf(a.currency)) - (currencyOrder.indexOf(b.currency) < 0 ? 99 : currencyOrder.indexOf(b.currency)) || a.currency.localeCompare(b.currency)).map(entry => money(entry.currency, entry.amount)).join(" · ");
    const totals = Object.fromEntries(entries.map(entry => [entry.currency, Number(entry.amount)]));
    return formatAmounts(totals);
  };

  const exportFilteredRows = async () => {
    setExportState("Preparing all rows that match the active filters…");
    try {
      const response = await fetch(`/api/exports/invoices.csv?${searchKey}`, { credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(currentReadTimeoutMs()) });
      if (!response.ok) throw new Error(`Export request failed (${response.status}).`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "late-payer-all-filtered.csv"; anchor.click(); URL.revokeObjectURL(url);
      setExportState(`Export downloaded · all rows matching the current filters · snapshot ${data?.as_of || "latest"}.`);
    } catch (cause) { setExportState(`Export failed. Retry after the invoice results finish loading. ${cause instanceof Error ? cause.message : ""}`); }
  };

  const selectView = (view: string) => updateUrl({ view, age_bucket: null, action_only: null, drifting_only: null });
  const setCardFilter = (filter: "open" | "overdue" | "actions" | "drifting") => {
    if (filter === "open") updateUrl({ view: "open", action_only: null, drifting_only: null, age_bucket: null });
    else if (filter === "overdue") updateUrl({ view: "overdue", action_only: null, drifting_only: null, age_bucket: null });
    else if (filter === "actions") updateUrl({ view: "all", action_only: "1", drifting_only: null, age_bucket: null });
    else updateUrl({ view: "all", drifting_only: "1", action_only: null, age_bucket: null });
  };
  const clearAllFilters = () => updateUrl({
    q: null, client_id: null, statuses: null, due_from: null, due_to: null,
    age_bucket: null, predicted_week: null, action_only: null, drifting_only: null, page: null,
  }, "push", false);
  const filterChips = [
    urlQuery ? { key: "q", label: `Search: ${urlQuery}` } : null,
    selectedClient ? { key: "client_id", label: `Client: ${data?.items.find(row => row.client_id === selectedClient)?.client_name || clients.find(client => client.id === selectedClient)?.name || "selected"}` } : null,
    params.get("statuses") ? { key: "statuses", label: `Status: ${params.get("statuses")!.split(",").map(status => statusLabel(status)).join(", ")}` } : null,
    params.get("due_from") ? { key: "due_from", label: `Due from: ${params.get("due_from")}` } : null,
    params.get("due_to") ? { key: "due_to", label: `Due to: ${params.get("due_to")}` } : null,
    selectedAge ? { key: "age_bucket", label: `Aging: ${selectedAge.replaceAll("_", " ")}` } : null,
    params.get("predicted_week") ? { key: "predicted_week", label: `Settlement week: ${params.get("predicted_week")}` } : null,
    actionOnly ? { key: "action_only", label: "Action queue" } : null,
    driftingOnly ? { key: "drifting_only", label: "Client timing signal" } : null,
  ].filter((chip): chip is { key: string; label: string } => chip !== null);
  const resultsStale = Boolean(data && appliedSearchKey !== searchKey);
  const askEvidence = askResult?.data && typeof askResult.data === "object" ? askResult.data as Record<string, unknown> : null;
  const askCurrencyMatch = askQuestion.match(/\b(USD|EUR|GBP|CAD|AUD)\b/i);
  const askWindow = /this week/i.test(askQuestion) ? "this calendar week" : /next 7 days/i.test(askQuestion) ? "next 7 days" : /overdue/i.test(askQuestion) ? "currently overdue" : "current invoice history; no date window";
  const evidenceInvoices: AskInvoice[] = askEvidence
    ? (Array.isArray(askEvidence.invoices) ? askEvidence.invoices as AskInvoice[]
      : Array.isArray(askEvidence) ? askEvidence.flatMap(item => {
        if (!item || typeof item !== "object") return [];
        const record = item as Record<string, unknown>;
        if (Array.isArray(record.invoices)) return record.invoices as AskInvoice[];
        return typeof record.invoice_id === "string" ? [{ id: record.invoice_id, invoice_number: String(record.invoice_number || record.invoice_id.slice(0, 8)) }] : [];
      })
      : Array.isArray(askEvidence.invoice_ids) ? (askEvidence.invoice_ids as string[]).map(id => ({ id, invoice_number: id.slice(0, 8) })) : [])
    : [];

  return <AgGridProvider modules={modules}>
    <div className="app-shell">
      <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/tour">Guided tour</Link><DemoStoriesMenu/><Link className="back-link" href="/performance">Model performance</Link><Link className="back-link" href="/actions">Action queue</Link><Link className="back-link" href="/collections">Collections</Link>{authenticated && <Link className="back-link" href="/operator">PayPal operator</Link>}<Link className="btn" href="/offline-demo">Read-only fallback</Link><OperatorControls/></div></header>
      <main className="main-wrap">
        {!authenticated && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Deterministic sample invoices. Simulated actions never contact PayPal.</span></div>}
        <div className="heading-row"><div><div className="eyebrow">Receivables intelligence</div><h1>Invoice portfolio</h1><p className="subheading">See what may get paid, who is slowing down, and what needs a human decision.</p></div><div className="toolbar-tools"><button className="btn" onClick={() => setUsdMode(value => !value)}>{usdMode ? "Show source currencies" : "Show USD equivalent"}</button>{authenticated && <button className="btn primary" disabled={busy} onClick={() => void sync()}>{busy ? "Syncing…" : "↻ Sync PayPal"}</button>}</div></div>
        {!authenticated && data && <><TimeTravelControl key={`${searchKey}-${data.as_of_max}`} currentAsOf={data.as_of} maxAsOf={data.as_of_max} resetBusy={resetBusy} onCommit={commitDemoClock} onReset={() => setResetConfirmOpen(true)} /><div className="fine-print" style={{ margin: "-6px 0 14px" }}>Simulated payments, replies, approvals, promises, and disputes persist across reloads in this browser&apos;s isolated demo session. Reset restores that session and clears unsaved session drafts; saved column preferences and PayPal Sandbox records stay as they are.</div></>}
        {error && <div className="error-box" role="alert">{error}{data && <span className="fine-print"> Showing the last successful portfolio snapshot from {data.as_of}.</span>}{!error.startsWith("The API is waking up.") && <button className="btn" style={{ marginLeft: 10 }} onClick={() => { wakeRetries.current.delete(searchKey); setFallbackVisible(false); setError(""); setLoading(true); void refresh(); }}>Retry</button>}</div>}
        {fallbackVisible && <section className="panel fallback-callout"><div><div className="panel-title">Continue with the read-only fallback demo</div><div className="panel-subtitle">A synthetic portfolio snapshot and five repeatable stops are packaged with this web app. No demo mutations are written from that page.</div></div><Link className="btn primary" href="/offline-demo">Open fallback demo</Link></section>}
        {data && <section className="decision-hero" aria-label="Portfolio snapshot and review actions">
          <div><span className="eyebrow">{hasAggregateFilters ? "Filtered invoice scope" : "Demo snapshot"} · {data.as_of}</span><span>{data.view_counts.open} matching open invoices · {data.view_counts.overdue} matching past due · {data.drifting_client_count} portfolio-wide new or worsening client signals. Amount totals appear in the exposure section below.</span></div>
          <div className="decision-hero-action"><span>Today&apos;s actions</span><strong>{Math.min(data.ready_for_review_count, data.daily_suggestion_cap)} ready for review</strong><Link className="btn primary" href="/actions">Open action queue</Link></div>
        </section>}
        {data && <nav className="workflow-strip" aria-label="Forecast to outcome workflow">
          <a href="#cash-forecast"><strong>1 · Forecast</strong><span>Review probability-weighted cash</span></a>
          <a href="#client-signals"><strong>2 · Changed client</strong><span>Inspect timing signals and evidence</span></a>
          <Link href="/actions"><strong>3 · Suggested action</strong><span>Open the ranked queue</span></Link>
          <Link href="/actions#action-approval"><strong>4 · Human approval</strong><span>Approve or skip each item</span></Link>
          <Link href="/actions#recorded-outcomes"><strong>5 · Outcome</strong><span>Review recorded payment results</span></Link>
        </nav>}
        {data && <div className="morning-summary panel"><div><div className="panel-title">Morning summary · {aggregateScopeLabel.toLowerCase()} invoice scope · snapshot {data.as_of}</div><div className="panel-subtitle">{data.expected_cash_by_week.length ? `First calendar-week cash estimate: ${formatCashEntries(data.expected_cash_by_week.filter(entry => entry.week_start === data.expected_cash_by_week[0].week_start))}` : "No expected cash in the current forecast window."} · {Math.min(data.ready_for_review_count, data.daily_suggestion_cap)} portfolio-wide actions are ready for human review.</div></div><a className="btn primary" href="#morning-brief">Expand morning brief</a></div>}
        <div className="ask-brief-grid">
        <section className="panel" aria-labelledby="ask-title"><div className="panel-head"><div><div className="panel-title" id="ask-title">Ask your receivables</div><div className="panel-subtitle">Supported intents only: largest open balance by named currency, clients with timing signals, expected cash this calendar week or next 7 days, and current overdue invoices.</div></div></div><div className="fine-print">Scope: all invoices in this demo/operator portfolio, as of {data?.as_of || "the latest snapshot"}; it does not inherit invoice-grid filters. Currency in question: {askCurrencyMatch?.[1].toUpperCase() || "none selected · results remain separated by currency"} · Window requested: {askWindow} · no FX conversion.</div>
          <div className="fine-print">Supported questions</div><div className="ask-examples">{["Who owes me the most in EUR?", "Which clients are paying later?", "How much cash is expected this week?", "Which invoices are overdue in USD?"].map(example => <button className="btn" key={example} type="button" onClick={() => setAskQuestion(example)}>{example}</button>)}</div>
          <form className="toolbar-tools" onSubmit={ask}><input className="search" aria-label="Ask your receivables" placeholder="Who owes me the most in EUR?" value={askQuestion} onChange={event => setAskQuestion(event.target.value)}/><button className="btn primary" disabled={askLoading || !askQuestion.trim()}>{askLoading ? "Checking…" : "Ask"}</button></form>
          {askResult && <div className="action-message" role="status" style={{ marginTop: 12 }}><div>{askResult.answer}</div><small className="fine-print">Snapshot {askResult.as_of || "date unavailable"} · {askResult.grounded_in || "current scoped invoice facts"}</small>
            {typeof askEvidence?.client_id === "string" && <div style={{ marginTop: 8 }}><Link className="table-link" href={`/clients/${encodeURIComponent(askEvidence.client_id)}`}>Open supporting client</Link></div>}
            {Array.isArray(askEvidence) && <div className="ask-examples">{askEvidence.slice(0, 5).flatMap(item => item && typeof item === "object" && typeof (item as Record<string, unknown>).client_id === "string" ? [{ id: String((item as Record<string, unknown>).client_id), name: String((item as Record<string, unknown>).client_name) }] : []).map(client => <Link className="btn" key={client.id} href={`/clients/${encodeURIComponent(client.id)}`}>Open {client.name}</Link>)}</div>}
            {evidenceInvoices.length > 0 && <div className="ask-examples" aria-label="Supporting invoices">{evidenceInvoices.slice(0, 8).map(invoice => <Link className="btn" key={invoice.id} href={detailHref(invoice.id)}>Open {invoice.invoice_number}</Link>)}</div>}
          </div>}
        </section>
        <section className="panel" id="morning-brief"><div className="panel-head"><div><div className="panel-title">Morning brief</div><div className="panel-subtitle">Expected cash, highest value actions, and client timing signals.</div></div><button className="btn" onClick={() => void openBrief()}>{briefOpen ? "Refresh full brief" : "Expand full brief"}</button></div>
          {briefOpen && (briefLoading ? <div className="empty-inline" role="status">Building the structured brief…</div> : brief ? <><div className="fine-print">Snapshot {brief.as_of} · this week through {brief.week_end}</div>
            <div className="cash-week-grid">{brief.expected_cash_this_week.length ? brief.expected_cash_this_week.map(item => <div className="cash-week" key={item.currency}><span>Expected cash · {item.currency}</span><strong>{money(item.currency, item.amount)}</strong><small>Probability-weighted · {item.invoice_count} contributing invoices</small>{item.invoice_ids?.slice(0, 5).map(invoiceId => <Link className="table-link" key={invoiceId} href={detailHref(invoiceId)}>Open contributing invoice</Link>)}</div>) : <div className="empty-inline">No expected settlements this week.</div>}</div>
            <div className="drift-signal-grid" style={{ marginTop: 12 }}>{brief.top_candidates.length ? brief.top_candidates.map(item => <article className="drift-signal-card" key={item.invoice_id}><div className="drift-signal-top"><Link className="table-link" href={detailHref(item.invoice_id, "view=open&page=1&page_size=25&sort=action&direction=asc")}>{item.invoice_number}</Link><span className="drift-badge">{decisionLabel(item.action)}</span></div><p><Link className="table-link" href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link> · {money(item.currency, item.outstanding)}</p><div className="fine-print">Assumed 14-day cash uplift · 10% scenario, not measured incremental cash: {money(item.currency, item.expected_cash_gain)} · {item.reason}</div></article>) : <div className="empty-inline">No reminder is pending review for this snapshot.</div>}</div>
            <div className="fine-print" style={{ marginTop: 12 }}>Clients slowing down: {brief.clients_slowing_down.length ? brief.clients_slowing_down.map(client => <span key={client.client_id}> <Link className="table-link" href={`/clients/${encodeURIComponent(client.client_id)}`}>{client.client_name}</Link> ({signalLabel(client.signal_type)})</span>) : "none"}</div>
          </> : null)}
        </section>
        </div>
        {authenticated && <details className="panel dashboard-secondary"><summary>PayPal Sandbox · controlled invoice and sync workflow</summary><section className="panel"><div className="panel-head"><div><div className="panel-title">PayPal Sandbox</div><div className="panel-subtitle">Create a controlled test invoice and review the last import.</div></div><span className="status-pill">Sandbox only</span></div>
          <div className="fine-print">{syncStatus?.last_run ? `Last sync ${new Date(syncStatus.last_run.completed_at || syncStatus.last_run.started_at).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} · ${syncStatus.last_run.created} imported · ${syncStatus.last_run.updated} updated · ${syncStatus.last_run.status}` : "No PayPal sync has completed yet."} · times shown in IST · Webhook retries failed: {syncStatus?.webhook_failures ?? "unavailable"}</div>
          {syncStatus?.last_run?.errors?.length ? <div className="error-box" style={{ marginTop: 10 }}>{syncStatus.last_run.errors.join(" · ")}</div> : null}
          <form className="toolbar-tools" onSubmit={createInvoice} style={{ marginTop: 14 }}>
            <select className="select" aria-label="Sandbox invoice recipient" value={newInvoiceClient} onChange={event => setNewInvoiceClient(event.target.value)} required>
              <option value="">Choose allowlisted recipient</option>{paypalRecipients.map(client => <option value={client.id} key={client.id}>{client.name}</option>)}
            </select>
            <input className="search" aria-label="Invoice amount" type="number" min="0.01" max="1000000" step="0.01" placeholder="Amount" value={newInvoiceAmount} onChange={event => setNewInvoiceAmount(event.target.value)} required />
            <select className="select" aria-label="Payment terms" value={newInvoiceTerm} onChange={event => setNewInvoiceTerm(event.target.value)}><option value="DUE_ON_RECEIPT">Due on receipt</option><option value="NET_15">NET 15</option><option value="NET_30">NET 30</option></select>
            <label className="fine-print"><input type="checkbox" checked={newInvoicePartial} onChange={event => setNewInvoicePartial(event.target.checked)} /> Allow partial payment</label>
            <button className="btn primary" disabled={creatingInvoice || !paypalRecipients.length}>{creatingInvoice ? "Creating and sending…" : "Create & send Sandbox invoice"}</button>
            <button className="btn" type="button" disabled={busy} onClick={() => void sync()}>{busy ? "Syncing…" : "Retry sync"}</button>
          </form>
          {!paypalRecipients.length && <div className="empty-inline" style={{ marginTop: 10 }}>No allowlisted PayPal recipient is synced. Sync Sandbox invoices and check PAYPAL_ALLOWED_RECIPIENT_EMAILS.</div>}
          {createdInvoice && <div className="action-message" role="status" style={{ marginTop: 12 }}>Created and sent <Link className="table-link" href={`/invoices/${encodeURIComponent(createdInvoice.id)}`}>{createdInvoice.number}</Link> through PayPal Sandbox.</div>}
        </section></details>}
        {loading && !data && <div role="status" aria-label="Loading portfolio" className="portfolio-skeleton"><div className="stats-grid">{[1,2,3,4].map(key => <div className="skeleton-card" key={key} />)}</div><div className="skeleton-grid" /></div>}
        {data && <>
          {data.as_of !== data.as_of_max && <div className="demo-banner" role="status">Viewing as of {data.as_of} · simulation is disabled while the clock is moved back.</div>}
          <section className="panel"><div className="eyebrow">{aggregateScopeLabel} open book · {data.as_of}</div><h2>{usdMode ? formatAmounts(data.currency_totals) : formatAmounts(data.currency_totals)} open across {data.view_counts.open.toLocaleString("en-US")} invoices</h2><p className="subheading">{data.view_counts.overdue.toLocaleString("en-US")} past due · {data.drifting_client_count.toLocaleString("en-US")} portfolio-wide client timing signals</p><p className="fine-print">{usdMode ? "Fixed demo rates: EUR 1.08 USD · GBP 1.27 USD · CAD 0.74 USD. FX is not live." : "Balances remain separated by source currency; toggle to a fixed USD equivalent when presenting."}</p></section>
          <section className="stats-grid">
            <button className="stat-card stat-button" onClick={() => setCardFilter("open")}><div className="stat-label">Open invoices</div><div className="stat-value">{data.view_counts.open}</div><div className="stat-note">Across {Object.keys(data.currency_totals).length} currencies</div></button>
            <button className="stat-card stat-button" onClick={() => setCardFilter("overdue")}><div className="stat-label">Past due</div><div className="stat-value">{data.view_counts.overdue}</div><div className="stat-note">Invoices with a remaining balance</div></button>
            <button className="stat-card stat-button" onClick={() => setCardFilter("actions")}><div className="stat-label">Ready for review</div><div className="stat-value">{Math.min(data.ready_for_review_count, data.daily_suggestion_cap)} of {data.ready_for_review_count}</div><div className="stat-note">Daily cap {data.daily_suggestion_cap} · portfolio-wide queue, independent of invoice filters</div></button>
            <button className="stat-card stat-button" onClick={() => setCardFilter("drifting")}><div className="stat-label">New or worsening signals</div><div className="stat-value">{data.drifting_client_count}</div><div className="stat-note">Paying later {driftCounts.payingLater} · Later than usual {driftCounts.laterThanUsual} · Gone quiet {driftCounts.goneQuiet}</div></button>
          </section>
          <p className="fine-print">Invoice summary scope: {hasAggregateFilters ? "cash, aging, exposure, and scenario summaries use the active search, client, status, and due-date filters" : "cash, aging, exposure, and scenario summaries cover the accessible invoice portfolio"}; grid view, aging bucket, settlement week, action-only, and drift-only filters do not change them. Client timing cards always cover all clients in the accessible portfolio. Habitually late clients are tracked separately ({driftCounts.habituallyLate}); only a ≥7-day worsening, a sustained ≥1.5-day-per-invoice slope, or a strong overdue outlier enters the action signal count.</p>
          <p className="fine-print" style={{ marginTop: -8, marginBottom: 14 }}>The demo cohort has {data.view_counts.all.toLocaleString("en-US")} invoices: {data.view_counts.paid.toLocaleString("en-US")} historical paid records provide model context, alongside {data.view_counts.open.toLocaleString("en-US")} currently open invoices.</p>
          <section className="insight-strip" id="client-signals"><div className="drift-strip-head"><div><div className="insight-title">Portfolio-wide client payment timing</div><div className="insight-text">Top five of {data.drifting_client_count} portfolio signals, sorted by money at risk and signal strength. This client view is global; the invoice grid below uses the active filters.</div></div><span className="fine-print">Activity signal · not a churn score</span></div>
            {drifting.length ? <div className="drift-signal-grid">{drifting.slice(0, 5).map(client => {
              const drift = client.drift;
              const trigger = drift.signal_type === "gone_quiet"
                ? `${drift.days_since_last_invoice ?? 0} days since last invoice · usual gap ${Math.round(drift.cadence_days || 0)} days`
                : `${Math.round(drift.recent_median_delay_days || 0)}d recent delay · ${Math.round(drift.usual_delay_days || 0)}d usual`;
              return <article className="drift-signal-card" key={client.id}><div className="drift-signal-top"><strong><Link className="table-link" href={`/clients/${encodeURIComponent(client.id)}`}>{client.name}</Link></strong><span className="drift-badge">{signalLabel(drift.signal_type)}</span></div><p>{trigger}</p><div className="fine-print">Risk factor {drift.signal_strength.toFixed(2)} · {Object.entries(drift.money_at_risk_by_currency || {}).map(([currency, amount]) => `${currency} ${amount.toLocaleString("en-US", { maximumFractionDigits: 0 })} at risk`).join(" · ") || "No current exposure"}</div></article>;
            })}</div> : <div className="insight-text">No client has a current delay or cadence signal.</div>}
          </section>

          <section className="aging-panel"><div className="panel-head"><div><div className="panel-title">{aggregateScopeLabel} open invoice aging · {data.view_counts.open} invoices</div><div className="panel-subtitle">Select a bucket to filter the current invoice grid.</div></div>{selectedAge && <button className="btn" onClick={() => updateUrl({ age_bucket: null })}>Clear bucket</button>}</div>
            <div className="aging-grid">{[["not_due", "Not due"], ["late_1_30", "1–30 days"], ["late_31_60", "31–60 days"], ["late_61_89", "61–89 days"], ["late_90_plus", "90+ days"]].map(([key, label]) => {
              const bucket = data.aging_buckets[key] || { count: 0, amounts: {} };
              return <button className={`aging-bucket ${selectedAge === key ? "selected" : ""}`} key={key} onClick={() => updateUrl({ view: "open", age_bucket: selectedAge === key ? null : key, action_only: null, drifting_only: null })}>
                <span>{label}</span><strong>{bucket.count}</strong><small>{formatAmounts(bucket.amounts)}</small>
              </button>;
            })}</div>
          </section>

          <section className="cash-forecast-panel" id="cash-forecast"><div className="panel-head"><div><div className="panel-title">{aggregateScopeLabel} expected cash in · 56-day forecast horizon · snapshot {data.as_of}</div><div className="panel-subtitle">Amounts use settlement probability mass by calendar week, not p50 dates. The 56 days after the snapshot can intersect nine calendar buckets: a partial first week, full middle weeks, and a partial last week. Each probability amount covers its whole calendar-week bucket; a border bucket can include days outside the 56-day window.</div></div></div>
            {data.expected_cash_by_week.length ? <div className="cash-week-grid">{Array.from(new Set(data.expected_cash_by_week.map(entry => entry.week_start))).map(week => { const bounds = calendarWeekBounds(data.as_of, week); return <button className="cash-week" key={week} onClick={() => { updateUrl({ view: "open", predicted_week: week, age_bucket: null, action_only: null, drifting_only: null }); document.getElementById("invoice-grid")?.scrollIntoView({ behavior: "smooth" }); }}><span>{bounds.partial ? "Partial-window bucket" : "Calendar week"} · {shortIsoDate(bounds.visibleStart)}–{shortIsoDate(bounds.visibleEnd)}</span><small>Full settlement week {shortIsoDate(bounds.calendarStart)}–{shortIsoDate(bounds.calendarEnd)}</small><strong>{formatCashEntries(data.expected_cash_by_week.filter(entry => entry.week_start === week))}</strong><small>Probability-weighted · contributor counts can repeat across weeks; same invoice can contribute to multiple buckets</small></button>; })}</div> : <div className="empty-inline">No forecast settlement probability mass falls inside the next eight weeks.</div>}
          </section>
          <section className="panel"><div className="panel-title">{aggregateScopeLabel} open balance by forecast horizon · {data.view_counts.open} invoices</div><div className="panel-subtitle">Gross principal is grouped by p50 settlement horizon for exposure review; this is not a cash receipt estimate. “Beyond 180 days” means the cumulative modeled probability does not reach that date quantile within the 180-day model horizon; review the invoice and its uncertainty before planning around the amount. Disputed or unforecastable invoices remain visible. Reconciliation: {data.open_balance_reconciliation?.count ?? data.view_counts.open} invoices against the {data.view_counts.open}-invoice open book.</div><div className="aging-grid">{[["next_8_weeks", "Next 8 weeks"], ["after_8_weeks", "After 8 weeks · within 180 days"], ["beyond_180_days", "Beyond 180 days / no median"], ["disputed_unforecastable", "Disputed / unforecastable"]].map(([key, label]) => { const bucket = data.cash_horizon[key] || { count: 0, amounts: {} }; return <article className="aging-bucket" key={key}><span>{label}</span><strong>{bucket.count}</strong><small>{formatAmounts(bucket.amounts)}</small></article>; })}</div></section>
          <section className="panel"><div className="panel-head"><div><div className="panel-title">If the top five late clients in the {aggregateScopeLabel.toLowerCase()} invoice scope pay within 14 days</div><div className="panel-subtitle">A gross hypothetical collection scenario compared with probability-weighted baseline cash for the same scoped overdue invoices and 14-day window.</div></div><button className="btn" onClick={() => setScenarioOpen(value => !value)}>{scenarioOpen ? "Hide scenario" : "Show weekly comparison"}</button></div>{scenarioOpen && <><p className="fine-print">Snapshot {data.as_of} · {data.top_late_scenario.assumption} {data.top_late_scenario.selection_rule}</p><div className="ask-examples">{data.top_late_scenario.clients.map(client => <Link className="btn" key={client.id} href={`/clients/${encodeURIComponent(client.id)}`}>{client.name} · {client.late_invoice_count} late invoices</Link>)}</div>{(() => { const gross: Record<string, number> = {}; for (const entry of data.top_late_scenario.cash_by_week) gross[entry.currency] = (gross[entry.currency] || 0) + Number(entry.amount); const baseline = Object.fromEntries((data.top_late_scenario.baseline_by_currency || []).map(entry => [entry.currency, Number(entry.amount)])); const delta = Object.fromEntries([...new Set([...Object.keys(gross), ...Object.keys(baseline)])].map(currency => [currency, (gross[currency] || 0) - (baseline[currency] || 0)])); return <div className="aging-grid scenario-comparison-grid"><article className="aging-bucket"><span>Gross hypothetical · full payment within 14 days</span><strong>{formatAmounts(gross)}</strong></article><article className="aging-bucket"><span>Probability-weighted baseline · same invoices and 14 days</span><strong>{formatAmounts(baseline)}</strong></article><article className="aging-bucket"><span>Scenario arithmetic · difference from baseline</span><strong>{formatAmounts(delta)}</strong></article></div>; })()}<p className="fine-print">Currency totals remain separate unless you choose Show USD equivalent; converted values are illustrative USD equivalents using fixed demo rates. The gross scenario assumes full payment within 14 days; the baseline uses the same overdue invoices and 14-day window. The delta is not measured incremental cash.</p><div className="cash-week-grid">{scenarioWeeks.map(week => <details className="cash-week" key={week.week_start}><summary>Week of {shortIsoDate(week.week_start)} · {week.invoice_count} contributing invoices</summary><p><strong>Hypothetical weekly gross: {scenarioWeekValue(week.entries)}</strong></p><div className="fine-print">{week.entries.flatMap(entry => (entry.client_contributions || []).map(client => <div key={`${entry.currency}-${client.client_id}`}><Link className="table-link" href={`/clients/${encodeURIComponent(client.client_id)}`}>{client.client_name}</Link> · {client.invoice_count} invoices · {usdMode ? DEMO_FX_TO_USD[entry.currency] === undefined ? `Not converted ${money(entry.currency, client.assumed_gross)}` : `Illustrative USD equivalent ${money("USD", Number(client.assumed_gross) * DEMO_FX_TO_USD[entry.currency])} from ${money(entry.currency, client.assumed_gross)}` : `gross ${money(entry.currency, client.assumed_gross)}`}</div>))}</div></details>)}</div>{!scenarioWeeks.length && <div className="empty-inline">No overdue balance is available for this scenario.</div>}</>}</section>

          <section className="grid-panel" id="invoice-grid">
            <div className="toolbar-tools filter-toolbar"><details className="column-chooser status-filter"><summary>Status{params.get("statuses") ? ` · ${params.get("statuses")!.split(",").length} selected` : " · All"}</summary><div className="column-menu" role="group" aria-label="Invoice status filters">{["SENT","OVERDUE","PARTIALLY_PAID","PAID","CANCELLED"].map(value => { const selected = (params.get("statuses") || "").split(",").filter(Boolean); return <label key={value}><input type="checkbox" checked={selected.includes(value)} onChange={event => { const next = event.target.checked ? [...selected, value] : selected.filter(item => item !== value); updateUrl({ statuses: next.join(",") || null }); }}/>{statusLabel(value)}</label>; })}<button className="btn" type="button" onClick={() => updateUrl({ statuses: null })}>Clear statuses</button></div></details><label>Due from <input type="date" aria-label="Due from" value={params.get("due_from") || ""} onChange={event => updateUrl({ due_from: event.target.value || null })} /></label><label>Due to <input type="date" aria-label="Due to" value={params.get("due_to") || ""} onChange={event => updateUrl({ due_to: event.target.value || null })} /></label>{params.has("predicted_week") && <button className="btn" onClick={() => updateUrl({ predicted_week: null })}>Clear settlement week</button>}{filterChips.length > 0 && <button className="btn" onClick={clearAllFilters}>Clear all filters ({filterChips.length})</button>}</div>
            {filterChips.length > 0 && <div className="active-filter-chips" aria-label="Active invoice filters">{filterChips.map(chip => <button className="filter-chip" key={chip.key} onClick={() => updateUrl({ [chip.key]: null })}>{chip.label} <span aria-hidden="true">×</span></button>)}</div>}
            {resultsStale && <div className="empty-inline filter-refresh-status" role="status" aria-live="polite">Updating results for the current filters. The rows shown below still use {new URLSearchParams(appliedSearchKey).toString() || "all invoice filters cleared"} from snapshot {data.as_of}; they are dimmed until the new query finishes.</div>}
            {params.has("predicted_week") && !resultsStale && <section className="panel cash-contributor-panel" id="cash-contributors" aria-labelledby="cash-contributors-title">
              <div className="panel-head"><div>
                <div className="panel-title" id="cash-contributors-title">Invoices contributing to settlement week · {params.get("predicted_week")}</div>
                <p className="panel-subtitle">These are the actual probability-mass contributors for the selected calendar week. Each amount equals the invoice open balance multiplied by its modeled probability of full settlement in that week.</p>
                <p className="fine-print">Showing {rowData.length} of {data.count.toLocaleString("en-US")} contributing invoices · page {data.page} of {data.page_count}. An invoice can contribute to more than one week.</p>
              </div></div>
              {data.count > 0 ? <div className="table-wrap"><table className="client-invoice-table cash-contributor-table">
                <thead><tr><th>Invoice</th><th>Client</th><th>Open balance</th><th>Full-settlement probability</th><th>Expected contribution</th></tr></thead>
                <tbody>{rowData.map(item => {
                  const probability = Number(item.cash_week_contribution?.probability || 0);
                  const clientHref = `/clients/${encodeURIComponent(item.client_id)}?return_to=${encodeURIComponent(searchKey)}`;
                  return <tr key={item.id}>
                    <td><Link className="table-link" href={detailHref(item.id)}>{item.invoice_number}</Link></td>
                    <td><Link className="table-link" href={clientHref}>{item.client_name}</Link></td>
                    <td>{money(item.currency, item.outstanding)}</td>
                    <td>{probability > 0 && probability < 0.01 ? "<1%" : `${Math.round(probability * 100)}%`}</td>
                    <td><strong>{money(item.currency, item.cash_week_contribution?.amount || 0)}</strong></td>
                  </tr>;
                })}</tbody>
              </table></div> : <div className="empty-inline">No invoices contribute probability mass to this settlement week.</div>}
            </section>}
            <div className="grid-toolbar"><div><div className="grid-title">Filtered invoice results <span className="fine-print">{data.count.toLocaleString("en-US")} matching records · client signal cards above are portfolio-wide</span></div><div className="fine-print" style={{ marginTop: 5 }}>{data.count === 0 ? "No matching invoices. Counts, aging, cash, and scenario cards above use the search/client/status/due-date scope; client timing signals remain portfolio-wide." : data.models.length ? `Forecast: ${data.models.join(", ")}` : "Forecast metadata unavailable for matching invoices"}{lastSync ? ` · Last PayPal sync ${lastSync}` : ""} · Demo snapshot {data.as_of} · Browser refresh {portfolioRefreshedAt ? new Date(portfolioRefreshedAt).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" }) + " IST" : "pending"} · Model timestamp {data.model_timestamp ? new Date(data.model_timestamp).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" }) + " IST" : "not available for this page"}</div></div>
              <div className="toolbar-tools"><SearchInput key={urlQuery} query={urlQuery} onDebouncedQuery={value => updateUrl({ q: value || null }, "replace")} />
                <div className="client-combobox" ref={clientPickerRef}><input className="search" role="combobox" aria-autocomplete="list" aria-expanded={clientOptionsOpen} aria-controls="client-options" aria-label="Search clients" placeholder={selectedClient ? "Client filter active · search to change" : "All clients · search"} value={clientSearch} onFocus={() => setClientOptionsOpen(true)} onChange={event => { setClientSearch(event.target.value); setClientOptionsOpen(true); }} onKeyDown={event => { if (event.key === "Escape") setClientOptionsOpen(false); if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); const options = Array.from(document.querySelectorAll<HTMLButtonElement>("#client-options button[role=option]")); options[event.key === "ArrowDown" ? 0 : options.length - 1]?.focus(); } }} />{selectedClient && <button className="clear-client-filter" type="button" onClick={() => { updateUrl({ client_id: null }); setClientSearch(""); }}>Clear selected client</button>}{clientOptionsOpen && <div id="client-options" role="listbox" aria-label="Matching clients">{clientOptionsLoading ? <span className="client-option-state" role="status">Loading clients…</span> : <><button role="option" aria-selected={!selectedClient} onKeyDown={event => { if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); const options = Array.from(document.querySelectorAll<HTMLButtonElement>("#client-options button[role=option]")); const index = options.indexOf(event.currentTarget); options[Math.max(0, Math.min(options.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus(); } if (event.key === "Escape") setClientOptionsOpen(false); }} onClick={() => { updateUrl({ client_id: null }); setClientSearch(""); setClientOptionsOpen(false); }}>All clients</button>{clients.map(client => <button role="option" aria-selected={selectedClient === client.id} key={client.id} title={client.name} onKeyDown={event => { if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); const options = Array.from(document.querySelectorAll<HTMLButtonElement>("#client-options button[role=option]")); const index = options.indexOf(event.currentTarget); options[Math.max(0, Math.min(options.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus(); } if (event.key === "Escape") setClientOptionsOpen(false); }} onClick={() => { updateUrl({ client_id: client.id }); setClientSearch(client.name); setClientOptionsOpen(false); }}>{client.name}</button>)}{!clients.length && <span className="client-option-state">No matching clients.</span>}</>}</div>}</div>
                <label className="sort-control">Sort rows <select className="select" aria-label="Sort invoices" value={`${currentSort}:${currentDirection}`} onChange={event => { const [sort, direction] = event.target.value.split(":"); updateUrl({ sort, direction }); }}>
                  <option value="action:asc">Action queue · daily rank</option><option value="predicted:asc">Predicted date · earliest</option><option value="drift:asc">Client drift state</option><option value="expected_cash:desc">Assumed cash uplift · 10% scenario · within currency</option><option value="p14:desc">Paid in 14d · high to low</option><option value="p14:asc">Paid in 14d · low to high</option><option value="due:asc">Due date · earliest</option><option value="balance:desc">Balance · high to low</option><option value="client:asc">Client · A to Z</option>
                </select></label>
                <details className="column-chooser"><summary>Columns</summary><div className="column-menu column-menu-wide"><strong>Core fields</strong>{columnChoices.filter(([id]) => ["invoice_number","client_name","outstanding","due_date","p50","action"].includes(id)).map(([id, label]) => <label key={id}><input type="checkbox" checked={visibleColumns.includes(id)} onChange={event => setVisibleColumns(columns => event.target.checked ? [...columns, id] : columns.filter(column => column !== id))}/>{label}</label>)}<strong>Optional fields</strong><span className="fine-print">p10–p90 and probabilities describe full settlement after the snapshot date.</span>{columnChoices.filter(([id]) => ["status","window","p14","drift","expected_cash"].includes(id)).map(([id, label]) => <label key={id}><input type="checkbox" checked={visibleColumns.includes(id)} onChange={event => setVisibleColumns(columns => event.target.checked ? [...columns, id] : columns.filter(column => column !== id))}/>{label}</label>)}<button className="btn" type="button" onClick={() => setVisibleColumns(defaultColumns)}>Reset columns</button></div></details>
                <button className="btn" type="button" disabled={resultsStale || Boolean(exportState.startsWith("Preparing"))} onClick={() => void exportFilteredRows()}>Export all filtered rows</button>
              </div>
            </div>
            {exportState && <div className={exportState.startsWith("Export failed") ? "error-box" : "fine-print"} role={exportState.startsWith("Export failed") ? "alert" : "status"}>{exportState} · source-currency values and active filter context are included.</div>}
            <nav className="view-tabs" aria-label="Invoice views">{viewTabs.map(([key, label]) => <button className={currentView === key && !actionOnly && !driftingOnly ? "active" : ""} key={key} onClick={() => selectView(key)}>{label}<span>{data.view_counts[key] || 0}</span></button>)}</nav>
            {actionOnly && <div className="filter-note">Showing only today&apos;s pending proposed reminders. <button onClick={() => updateUrl({ action_only: null })}>Clear</button></div>}
            {driftingOnly && <div className="filter-note">Showing invoices for clients with a timing signal. <button onClick={() => updateUrl({ drifting_only: null })}>Clear</button></div>}
            <div className={`grid-wrap ${resultsStale ? "is-stale" : ""}`} aria-busy={resultsStale}>{rowData.length ? <AgGridReact<InvoiceRow> rowData={rowData} columnDefs={columns} theme={gridTheme} defaultColDef={{ sortable: false, filter: false, resizable: true }} rowHeight={57} headerHeight={44} animateRows={false} suppressCellFocus={false} /> : <div className="empty-box">{data.count === 0 ? "No matching invoices. Portfolio aggregate cards are scoped by search/client/status/due-date filters; client timing signals above are portfolio-wide." : "No invoices are on this page. Choose a prior page or change the page size."}</div>}</div>
            <div className="grid-footer"><div className="currency-total-row">{usdMode ? <span>All pages · open balance <strong>{formatAmounts(data.currency_totals)}</strong></span> : Object.entries(data.currency_totals).map(([currency, amount]) => <span key={currency}>All pages · not converted · open {currency} <strong>{Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>)}</div>
              <div className="pager"><label>Rows <select className="select" aria-label="Rows per page" value={pageSize} onChange={event => updateUrl({ page_size: event.target.value })}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><span>Page {currentPage} of {pageCount}</span><button className="btn" disabled={currentPage <= 1} onClick={() => updateUrl({ page: String(currentPage - 1) }, "push", false)}>Previous</button><button className="btn" disabled={currentPage >= pageCount} onClick={() => updateUrl({ page: String(currentPage + 1) }, "push", false)}>Next</button></div>
            </div>
          </section>
        </>}
      </main>
      <ConfirmDialog
        open={resetConfirmOpen}
        title="Reset this browser’s demo session?"
        description="Simulated payments, replies, approvals, promises, and disputes return to their starting state. Unsaved notes and collection drafts plus the tour position are cleared. Saved column preferences, other reviewers, and PayPal Sandbox records are unchanged."
        confirmLabel="Reset demo session"
        tone="danger"
        onCancel={() => setResetConfirmOpen(false)}
        onConfirm={() => { setResetConfirmOpen(false); void resetDemo(); }}
      />
    </div>
  </AgGridProvider>;
}

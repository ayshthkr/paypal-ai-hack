"use client";

import { AllCommunityModule, ModuleRegistry, themeQuartz } from "ag-grid-community";
import type { ColDef, GridApi, ICellRendererParams } from "ag-grid-community";
import { AgGridProvider, AgGridReact } from "ag-grid-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

ModuleRegistry.registerModules([AllCommunityModule]);

type Drift = {
  signal: string; signal_type: string; available: boolean; settled_count: number; message: string;
  severity_score: number; signal_strength: number; recent_median_delay_days?: number | null;
  usual_delay_days?: number | null; days_since_last_invoice?: number | null; cadence_days?: number | null;
  activity_probability?: number | null; money_at_risk_by_currency?: Record<string, number>;
};
type Forecast = {
  quantiles: Record<string, { days: number; date: string } | null>;
  probabilities: Record<string, number>; model: string;
} | null;
type InvoiceRow = {
  id: string; invoice_number: string; client_id: string; client_name: string; due_date: string;
  days_to_due: number; outstanding: string; currency: string; status: string; source: string;
  forecast: Forecast; drift: Drift;
  decision: { action: string; tone: string; reason: string; expected_cash_gain: number; expected_cash_gain_currency: string | null; daily_rank?: number };
};
type AgingBucket = { count: number; amounts: Record<string, string> };
type InvoiceResult = {
  items: InvoiceRow[]; count: number; page: number; page_size: number; page_count: number; view: string;
  view_counts: Record<string, number>; currency_totals: Record<string, string>;
  aging_buckets: Record<string, AgingBucket>;
  expected_cash_by_week: { week_start: string; currency: string; amount: string; invoice_count: number }[];
  synthetic_demo: boolean; models: string[]; ready_for_review_count: number; drifting_client_count: number;
  as_of: string; as_of_max: string;
};
type ClientRow = { id: string; name: string; drift: Drift };
type MorningBrief = {
  as_of: string; week_end: string;
  expected_cash_this_week: { currency: string; amount: string; invoice_count: number }[];
  top_candidates: { invoice_id: string; invoice_number: string; client_name: string; currency: string; outstanding: string; action: string; reason: string; expected_cash_gain: number }[];
  clients_slowing_down: { client_id: string; client_name: string; signal_type: string; message: string; severity_score: number }[];
};
type AskResult = { intent: string; answer: string; data: unknown };
type SyncStatus = {
  last_run: null | { started_at: string; completed_at: string | null; status: string; created: number; updated: number; event_refreshes: number; retry_failures: number; errors: string[] };
  webhook_failures: number;
};
type PaypalRecipient = { id: string; name: string; email: string };

const modules = [AllCommunityModule];
const gridTheme = themeQuartz.withParams({ accentColor: "#155eef", backgroundColor: "#ffffff", borderColor: "#e6ebee", fontFamily: "Arial, sans-serif", fontSize: "12px", headerBackgroundColor: "#f7f9fa", headerFontWeight: 700, rowHoverColor: "#f5f8fc" });
const viewTabs = [
  ["open", "Open"], ["due_this_week", "Due this week"], ["overdue", "Overdue"], ["paid", "Paid"], ["all", "All"],
] as const;
const columnChoices = [
  ["invoice_number", "Invoice"], ["client_name", "Client"], ["outstanding", "Balance"], ["due_date", "Due"],
  ["status", "Status"], ["p50", "Predicted"], ["window", "p10–p90 range"], ["p14", "Paid in 14d"],
  ["drift", "Client drift"], ["action", "Next action"], ["expected_cash", "Expected cash gain"],
] as const;
const defaultColumns = columnChoices.map(([id]) => id).filter(id => !["status", "window", "expected_cash"].includes(id));

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

function SearchInput({ query, onDebouncedQuery }: { query: string; onDebouncedQuery: (value: string) => void }) {
  const [draft, setDraft] = useState(query);
  useEffect(() => {
    if (draft === query) return;
    const timer = window.setTimeout(() => onDebouncedQuery(draft), 350);
    return () => window.clearTimeout(timer);
  }, [draft, query, onDebouncedQuery]);
  return <input className="search" aria-label="Search invoices" placeholder="Search client, invoice, status" value={draft} onChange={event => setDraft(event.target.value)} />;
}

function TimeTravelControl({ currentAsOf, maxAsOf, resetBusy, onCommit, onReset }: { currentAsOf: string; maxAsOf: string; resetBusy: boolean; onCommit: (value: string) => void; onReset: () => void }) {
  const [selectedDate, setSelectedDate] = useState(currentAsOf || maxAsOf);
  const currentOffset = Math.round((new Date(`${selectedDate}T00:00:00Z`).getTime() - new Date(`${maxAsOf}T00:00:00Z`).getTime()) / 86_400_000);
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const date = new Date(`${maxAsOf}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + Number(event.target.value));
    setSelectedDate(date.toISOString().slice(0, 10));
  };
  return <section className="time-travel-panel"><div><strong>Demo clock</strong><span>Move today back up to 60 days to inspect the portfolio at that point in time.</span></div><label><span>{new Date(`${selectedDate}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span><input aria-label="Demo time travel, days before current date" type="range" min={-60} max={0} value={Math.max(-60, Math.min(0, currentOffset))} onChange={onChange} onPointerUp={() => onCommit(selectedDate)} onKeyUp={() => onCommit(selectedDate)} /><button className="btn" disabled={resetBusy} onClick={onReset}>{resetBusy ? "Resetting…" : "Reset demo"}</button></label></section>;
}

function money(currency: string, value: number | string) {
  return `${currency} ${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function PortfolioClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchKey = searchParams.toString();
  const params = useMemo(() => new URLSearchParams(searchKey), [searchKey]);
  const currentView = params.get("view") || "open";
  const currentPage = Math.max(1, Number(params.get("page") || "1"));
  const pageSize = Number(params.get("page_size") || "25");
  const currentSort = params.get("sort") || "action";
  const currentDirection = params.get("direction") || "asc";
  const selectedClient = params.get("client_id") || "";
  const selectedAge = params.get("age_bucket") || "";
  const actionOnly = params.get("action_only") === "1";
  const driftingOnly = params.get("drifting_only") === "1";
  const urlQuery = params.get("q") || "";
  const detailHref = useCallback((id: string, query = searchKey) => `/invoices/${encodeURIComponent(id)}${query ? `?${query}` : ""}`, [searchKey]);

  const [data, setData] = useState<InvoiceResult | null>(null);
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [visibleColumns, setVisibleColumns] = useState<string[]>(defaultColumns);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [wakeRetryNonce, setWakeRetryNonce] = useState(0);
  const [authenticated, setAuthenticated] = useState(false);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [resetBusy, setResetBusy] = useState(false);
  const [paypalRecipients, setPaypalRecipients] = useState<PaypalRecipient[]>([]);
  const [newInvoiceClient, setNewInvoiceClient] = useState("");
  const [newInvoiceAmount, setNewInvoiceAmount] = useState("");
  const [newInvoiceTerm, setNewInvoiceTerm] = useState("NET_30");
  const [newInvoicePartial, setNewInvoicePartial] = useState(false);
  const [creatingInvoice, setCreatingInvoice] = useState(false);
  const [createdInvoice, setCreatedInvoice] = useState<{ id: string; number: string } | null>(null);
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [briefLoading, setBriefLoading] = useState(false);
  const [askQuestion, setAskQuestion] = useState("");
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [askLoading, setAskLoading] = useState(false);
  const gridApi = useRef<GridApi<InvoiceRow> | null>(null);
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
    if (mode === "push") router.push(href, { scroll: false });
    else router.replace(href, { scroll: false });
  }, [router, searchKey]);

  useEffect(() => {
    const next = new URLSearchParams(searchKey);
    let changed = false;
    for (const [key, value] of [["view", "open"], ["page", "1"], ["page_size", "25"], ["sort", "action"], ["direction", "asc"]]) {
      if (!next.has(key)) { next.set(key, value); changed = true; }
    }
    if (changed) router.replace(`/?${next.toString()}`, { scroll: false });
  }, [router, searchKey]);

  const refresh = useCallback(async () => {
    const apiParams = new URLSearchParams(searchKey);
    if (!apiParams.has("view")) apiParams.set("view", "open");
    if (!apiParams.has("page")) apiParams.set("page", "1");
    if (!apiParams.has("page_size")) apiParams.set("page_size", "25");
    if (!apiParams.has("sort")) apiParams.set("sort", "action");
    const url = `/api/invoices?${apiParams.toString()}`;
    const existing = refreshInFlight.current.get(url);
    if (existing) return existing;
    const pending = Promise.all([
      getJson<InvoiceResult>(url), getJson<{ items: ClientRow[] }>("/api/clients"),
      getJson<{ authenticated: boolean }>("/api/auth/me"),
    ]).then(async ([invoices, clientData, auth]) => {
      if (latestQuery.current !== searchKey) return;
      let operatorSync: SyncStatus | null = null;
      let recipients: PaypalRecipient[] = [];
      if (auth.authenticated) {
        const [syncResult, recipientResult] = await Promise.all([
          getJson<SyncStatus>("/api/sync/status").catch(() => null),
          getJson<{ items: PaypalRecipient[] }>("/api/paypal/recipients").catch(() => null),
        ]);
        operatorSync = syncResult;
        recipients = recipientResult?.items || [];
      }
      if (latestQuery.current !== searchKey) return;
      setData(invoices); setClients(clientData.items); setAuthenticated(auth.authenticated);
      setSyncStatus(operatorSync); setPaypalRecipients(recipients); setError(""); wakeRetries.current.delete(searchKey);
    }).catch(cause => {
      if (latestQuery.current !== searchKey) return;
      const message = cause instanceof Error ? cause.message : "Could not load invoices.";
      const retries = wakeRetries.current.get(searchKey) || 0;
      if ((message.includes("unavailable") || message.includes("502")) && retries < 5) {
        wakeRetries.current.set(searchKey, retries + 1);
        setError(`The API is waking up. Retrying (${retries + 1}/5)…`);
      } else setError(message);
    }).finally(() => {
      refreshInFlight.current.delete(url);
      if (latestQuery.current === searchKey) setLoading(false);
    });
    refreshInFlight.current.set(url, pending);
    return pending;
  }, [searchKey]);

  useEffect(() => { latestQuery.current = searchKey; }, [searchKey]);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!error.startsWith("The API is waking up.")) return;
    const timer = window.setTimeout(() => setWakeRetryNonce(value => value + 1), 1800);
    return () => window.clearTimeout(timer);
  }, [error]);
  useEffect(() => { if (wakeRetryNonce > 0) void refresh(); }, [refresh, wakeRetryNonce]);
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
      setLastSync(new Date().toLocaleTimeString());
      await refresh();
      if (result.errors?.length) setError(`Sync completed with ${result.errors.length} invoice error(s).`);
      else setError(`Sync complete · ${result.created || 0} imported, ${result.updated || 0} updated.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "PayPal sync failed."); }
    finally { setBusy(false); }
  };

  const resetDemo = async () => {
    if (!window.confirm("Reset all synthetic demo payments, reminders, and replies to the starting state? PayPal Sandbox records are not changed.")) return;
    setResetBusy(true); setError("");
    try {
      await getJson("/api/demo/reset", { method: "POST" });
      const next = new URLSearchParams(searchKey);
      next.delete("as_of"); next.delete("action_only"); next.delete("drifting_only"); next.delete("age_bucket");
      next.set("view", "open"); next.set("page", "1");
      const query = next.toString();
      router.replace(query ? `/?${query}` : "/", { scroll: false });
      const [invoices, clientData] = await Promise.all([
        getJson<InvoiceResult>(`/api/invoices?${query}`), getJson<{ items: ClientRow[] }>("/api/clients"),
      ]);
      setData(invoices); setClients(clientData.items);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not reset the synthetic demo."); }
    finally { setResetBusy(false); }
  };

  const createInvoice = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newInvoiceClient || !newInvoiceAmount) return;
    setCreatingInvoice(true); setError(""); setCreatedInvoice(null);
    try {
      const result = await getJson<{ invoice_id: string; invoice_number: string; status: string }>("/api/paypal/invoices", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: newInvoiceClient, amount: newInvoiceAmount, payment_term_type: newInvoiceTerm, partial_payment_allowed: newInvoicePartial }),
      });
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

  const ask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!askQuestion.trim()) return;
    setAskLoading(true); setAskResult(null); setError("");
    try {
      const response = await fetch("/api/ask", { method: "POST", cache: "no-store", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: askQuestion }) });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      setAskResult(value as AskResult);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Receivables question failed."); }
    finally { setAskLoading(false); }
  };

  const baseColumns = useMemo<ColDef<InvoiceRow>[]>(() => [
    { colId: "invoice_number", field: "invoice_number", headerName: "Invoice", pinned: "left", minWidth: 140, flex: .8,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <Link className="table-link" href={detailHref(cell.data?.id || "")}>{cell.value}</Link> },
    { colId: "client_name", field: "client_name", headerName: "Client", pinned: "left", minWidth: 160, flex: 1.1,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <Link className="table-link" href={`/clients/${encodeURIComponent(cell.data?.client_id || "")}`}>{cell.value}</Link> },
    { colId: "outstanding", field: "outstanding", headerName: "Balance", minWidth: 125,
      valueFormatter: cell => cell.data ? money(cell.data.currency, cell.value) : "" },
    { colId: "due_date", field: "due_date", headerName: "Due", minWidth: 115,
      valueFormatter: cell => cell.value ? new Date(`${cell.value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "" },
    { colId: "status", field: "status", headerName: "Status", minWidth: 125,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <span className={`status-pill ${String(cell.value).toLowerCase()}`}>{String(cell.value).replaceAll("_", " ")}</span> },
    { colId: "p50", headerName: "Predicted", minWidth: 120,
      valueGetter: cell => cell.data?.forecast?.quantiles.p50?.date || null,
      valueFormatter: cell => !cell.data?.forecast ? "—" : cell.value ? new Date(`${cell.value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "Beyond 180d" },
    { colId: "window", headerName: "p10–p90 range", minWidth: 165,
      valueGetter: cell => cell.data?.forecast ? { p10: cell.data.forecast.quantiles.p10?.date, p90: cell.data.forecast.quantiles.p90?.date } : null,
      valueFormatter: cell => {
        if (!cell.value) return "—";
        const fmt = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
        return cell.value.p10 ? `${fmt(cell.value.p10)} – ${cell.value.p90 ? fmt(cell.value.p90) : "beyond horizon"}` : "Beyond horizon";
      } },
    { colId: "p14", headerName: "Paid in 14d", minWidth: 110,
      valueGetter: cell => cell.data?.forecast?.probabilities["14d"],
      valueFormatter: cell => cell.value == null ? "—" : `${Math.round(cell.value * 100)}%` },
    { colId: "drift", headerName: "Client drift", minWidth: 135,
      valueGetter: cell => cell.data?.drift?.signal_type,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <span className={`drift-badge ${cell.value === "stable" ? "stable" : ""}`}>{String(cell.value || "—").replaceAll("_", " ")}</span> },
    { colId: "action", headerName: "Next action", minWidth: 145,
      cellRenderer: (cell: ICellRendererParams<InvoiceRow>) => <span className={`status-pill decision-pill ${cell.data?.decision.action}`}>{cell.data?.decision.action.replaceAll("_", " ")}</span> },
    { colId: "expected_cash", headerName: "Expected cash gain", minWidth: 160,
      valueGetter: cell => cell.data?.decision.expected_cash_gain,
      valueFormatter: cell => cell.data?.decision.expected_cash_gain_currency ? money(cell.data.decision.expected_cash_gain_currency, cell.value || 0) : "—" },
  ], [detailHref]);
  const columns = useMemo(() => baseColumns.map(column => ({ ...column, hide: !visibleColumns.includes(column.colId || column.field || "") })), [baseColumns, visibleColumns]);
  const rowData = useMemo(() => data?.items || [], [data]);
  const drifting = clients.filter(client => ["paying_later", "later_than_usual", "gone_quiet"].includes(client.drift.signal_type));
  const driftCounts = {
    payingLater: drifting.filter(client => client.drift.signal_type === "paying_later").length,
    laterThanUsual: drifting.filter(client => client.drift.signal_type === "later_than_usual").length,
    goneQuiet: drifting.filter(client => client.drift.signal_type === "gone_quiet").length,
  };
  const pageCount = data?.page_count || 1;

  const selectView = (view: string) => updateUrl({ view, age_bucket: null, action_only: null, drifting_only: null });
  const setCardFilter = (filter: "open" | "overdue" | "actions" | "drifting") => {
    if (filter === "open") updateUrl({ view: "open", action_only: null, drifting_only: null, age_bucket: null });
    else if (filter === "overdue") updateUrl({ view: "overdue", action_only: null, drifting_only: null, age_bucket: null });
    else if (filter === "actions") updateUrl({ view: "all", action_only: "1", drifting_only: null, age_bucket: null });
    else updateUrl({ view: "all", drifting_only: "1", action_only: null, age_bucket: null });
  };

  return <AgGridProvider modules={modules}>
    <div className="app-shell">
      <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/performance">Model performance</Link><Link className="back-link" href="/actions">Action queue</Link><span>{authenticated ? "Operator session" : "Receivables workspace"}</span><Link className="btn" href={authenticated ? "/" : "/login"}>{authenticated ? "Operator" : "Operator login"}</Link></div></header>
      <main className="main-wrap">
        {!authenticated && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Deterministic sample invoices. Simulated actions never contact PayPal.</span></div>}
        <div className="heading-row"><div><div className="eyebrow">Receivables intelligence</div><h1>Invoice portfolio</h1><p className="subheading">See what may get paid, who is slowing down, and what needs a human decision.</p></div><div className="toolbar-tools">{authenticated && <button className="btn primary" disabled={busy} onClick={() => void sync()}>{busy ? "Syncing…" : "↻ Sync PayPal"}</button>}</div></div>
        {!authenticated && data && <TimeTravelControl key={`${searchKey}-${data.as_of_max}`} currentAsOf={params.get("as_of") || data.as_of_max} maxAsOf={data.as_of_max} resetBusy={resetBusy} onCommit={value => updateUrl({ as_of: value === data.as_of_max ? null : value }, "replace")} onReset={() => void resetDemo()} />}
        {error && <div className="error-box" role="alert">{error}</div>}
        <section className="panel"><div className="panel-head"><div><div className="panel-title">Ask your receivables</div><div className="panel-subtitle">Answers come from bounded invoice and client summaries.</div></div></div>
          <form className="toolbar-tools" onSubmit={ask}><input className="search" aria-label="Ask your receivables" placeholder="Who owes me the most in EUR?" value={askQuestion} onChange={event => setAskQuestion(event.target.value)}/><button className="btn primary" disabled={askLoading || !askQuestion.trim()}>{askLoading ? "Checking…" : "Ask"}</button></form>
          {askResult && <div className="action-message" role="status" style={{ marginTop: 12 }}>{askResult.answer}</div>}
        </section>
        <section className="panel"><div className="panel-head"><div><div className="panel-title">Morning brief</div><div className="panel-subtitle">Expected cash, highest value actions, and client timing signals.</div></div><button className="btn" onClick={() => void openBrief()}>{briefOpen ? "Hide brief" : "Show brief"}</button></div>
          {briefOpen && (briefLoading ? <div className="empty-inline">Building the structured brief…</div> : brief ? <><div className="fine-print">As of {brief.as_of} · this week through {brief.week_end}</div>
            <div className="cash-week-grid">{brief.expected_cash_this_week.length ? brief.expected_cash_this_week.map(item => <div className="cash-week" key={item.currency}><span>Expected cash</span><strong>{money(item.currency, item.amount)}</strong><small>{item.invoice_count} p50 settlement{item.invoice_count === 1 ? "" : "s"}</small></div>) : <div className="empty-inline">No p50 settlements fall this week.</div>}</div>
            <div className="drift-signal-grid" style={{ marginTop: 12 }}>{brief.top_candidates.length ? brief.top_candidates.map(item => <article className="drift-signal-card" key={item.invoice_id}><div className="drift-signal-top"><Link className="table-link" href={detailHref(item.invoice_id, "view=open&page=1&page_size=25&sort=action&direction=asc")}>{item.invoice_number}</Link><span className="drift-badge">{item.action.replaceAll("_", " ")}</span></div><p>{item.client_name} · {money(item.currency, item.outstanding)}</p><div className="fine-print">Expected gain {money(item.currency, item.expected_cash_gain)} · {item.reason}</div></article>) : <div className="empty-inline">No reminder is pending review today.</div>}</div>
            <div className="fine-print" style={{ marginTop: 12 }}>Clients slowing down: {brief.clients_slowing_down.map(client => `${client.client_name} (${client.signal_type.replaceAll("_", " ")})`).join(" · ") || "none"}</div>
          </> : null)}
        </section>
        {authenticated && <section className="panel"><div className="panel-head"><div><div className="panel-title">PayPal Sandbox</div><div className="panel-subtitle">Create a controlled test invoice and review the last import.</div></div><span className="status-pill">Sandbox only</span></div>
          <div className="fine-print">{syncStatus?.last_run ? `Last sync ${new Date(syncStatus.last_run.completed_at || syncStatus.last_run.started_at).toLocaleString()} · ${syncStatus.last_run.created} imported · ${syncStatus.last_run.updated} updated · ${syncStatus.last_run.status}` : "No PayPal sync has completed yet."} · Webhook retries failed: {syncStatus?.webhook_failures ?? "unavailable"}</div>
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
        </section>}
        {loading && !data && <div className="empty-box">Loading invoice portfolio…</div>}
        {data && <>
          <section className="stats-grid">
            <button className="stat-card stat-button" onClick={() => setCardFilter("open")}><div className="stat-label">Open invoices</div><div className="stat-value">{data.view_counts.open}</div><div className="stat-note">Across {Object.keys(data.currency_totals).length} currencies</div></button>
            <button className="stat-card stat-button" onClick={() => setCardFilter("overdue")}><div className="stat-label">Past due</div><div className="stat-value">{data.view_counts.overdue}</div><div className="stat-note">Invoices with a remaining balance</div></button>
            <button className="stat-card stat-button" onClick={() => setCardFilter("actions")}><div className="stat-label">Ready for review</div><div className="stat-value">{data.ready_for_review_count}</div><div className="stat-note">Pending proposed reminders from today</div></button>
            <button className="stat-card stat-button" onClick={() => setCardFilter("drifting")}><div className="stat-label">Client timing signals</div><div className="stat-value">{data.drifting_client_count}</div><div className="stat-note">Paying later {driftCounts.payingLater} · Later than usual {driftCounts.laterThanUsual} · Gone quiet {driftCounts.goneQuiet}</div></button>
          </section>
          <section className="insight-strip"><div className="drift-strip-head"><div><div className="insight-title">Client payment timing</div><div className="insight-text">Signals sorted by money at risk and signal strength.</div></div><span className="fine-print">Activity signal · not a churn score</span></div>
            {drifting.length ? <div className="drift-signal-grid">{drifting.slice(0, 5).map(client => {
              const drift = client.drift;
              const trigger = drift.signal_type === "gone_quiet"
                ? `${drift.days_since_last_invoice ?? 0} days since last invoice · usual gap ${Math.round(drift.cadence_days || 0)} days`
                : `${Math.round(drift.recent_median_delay_days || 0)}d recent delay · ${Math.round(drift.usual_delay_days || 0)}d usual`;
              return <article className="drift-signal-card" key={client.id}><div className="drift-signal-top"><strong><Link className="table-link" href={`/clients/${encodeURIComponent(client.id)}`}>{client.name}</Link></strong><span className="drift-badge">{drift.signal_type.replaceAll("_", " ")}</span></div><p>{trigger}</p><div className="fine-print">Risk factor {drift.signal_strength.toFixed(2)} · {Object.entries(drift.money_at_risk_by_currency || {}).map(([currency, amount]) => `${currency} ${amount.toLocaleString(undefined, { maximumFractionDigits: 0 })} at risk`).join(" · ") || "No current exposure"}</div></article>;
            })}</div> : <div className="insight-text">No client has a current delay or cadence signal.</div>}
          </section>

          <section className="aging-panel"><div className="panel-head"><div><div className="panel-title">Open invoice aging</div><div className="panel-subtitle">Select a bucket to filter the current portfolio view.</div></div>{selectedAge && <button className="btn" onClick={() => updateUrl({ age_bucket: null })}>Clear bucket</button>}</div>
            <div className="aging-grid">{[["not_due", "Not due"], ["late_1_30", "1–30 days"], ["late_31_60", "31–60 days"], ["late_61_90", "61–90 days"], ["late_90_plus", "90+ days"]].map(([key, label]) => {
              const bucket = data.aging_buckets[key] || { count: 0, amounts: {} };
              return <button className={`aging-bucket ${selectedAge === key ? "selected" : ""}`} key={key} onClick={() => updateUrl({ view: "open", age_bucket: selectedAge === key ? null : key, action_only: null, drifting_only: null })}>
                <span>{label}</span><strong>{bucket.count}</strong><small>{Object.entries(bucket.amounts).map(([currency, amount]) => money(currency, amount)).join(" · ") || "No balance"}</small>
              </button>;
            })}</div>
          </section>

          <section className="cash-forecast-panel"><div className="panel-head"><div><div className="panel-title">Expected cash in · next 8 weeks</div><div className="panel-subtitle">Outstanding balances grouped by their p50 settlement week.</div></div></div>
            {data.expected_cash_by_week.length ? <div className="cash-week-grid">{data.expected_cash_by_week.map(entry => <div className="cash-week" key={`${entry.week_start}-${entry.currency}`}><span>Week of {new Date(`${entry.week_start}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span><strong>{money(entry.currency, entry.amount)}</strong><small>{entry.invoice_count} invoice{entry.invoice_count === 1 ? "" : "s"}</small></div>)}</div> : <div className="empty-inline">No open invoice has a p50 settlement date inside the next eight weeks.</div>}
          </section>

          <section className="grid-panel">
            <div className="grid-toolbar"><div><div className="grid-title">Invoices <span className="fine-print">{data.count.toLocaleString()} records in this view</span></div><div className="fine-print" style={{ marginTop: 5 }}>{data.models.length ? `Forecast: ${data.models.join(", ")}` : "Forecast unavailable"}{lastSync ? ` · Last sync ${lastSync}` : ""}</div></div>
              <div className="toolbar-tools"><SearchInput key={urlQuery} query={urlQuery} onDebouncedQuery={value => updateUrl({ q: value || null }, "replace")} />
                <select className="select" aria-label="Filter by client" value={selectedClient} onChange={event => updateUrl({ client_id: event.target.value || null })}><option value="">All clients</option>{clients.map(client => <option key={client.id} value={client.id}>{client.name}</option>)}</select>
                <select className="select" aria-label="Sort invoices" value={`${currentSort}:${currentDirection}`} onChange={event => { const [sort, direction] = event.target.value.split(":"); updateUrl({ sort, direction }); }}>
                  <option value="action:asc">Action queue</option><option value="expected_cash:desc">Expected cash gain</option><option value="p14:desc">Paid in 14d · high to low</option><option value="p14:asc">Paid in 14d · low to high</option><option value="due:asc">Due date · earliest</option><option value="balance:desc">Balance · high to low</option><option value="client:asc">Client · A to Z</option>
                </select>
                <details className="column-chooser"><summary>Columns</summary><div className="column-menu">{columnChoices.map(([id, label]) => <label key={id}><input type="checkbox" checked={visibleColumns.includes(id)} onChange={event => setVisibleColumns(columns => event.target.checked ? [...columns, id] : columns.filter(column => column !== id))}/>{label}</label>)}</div></details>
                <button className="btn" onClick={() => gridApi.current?.exportDataAsCsv({ fileName: "late-payer-current-page.csv" })}>Export CSV</button>
              </div>
            </div>
            <nav className="view-tabs" aria-label="Invoice views">{viewTabs.map(([key, label]) => <button className={currentView === key && !actionOnly && !driftingOnly ? "active" : ""} key={key} onClick={() => selectView(key)}>{label}<span>{data.view_counts[key] || 0}</span></button>)}</nav>
            {actionOnly && <div className="filter-note">Showing only today&apos;s pending proposed reminders. <button onClick={() => updateUrl({ action_only: null })}>Clear</button></div>}
            {driftingOnly && <div className="filter-note">Showing invoices for clients with a timing signal. <button onClick={() => updateUrl({ drifting_only: null })}>Clear</button></div>}
            <div className="grid-wrap">{rowData.length ? <AgGridReact<InvoiceRow> rowData={rowData} columnDefs={columns} theme={gridTheme} defaultColDef={{ sortable: false, filter: false, resizable: true }} rowHeight={57} headerHeight={44} animateRows={false} suppressCellFocus={false} onGridReady={event => { gridApi.current = event.api; }} /> : <div className="empty-box">No invoices match this view and its filters. Change a filter or select another view.</div>}</div>
            <div className="grid-footer"><div className="currency-total-row">{Object.entries(data.currency_totals).map(([currency, amount]) => <span key={currency}>Filtered open {currency} <strong>{Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>)}</div>
              <div className="pager"><label>Rows <select className="select" aria-label="Rows per page" value={pageSize} onChange={event => updateUrl({ page_size: event.target.value })}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><span>Page {currentPage} of {pageCount}</span><button className="btn" disabled={currentPage <= 1} onClick={() => updateUrl({ page: String(currentPage - 1) }, "push", false)}>Previous</button><button className="btn" disabled={currentPage >= pageCount} onClick={() => updateUrl({ page: String(currentPage + 1) }, "push", false)}>Next</button></div>
            </div>
          </section>
        </>}
      </main>
    </div>
  </AgGridProvider>;
}

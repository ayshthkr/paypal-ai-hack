"use client";

import { AllCommunityModule, ModuleRegistry, themeQuartz } from "ag-grid-community";
import type { ColDef, GridApi, ICellRendererParams, ModelUpdatedEvent } from "ag-grid-community";
import { AgGridProvider, AgGridReact } from "ag-grid-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

ModuleRegistry.registerModules([AllCommunityModule]);

type Drift = { signal: string; signal_type: string; available: boolean; settled_count: number; message: string; severity_score: number; signal_strength: number; recent_median_delay_days?: number | null; usual_delay_days?: number | null; days_since_last_invoice?: number | null; cadence_days?: number | null; activity_probability?: number | null; money_at_risk_by_currency?: Record<string, number> };
type InvoiceRow = {
  id: string; invoice_number: string; client_name: string; due_date: string; days_to_due: number;
  outstanding: string; currency: string; status: string; source: string;
  forecast: null | { quantiles: Record<string, { days: number; date: string } | null>; probabilities: Record<string, number>; model: string };
  drift: Drift; decision: { action: string; tone: string; reason: string };
};
type InvoiceResult = { items: InvoiceRow[]; count: number; currency_totals: Record<string, string>; synthetic_demo: boolean; models: string[]; ready_for_review_count: number };
type ClientRow = { id: string; name: string; drift: Drift };

const modules = [AllCommunityModule];
const gridTheme = themeQuartz.withParams({ accentColor: "#155eef", backgroundColor: "#ffffff", borderColor: "#e6ebee", fontFamily: "Arial, sans-serif", fontSize: "12px", headerBackgroundColor: "#f7f9fa", headerFontWeight: 700, rowHoverColor: "#f5f8fc" });

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export default function HomePage() {
  const [data, setData] = useState<InvoiceResult | null>(null);
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [filteredRows, setFilteredRows] = useState<InvoiceRow[] | null>(null);
  const refreshInFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const pending = Promise.all([
      getJson<InvoiceResult>("/api/invoices?limit=500"), getJson<{ items: ClientRow[] }>("/api/clients"),
      getJson<{ authenticated: boolean }>("/api/auth/me"),
    ]).then(([invoices, clientData, auth]) => {
      setData(invoices); setClients(clientData.items); setAuthenticated(auth.authenticated); setError("");
    }).catch(cause => {
      setError(cause instanceof Error ? cause.message : "Could not load invoices.");
    }).finally(() => {
      setLoading(false);
      refreshInFlight.current = null;
    });
    refreshInFlight.current = pending;
    return pending;
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const sync = async () => {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/sync", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "PayPal sync failed.");
      setLastSync(new Date().toLocaleTimeString());
      await refresh();
      if (result.errors?.length) setError(`Sync completed with ${result.errors.length} invoice error(s).`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "PayPal sync failed."); }
    finally { setBusy(false); }
  };

  const columns = useMemo<ColDef<InvoiceRow>[]>(() => [
    { field: "invoice_number", headerName: "Invoice", minWidth: 140, flex: .8, cellRenderer: (params: ICellRendererParams<InvoiceRow>) => <Link className="table-link" href={`/invoices/${params.data?.id}`}>{params.value}</Link> },
    { field: "client_name", headerName: "Client", minWidth: 160, flex: 1.1 },
    { field: "outstanding", headerName: "Balance", minWidth: 120, valueFormatter: params => params.data ? `${params.data.currency} ${Number(params.value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "" },
    { field: "due_date", headerName: "Due", minWidth: 115, valueFormatter: params => params.value ? new Date(`${params.value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "" },
    { field: "status", headerName: "Status", minWidth: 130, cellRenderer: (params: ICellRendererParams<InvoiceRow>) => <span className={`status-pill ${String(params.value).toLowerCase()}`}>{String(params.value).replaceAll("_", " ")}</span> },
    { colId: "p50", headerName: "Predicted", minWidth: 125, valueGetter: params => params.data?.forecast?.quantiles.p50?.date || null, valueFormatter: params => !params.data?.forecast ? "—" : params.value ? new Date(`${params.value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "Beyond 180d" },
    { colId: "window", headerName: "p10–p90 range", minWidth: 175, valueGetter: params => params.data?.forecast ? { p10: params.data.forecast.quantiles.p10?.date, p90: params.data.forecast.quantiles.p90?.date } : null, valueFormatter: params => {
      if (!params.value) return "—";
      const formatDate = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
      if (!params.value.p10) return "Beyond horizon";
      return `${formatDate(params.value.p10)} – ${params.value.p90 ? formatDate(params.value.p90) : "beyond horizon"}`;
    } },
    { colId: "p14", headerName: "Paid in 14d", minWidth: 115, valueGetter: params => params.data?.forecast?.probabilities["14d"], valueFormatter: params => params.value == null ? "—" : `${Math.round(params.value * 100)}%` },
    { colId: "drift", headerName: "Client drift", minWidth: 130, valueGetter: params => params.data?.drift?.signal, cellRenderer: (params: ICellRendererParams<InvoiceRow>) => <span className={`drift-badge ${params.value === "stable" ? "stable" : ""}`}>{String(params.value || "—").replaceAll("_", " ")}</span> },
    { colId: "action", headerName: "Next action", minWidth: 130, cellRenderer: (params: ICellRendererParams<InvoiceRow>) => <span className={`status-pill decision-pill ${params.data?.decision.action}`}>{params.data?.decision.action}</span> },
  ], []);

  const overdue = data?.items.filter(row => row.days_to_due < 0 && Number(row.outstanding) > 0 && !["PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"].includes(row.status)).length || 0;
  const recommendationCount = data?.ready_for_review_count || 0;
  const drifting = clients.filter(client => ["paying_later", "later_than_usual", "gone_quiet"].includes(client.drift.signal_type));
  const driftCounts = {
    payingLater: drifting.filter(client => client.drift.signal_type === "paying_later").length,
    laterThanUsual: drifting.filter(client => client.drift.signal_type === "later_than_usual").length,
    goneQuiet: drifting.filter(client => client.drift.signal_type === "gone_quiet").length,
  };
  const rowData = useMemo(() => data?.items || [], [data]);
  const updateVisibleRows = useCallback((event: ModelUpdatedEvent<InvoiceRow> | { api: GridApi<InvoiceRow> }) => {
    const visible: InvoiceRow[] = [];
    event.api.forEachNodeAfterFilterAndSort(node => { if (node.data) visible.push(node.data); });
    setFilteredRows(visible);
  }, []);
  const totalRows = filteredRows ?? rowData;
  const visibleTotals = useMemo(() => totalRows.reduce<Record<string, number>>((totals, row) => {
    if (Number(row.outstanding) > 0 && !["PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"].includes(row.status)) {
      totals[row.currency] = (totals[row.currency] || 0) + Number(row.outstanding);
    }
    return totals;
  }, {}), [totalRows]);

  return <AgGridProvider modules={modules}>
    <div className="app-shell">
      <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><span>{authenticated ? "Operator session" : "Receivables workspace"}</span><Link className="btn" href={authenticated ? "/" : "/login"}>{authenticated ? "Operator" : "Operator login"}</Link></div></header>
      <main className="main-wrap">
        {!authenticated && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Deterministic sample invoices. Simulated actions never contact PayPal.</span></div>}
        <div className="heading-row"><div><div className="eyebrow">Receivables intelligence</div><h1>Invoice portfolio</h1><p className="subheading">See what may get paid, who is slowing down, and what needs a human decision.</p></div><div className="toolbar-tools">{authenticated && <button className="btn primary" disabled={busy} onClick={() => void sync()}>{busy ? "Syncing…" : "↻ Sync PayPal"}</button>}</div></div>
        {error && <div className="error-box" role="alert">{error}</div>}
        {loading && !data ? <div className="empty-box">Loading invoice portfolio…</div> : null}
        {data && <>
          <section className="stats-grid">
            <div className="stat-card"><div className="stat-label">Open invoices</div><div className="stat-value">{data.items.filter(row => Number(row.outstanding) > 0 && !["PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"].includes(row.status)).length}</div><div className="stat-note">Across {Object.keys(data.currency_totals).length} currencies</div></div>
            <div className="stat-card"><div className="stat-label">Past due</div><div className="stat-value">{overdue}</div><div className="stat-note">Invoices with a remaining balance</div></div>
            <div className="stat-card"><div className="stat-label">Ready for review</div><div className="stat-value">{recommendationCount}</div><div className="stat-note">Rule-based reminder suggestions</div></div>
            <div className="stat-card"><div className="stat-label">Client timing signals</div><div className="stat-value">{drifting.length}</div><div className="stat-note">Paying later {driftCounts.payingLater} · Later than usual {driftCounts.laterThanUsual} · Gone quiet {driftCounts.goneQuiet}</div></div>
          </section>
          <section className="insight-strip"><div className="drift-strip-head"><div><div className="insight-title">Client payment timing</div><div className="insight-text">Signals sorted by money at risk and signal strength.</div></div><span className="fine-print">Activity signal · not a churn score</span></div>
            {drifting.length ? <div className="drift-signal-grid">{drifting.slice(0, 5).map(client => {
              const drift = client.drift;
              const trigger = drift.signal_type === "gone_quiet"
                ? `${drift.days_since_last_invoice ?? 0} days since last invoice · usual gap ${Math.round(drift.cadence_days || 0)} days`
                : `${Math.round(drift.recent_median_delay_days || 0)}d recent delay · ${Math.round(drift.usual_delay_days || 0)}d usual`;
              return <article className="drift-signal-card" key={client.id}><div className="drift-signal-top"><strong>{client.name}</strong><span className="drift-badge">{drift.signal_type.replaceAll("_", " ")}</span></div><p>{trigger}</p><div className="fine-print">Risk factor {drift.signal_strength.toFixed(2)} · {Object.entries(drift.money_at_risk_by_currency || {}).map(([currency, amount]) => `${currency} ${amount.toLocaleString(undefined, { maximumFractionDigits: 0 })} at risk`).join(" · ") || "No current exposure"}</div></article>;
            })}</div> : <div className="insight-text">No client has a current delay or cadence signal.</div>}
          </section>
          <section className="grid-panel"><div className="grid-toolbar"><div><div className="grid-title">Invoices <span className="fine-print">{data.count} records</span></div><div className="fine-print" style={{ marginTop: 5 }}>{data.models.length ? `Forecast: ${data.models.join(", ")}` : "Forecast unavailable"}{lastSync ? ` · Last sync ${lastSync}` : ""}</div></div><div className="toolbar-tools"><input className="search" aria-label="Search invoices" placeholder="Search client, invoice, status" value={query} onChange={event => setQuery(event.target.value)} /></div></div>
            <div className="grid-wrap"><AgGridReact<InvoiceRow> rowData={rowData} columnDefs={columns} theme={gridTheme} quickFilterText={query} defaultColDef={{ sortable: true, filter: true, resizable: true }} rowHeight={57} headerHeight={44} animateRows={false} suppressCellFocus={false} onGridReady={updateVisibleRows} onModelUpdated={updateVisibleRows} /></div>
            <div className="currency-total-row">{Object.entries(visibleTotals).map(([currency, amount]) => <span key={currency}>Filtered open {currency} <strong>{amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>)}</div>
          </section>
        </>}
      </main>
    </div>
  </AgGridProvider>;
}

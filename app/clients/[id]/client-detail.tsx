"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type DelayInvoice = {
  id: string; invoice_number: string; issue_date: string; due_date: string; settled_at: string | null;
  days_late: number | null; total: string; paid: string; outstanding: string; currency: string; status: string;
  forecast: { quantiles: Record<string, { date: string; days: number } | null>; probabilities: Record<string, number> } | null;
  decision: { action: string; reason: string };
};
type ClientFacts = {
  as_of: string;
  client: { id: string; name: string; source: string };
  history: { invoice_count: number; settled_invoice_count: number; median_days_late: number | null; partial_payment_invoice_count: number; median_invoice_amount_by_currency: Record<string, number> };
  drift: { signal: string; signal_type: string; available: boolean; message: string; settled_count: number; severity_score: number; signal_strength: number; recent_median_delay_days?: number | null; usual_delay_days?: number | null; activity_probability?: number | null };
  invoices: DelayInvoice[];
};

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function getFacts(id: string): Promise<ClientFacts> {
  const response = await fetch(`/api/facts/clients/${encodeURIComponent(id)}`, { cache: "no-store", credentials: "same-origin" });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(value.detail || `Request failed (${response.status})`, response.status);
  return value as ClientFacts;
}

function formatDate(value: string | null) {
  if (!value) return "—";
  return new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function money(currency: string, value: number | string) {
  return `${currency} ${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function ClientDetail({ id }: { id: string }) {
  const [data, setData] = useState<ClientFacts | null>(null);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getFacts(id).then(value => {
      if (!cancelled) { setData(value); setError(""); setNotFound(false); }
    }).catch(cause => {
      if (!cancelled) {
        setError(cause instanceof Error ? cause.message : "Could not load client history.");
        setNotFound(cause instanceof ApiError && cause.status === 404);
      }
    });
    return () => { cancelled = true; };
  }, [id]);

  if (error && !data && notFound) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link></header><main className="main-wrap"><div className="panel"><h1>Client not found</h1><p className="subheading">This client is unavailable or your operator session has expired.</p><Link className="btn" href="/">Back to portfolio</Link>{error.includes("login") && <Link className="btn" href="/login">Operator login</Link>}</div></main></div>;
  if (!data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link></header><main className="main-wrap">{error ? <div className="error-box">{error} {error.includes("login") && <Link className="table-link" href="/login">Operator login</Link>}</div> : <div className="empty-box">Loading client history…</div>}</main></div>;

  const balances = data.invoices.reduce<Record<string, number>>((totals, invoice) => {
    if (!new Set(["PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"]).has(invoice.status) && Number(invoice.outstanding) > 0) {
      totals[invoice.currency] = (totals[invoice.currency] || 0) + Number(invoice.outstanding);
    }
    return totals;
  }, {});
  const balanceEntries = Object.entries(balances);
  const balanceAmounts = balanceEntries.map(([currency, amount]) => money(currency, amount));
  const balanceHeadline = balanceAmounts.length === 1 ? balanceAmounts[0] : balanceAmounts.length ? `${balanceAmounts.length} currencies` : "None";
  const delays = data.invoices.filter(invoice => invoice.days_late != null).sort((a, b) => (a.settled_at || "").localeCompare(b.settled_at || "")).slice(-24);
  const maxDelay = Math.max(1, ...delays.map(invoice => Math.abs(invoice.days_late || 0)));

  return <div className="app-shell">
    <header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← All invoices</Link></div></header>
    <main className="main-wrap">
      {data.client.source === "demo" && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Invoice and payment history is generated for the receivables demo.</span></div>}
      <div className="heading-row"><div><div className="eyebrow">Client detail · {data.client.source === "demo" ? "Synthetic" : "PayPal Sandbox"}</div><h1>{data.client.name}</h1><p className="subheading">{data.history.invoice_count} invoices · {data.history.settled_invoice_count} fully settled · as of {formatDate(data.as_of)}</p></div><span className="status-pill">{data.drift.signal.replaceAll("_", " ")}</span></div>
      <section className="stats-grid">
        <div className="stat-card"><div className="stat-label">Settled invoices</div><div className="stat-value">{data.history.settled_invoice_count}</div><div className="stat-note">{data.history.median_days_late == null ? "No settled delay history yet" : `Median ${data.history.median_days_late} days vs due date`}</div></div>
        <div className="stat-card"><div className="stat-label">Partial-payment history</div><div className="stat-value">{data.history.partial_payment_invoice_count}</div><div className="stat-note">Invoices with multiple payment records</div></div>
        <div className="stat-card"><div className="stat-label">Current open balance</div><div className="stat-value">{balanceHeadline}</div><div className="stat-note">{balanceAmounts.join(" · ") || "No open balance"}</div></div>
        <div className="stat-card"><div className="stat-label">Activity signal</div><div className="stat-value">{data.drift.signal_type.replaceAll("_", " ")}</div><div className="stat-note">{data.drift.message}</div></div>
      </section>
      <section className="panel"><div className="panel-head"><div><div className="panel-title">Payment delay history</div><div className="panel-subtitle">Days from each invoice due date to full settlement, ordered by payment date.</div></div><span className="fine-print">{delays.length} settled invoices shown · signal strength {data.drift.signal_strength.toFixed(2)}</span></div>
        {delays.length ? <><div className="delay-history" role="img" aria-label="Invoice settlement delays relative to due dates">{delays.map(invoice => {
          const delay = invoice.days_late || 0;
          const height = Math.max(5, Math.round(Math.abs(delay) / maxDelay * 72));
          const direction = delay > 0 ? "late" : delay < 0 ? "early" : "on-time";
          return <div className="delay-history-item" key={invoice.id} title={`${invoice.invoice_number}: ${delay > 0 ? `${delay} days late` : delay < 0 ? `${Math.abs(delay)} days early` : "on time"}`}><div className="delay-history-track"><div className={`delay-history-bar ${direction}`} style={delay >= 0 ? { height: `${height}px`, bottom: "50%" } : { height: `${height}px`, top: "50%" }}/></div><span>{formatDate(invoice.settled_at).slice(0, 6)}</span></div>;
        })}</div><div className="fine-print">Bars above the baseline indicate late payment; blue bars indicate on-time or early payment.</div></> : <div className="empty-inline">There are no fully settled invoices to show in the delay history.</div>}
        <div className="drift-card" style={{ marginTop: 14 }}><strong>{data.drift.available ? `${data.drift.settled_count} invoices support the current signal` : "Not enough settled history for a drift signal"}</strong><p>{data.drift.message}</p></div>
      </section>
      <section className="grid-panel"><div className="grid-toolbar"><div><div className="grid-title">Client invoices <span className="fine-print">{data.invoices.length} records</span></div><div className="fine-print" style={{ marginTop: 5 }}>Forecast and next-action facts are shown per invoice.</div></div></div>
        {data.invoices.length ? <div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Invoice</th><th>Issued</th><th>Due</th><th>Status</th><th>Outstanding</th><th>Paid in 14d</th><th>Next action</th></tr></thead><tbody>{data.invoices.map(invoice => <tr key={invoice.id}><td><Link className="table-link" href={`/invoices/${encodeURIComponent(invoice.id)}?view=all&client_id=${encodeURIComponent(data.client.id)}&page=1&page_size=25&sort=action&direction=asc`}>{invoice.invoice_number}</Link></td><td>{formatDate(invoice.issue_date)}</td><td>{formatDate(invoice.due_date)}</td><td><span className={`status-pill ${invoice.status.toLowerCase()}`}>{invoice.status.replaceAll("_", " ")}</span></td><td>{money(invoice.currency, invoice.outstanding)}</td><td>{invoice.forecast ? `${Math.round(invoice.forecast.probabilities["14d"] * 100)}%` : "—"}</td><td>{invoice.decision.action.replaceAll("_", " ")}</td></tr>)}</tbody></table></div> : <div className="empty-box">This client has no invoices.</div>}
      </section>
    </main>
  </div>;
}

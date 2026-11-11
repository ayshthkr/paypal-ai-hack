"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { requestJson, RequestError, clearReads, ensureDemoSession, API_REQUEST_TIMEOUT_MS } from "../../lib/api";
import { decisionLabel, signalLabel, statusLabel, termLabel } from "../../lib/labels";
import { useEffect, useState } from "react";

type DelayInvoice = {
  id: string; invoice_number: string; issue_date: string; due_date: string; settled_at: string | null; payment_term_type: string | null; disputed: boolean;
  days_late: number | null; total: string; paid: string; outstanding: string; currency: string; status: string;
  forecast: { quantiles: Record<string, { date: string; days: number } | null>; probabilities: Record<string, number> } | null;
  decision: { action: string; reason: string };
};
type ClientFacts = {
  as_of: string;
  client: { id: string; name: string; source: string };
  history: { invoice_count: number; settled_invoice_count: number; median_days_late: number | null; partial_payment_invoice_count: number; median_invoice_amount_by_currency: Record<string, number> };
  drift: { signal: string; signal_type: string; available: boolean; message: string; settled_count: number; severity_score: number; signal_strength: number; recent_median_delay_days?: number | null; usual_delay_days?: number | null; activity_probability?: number | null; right_censored_overdue_count?: number; max_open_overdue_days?: number | null; money_at_risk_basis?: string };
  invoices: DelayInvoice[];
  stories?: string[];
};
type WhatIf = {
  as_of: string; currency: string; representative_amount: string;
  baseline: { payment_term_type: string; partial_payment_allowed: boolean; p50: { date: string; days: number } | null; probabilities: Record<string, number>; limited_history: boolean };
  with_terms: { payment_term_type: string; partial_payment_allowed: boolean; p50: { date: string; days: number } | null; probabilities: Record<string, number>; limited_history: boolean };
  with_partial_payment: { payment_term_type: string; partial_payment_allowed: boolean; p50: { date: string; days: number } | null; probabilities: Record<string, number>; limited_history: boolean };
};

const ApiError = RequestError;
const getFacts = (id: string) => requestJson<ClientFacts>(`/api/facts/clients/${encodeURIComponent(id)}`);

function formatDate(value: string | null) {
  if (!value) return "—";
  return new Date(`${value.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
}

function money(currency: string, value: number | string) {
  return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function ClientDetail({ id }: { id: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const page = Math.max(1, Number(searchParams.get("page")) || 1);
  const statusFilter = searchParams.get("status") || "all";
  const setFilter = (key: string, value: string) => { const params = new URLSearchParams(searchParams.toString()); params.set(key, value); if (key !== "page") params.set("page", "1"); router.replace(`?${params.toString()}`, { scroll: false }); };
  const [data, setData] = useState<ClientFacts | null>(null);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);
  const [whatIf, setWhatIf] = useState<WhatIf | null>(null);
  const [whatIfTerm, setWhatIfTerm] = useState("NET_15");
  const [whatIfLoading, setWhatIfLoading] = useState(false);
  const [whatIfError, setWhatIfError] = useState("");
  const [reload, setReload] = useState(0);

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
  }, [id, reload]);

  if (error && !data && notFound) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></header><main className="main-wrap"><div className="panel"><h1>Client not found</h1><p className="subheading">This client is unavailable or your operator session has expired.</p><Link className="btn" href="/">Back to portfolio</Link>{error.includes("login") && <Link className="btn" href="/login">Operator login</Link>}</div></main></div>;
  if (!data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></header><main className="main-wrap">{error ? <div className="error-box">{error.includes("unavailable") || error.includes("502") ? "The API may be waking up. Refresh to load client history." : error} <button className="btn" onClick={() => { clearReads(); setReload(value => value + 1); }}>Retry</button> {error.includes("login") && <Link className="table-link" href="/login">Operator login</Link>}</div> : <div className="empty-box">Loading client history…</div>}</main></div>;

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
  const isSynthetic = data.client.source.startsWith("demo");

  const runWhatIf = async () => {
    setWhatIfLoading(true); setWhatIfError("");
    try {
      await ensureDemoSession();
      const response = await fetch(`/api/clients/${encodeURIComponent(id)}/what-if`, { method: "POST", cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(API_REQUEST_TIMEOUT_MS), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ payment_term_type: whatIfTerm }) });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      setWhatIf(value as WhatIf);
    } catch (cause) { setWhatIfError(cause instanceof Error ? cause.message : "Could not score the terms scenario."); }
    finally { setWhatIfLoading(false); }
  };

  const filteredInvoices = data.invoices.filter(invoice => statusFilter === "all" || (statusFilter === "open" ? Number(invoice.outstanding) > 0 && !["CANCELLED", "VOIDED"].includes(invoice.status) : invoice.status === statusFilter));
  const pageCount = Math.max(1, Math.ceil(filteredInvoices.length/25));
  const pageInvoices = filteredInvoices.slice((page-1)*25, page*25);
  const forecastDate = (value: WhatIf["baseline"]["p50"]) => value ? formatDate(value.date) : "Beyond 180 days";

  return <div className="app-shell">
    <header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href={`/?client_id=${encodeURIComponent(id)}&view=all`}>← Client portfolio</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></div></header>
    <main className="main-wrap">
      {isSynthetic && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Invoice and payment history is generated for this isolated browser session.</span></div>}
      <div className="heading-row"><div><div className="eyebrow">Client detail · {isSynthetic ? "Synthetic" : "PayPal Sandbox"}</div><h1>{data.client.name}</h1><p className="subheading">{data.history.invoice_count} invoices · {data.history.settled_invoice_count} fully settled · as of {formatDate(data.as_of)}</p></div><span className="status-pill">{signalLabel(data.drift.signal_type)}</span></div>
      {data.history.settled_invoice_count < 5 && <div className="delivery-note">Limited history · fewer than 5 settlements; forecasts use pooled priors.</div>}
      <section className="stats-grid">
        <div className="stat-card"><div className="stat-label">Settled invoices</div><div className="stat-value">{data.history.settled_invoice_count}</div><div className="stat-note">{data.history.median_days_late == null ? "No settled delay history yet" : `Median ${data.history.median_days_late} days vs due date`}</div></div>
        <div className="stat-card"><div className="stat-label">Partial-payment history</div><div className="stat-value">{data.history.partial_payment_invoice_count}</div><div className="stat-note">Invoices with multiple payment records</div></div>
        <div className="stat-card"><div className="stat-label">Current open balance</div><div className="stat-value">{balanceHeadline}</div><div className="stat-note">{balanceAmounts.join(" · ") || "No open balance"}</div></div>
        <div className="stat-card"><div className="stat-label">Activity signal</div><div className="stat-value">{signalLabel(data.drift.signal_type)}</div><div className="stat-note">{data.drift.message}</div><div className="fine-print" style={{ marginTop: 6 }}>{data.drift.money_at_risk_basis || "Risk estimate uses this client's open balances and typical settled invoice amount, kept separate by currency."}</div></div>
      </section>
      <section className="panel"><div className="panel-head"><div><div className="panel-title">Payment delay history</div><div className="panel-subtitle">Days from each invoice due date to full settlement. Bars are in invoice order; hover a bar for its invoice and delay.</div></div><span className="fine-print">Last {delays.length} of {data.history.settled_invoice_count} settlements shown · signal strength {data.drift.signal_strength.toFixed(2)}</span></div>
        {delays.length ? <><div className="delay-history" role="img" aria-label="Invoice settlement delays relative to due dates">{delays.map((invoice, index) => {
          const delay = invoice.days_late || 0;
          const height = Math.max(5, Math.round(Math.abs(delay) / maxDelay * 72));
          const direction = delay > 0 ? "late" : delay < 0 ? "early" : "on-time";
          return <div className="delay-history-item" key={invoice.id} title={`${invoice.invoice_number} · settled ${formatDate(invoice.settled_at)} · ${delay > 0 ? `${delay} days late` : delay < 0 ? `${Math.abs(delay)} days early` : "on time"}`}><div className="delay-history-track"><div className={`delay-history-bar ${direction}`} style={delay >= 0 ? { height: `${height}px`, bottom: "50%" } : { height: `${height}px`, top: "50%" }}/></div><span>#{index + 1}</span></div>;
        })}</div><div className="fine-print">Bars above the baseline indicate late payment; blue bars indicate on-time or early payment.</div></> : <div className="empty-inline">There are no fully settled invoices to show in the delay history.</div>}
        <div className="drift-card" style={{ marginTop: 14 }}><strong>{data.drift.available ? `${data.drift.settled_count} invoices support the current signal` : "Not enough settled history for a drift signal"}</strong><p>{data.drift.message}</p></div>
      </section>
      {data.stories?.length ? <section className="panel"><div className="panel-title">Client stories</div><ul className="reasons">{data.stories.map(story => <li key={story}><span className="reason-mark">✓</span>{story}</li>)}</ul></section> : null}
      <section className="panel"><div className="panel-head"><div><div className="panel-title">What if the next invoice had different terms?</div><div className="panel-subtitle">Scores a representative {data.history.median_invoice_amount_by_currency && Object.values(data.history.median_invoice_amount_by_currency)[0] != null ? "client invoice" : "invoice"} using the current model, then changes only payment terms or partial-payment eligibility.</div></div></div>
        <div className="fine-print" role="note" style={{ marginBottom: 12 }}>These are hypothetical model comparisons, not causal estimates. Historical term and partial-payment associations may reflect which clients received those terms. The output shows point scenarios without a model uncertainty interval. P50 means full settlement, so partial payments can arrive before the displayed date and the partial-payment scenario can still have a later full-settlement P50.</div>
        <div className="toolbar-tools"><label className="fine-print">Payment terms <select className="select" value={whatIfTerm} onChange={event => setWhatIfTerm(event.target.value)}><option value="NET_15">NET 15</option><option value="NET_30">NET 30</option></select></label><button className="btn primary" disabled={whatIfLoading} onClick={() => void runWhatIf()}>{whatIfLoading ? "Scoring…" : "Compare scenarios"}</button></div>
        {whatIfError && <div className="error-box" role="alert" style={{ marginTop: 12 }}>{whatIfError.includes("unavailable") || whatIfError.includes("502") ? "The API may be waking up. Retry the scenario." : whatIfError} <button className="btn" onClick={() => void runWhatIf()}>Retry</button></div>}
        {whatIf && <><div className="fine-print" style={{ marginTop: 12 }}>Model {whatIf.baseline.limited_history ? "uses a limited-history baseline" : "uses this client’s observed payment history"} · {whatIf.currency} {Number(whatIf.representative_amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} representative amount · as of {formatDate(whatIf.as_of)}</div><div className="what-if-grid">{[["NET 30 · no partial payments", whatIf.baseline], [`${whatIfTerm.replaceAll("_", " ")} · no partial payments`, whatIf.with_terms], [`${whatIfTerm.replaceAll("_", " ")} · partial payments allowed`, whatIf.with_partial_payment]].map(([label, result]) => { const scenario = result as WhatIf["baseline"]; return <article className="what-if-card" key={String(label)}><strong>{String(label)}</strong><span>p50 full settlement</span><b>{forecastDate(scenario.p50)}</b><span>Paid in 14 days</span><b>{Math.round((scenario.probabilities["14d"] || 0) * 100)}%</b></article>; })}</div></>}
      </section>
      <section className="grid-panel"><div className="grid-toolbar"><div><label>Invoice status <select aria-label="Client invoice status" value={statusFilter} onChange={event => setFilter("status", event.target.value)}><option value="all">All</option><option value="open">Open</option><option value="PAID">Paid</option><option value="OVERDUE">Overdue</option></select></label><div className="grid-title">Client invoices <span className="fine-print">{filteredInvoices.length} filtered records · all pages</span></div><div className="fine-print" style={{ marginTop: 5 }}>Forecast and next-action facts are shown per invoice.</div></div></div>
        {data.invoices.length ? <div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Invoice</th><th>Issued</th><th>Due</th><th>Terms</th><th>Status</th><th>Outstanding</th><th>Paid in 14d</th><th>Next action</th></tr></thead><tbody>{pageInvoices.map(invoice => <tr key={invoice.id}><td><Link className="table-link" href={`/invoices/${encodeURIComponent(invoice.id)}?view=all&client_id=${encodeURIComponent(data.client.id)}&page=1&page_size=25&sort=action&direction=asc`}>{invoice.invoice_number}</Link></td><td>{formatDate(invoice.issue_date)}</td><td>{formatDate(invoice.due_date)}</td><td>{termLabel(invoice.payment_term_type)}</td><td><span className={`status-pill ${invoice.disputed ? "disputed" : invoice.status.toLowerCase()}`}>{statusLabel(invoice.status, invoice.disputed)}</span></td><td>{money(invoice.currency, invoice.outstanding)}</td><td>{invoice.forecast ? `${Math.round(invoice.forecast.probabilities["14d"] * 100)}%` : "—"}</td><td>{decisionLabel(invoice.decision.action, invoice.status, invoice.disputed)}</td></tr>)}</tbody></table></div> : <div className="empty-box">This client has no invoices.</div>}
      <div className="grid-footer"><button className="btn" disabled={page <= 1} onClick={() => setFilter("page", String(page-1))}>Previous</button><span>Page {page} of {pageCount}</span><button className="btn" disabled={page >= pageCount} onClick={() => setFilter("page", String(page+1))}>Next</button></div>
      </section>
    </main>
  </div>;
}

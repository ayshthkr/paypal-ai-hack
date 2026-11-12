"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

type Snapshot = {
  snapshot_version: string; as_of: string; generated_from: string; invoice_count: number; open_count: number;
  overdue_count: number; disputed_count: number; currency_totals: Record<string, string>;
  aging_buckets: Record<string, { count: number; amounts: Record<string, string> }>;
  sample: { id: string; invoice_number: string; client_name: string; currency: string; outstanding: string; due_date: string; status: string; disputed: boolean }[];
  workflow_examples: Record<string, { title: string; body: string; invoice?: { id: string; invoice_number: string; client_name: string; currency: string; total: string; paid: string; outstanding: string; issue_date: string; due_date: string; payment_term_type: string; partial_payment_allowed: boolean; partial_amount: string; partial_at: string | null; status: string; disputed: boolean; overdue_days: number; reminder_count: number; reminder_dates: string[]; settled_at: string | null } | null; facts: string[] }>;
};

const stops = [
  ["Portfolio", "Open balances, past-due exposure, and the reconciliation snapshot."],
  ["Verdant Market outlier", "An overdue age is a lower bound while payment remains unpaid."],
  ["Action queue", "Human approval, actual delivery preview, and collections review fields."],
  ["Model performance", "Validation metrics and their synthetic-data limits."],
  ["Reply triage", "A preview, correction, and explicit approval before invoice state changes."],
];
const exampleKeys = ["portfolio", "verdant_outlier", "action_queue", "model_performance", "reply_triage"];

function format(currency: string, value: string | number) {
  return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function FallbackExample({ example, snapshotDate }: { example?: Snapshot["workflow_examples"][string]; snapshotDate: string }) {
  if (!example) return null;
  const invoice = example.invoice;
  return <article className="drift-card offline-example">
    <div className="offline-example-heading"><span className="tour-content-label">Packaged example</span><h3>{example.title}</h3></div>
    <p>{example.body}</p>
    {invoice && <>
      <div className="offline-invoice-summary">
        <span className="offline-invoice-id">{invoice.invoice_number}</span>
        <strong>{invoice.client_name}</strong>
        <span>{format(invoice.currency, invoice.outstanding)} outstanding</span>
        <span>Due {invoice.due_date}</span>
        <span>{invoice.overdue_days} days overdue</span>
        <span className="offline-record-status">{invoice.disputed ? "Disputed" : invoice.status}</span>
      </div>
      <details className="fallback-invoice-detail">
        <summary>Expand packaged invoice evidence</summary>
        <div className="fallback-evidence-grid">
          <span>Issue date<strong>{invoice.issue_date}</strong></span><span>Due date<strong>{invoice.due_date}</strong></span>
          <span>Terms<strong>{invoice.payment_term_type.replaceAll("_", " ")}</strong></span>
          <span>Total<strong>{format(invoice.currency, invoice.total)}</strong></span><span>Paid to date<strong>{format(invoice.currency, invoice.paid)}</strong></span>
          <span>Outstanding<strong>{format(invoice.currency, invoice.outstanding)}</strong></span>
          <span>Partial payments allowed<strong>{invoice.partial_payment_allowed ? "Yes" : "No"}</strong></span>
          <span>Partial receipt<strong>{invoice.partial_at ? `${format(invoice.currency, invoice.partial_amount)} on ${invoice.partial_at.slice(0, 10)}` : "None recorded"}</strong></span>
          <span>Reminder history<strong>{invoice.reminder_count} recorded</strong></span>
          <span>Full settlement<strong>{invoice.settled_at?.slice(0, 10) || "Unsettled at snapshot"}</strong></span>
        </div>
        {invoice.reminder_dates.length > 0 && <div className="fine-print">Reminder dates: {invoice.reminder_dates.join(", ")} · descriptive history, not evidence that a reminder caused payment.</div>}
        <div className="fine-print">Packaged read-only record from snapshot {snapshotDate}; live invoice timeline requires the API.</div>
      </details>
    </>}
    {example.facts.length > 0 && <ul className="reasons offline-example-facts">{example.facts.map(fact => <li key={fact}><span className="reason-mark">•</span>{fact}</li>)}</ul>}
  </article>;
}

export default function OfflineDemoClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const requestedStop = Number(searchParams.get("stop") || "1");
  const stop = Math.max(1, Math.min(stops.length, Number.isFinite(requestedStop) ? requestedStop : 1));
  useEffect(() => {
    fetch("/demo-fallback.json", { cache: "force-cache" })
      .then(async response => { if (!response.ok) throw new Error("The packaged snapshot is missing."); return response.json(); })
      .then(value => setSnapshot(value as Snapshot))
      .catch(cause => setError(cause instanceof Error ? cause.message : "The offline snapshot could not be loaded."));
  }, []);
  const selectStop = (value: number) => {
    const next = Math.max(1, Math.min(stops.length, value));
    sessionStorage.setItem("latepayer-tour-stop", String(next));
    router.replace(`/offline-demo?stop=${next}`, { scroll: false });
  };
  const current = stops[stop - 1];
  return <div className="offline-content">
    <header className="offline-intro">
      <div className="eyebrow">Demonstration data</div>
      <h1>Read-only fallback</h1>
      <p className="subheading">Review a packaged snapshot and workflow examples when the live API is unavailable.</p>
    </header>
    <aside className="demo-banner offline-readonly-notice" role="note" aria-label="Fallback limitations">
      <span className="offline-readonly-mark" aria-hidden="true">i</span>
      <div><strong>Read-only snapshot</strong><span>Packaged snapshot {snapshot?.snapshot_version || "late-payer-fallback-v2"} stays usable when the API is unavailable. The browser still needs to load the web app and its packaged files; airplane-mode availability is not guaranteed. This page cannot change shared demo data.</span></div>
    </aside>
    {error && <div className="error-box" role="alert">{error}</div>}
    {snapshot && <>
      <section className="panel offline-overview" aria-labelledby="offline-overview-title">
        <div className="offline-overview-header">
          <div><div className="eyebrow">Snapshot {snapshot.snapshot_version} <span aria-hidden="true">·</span> as of {snapshot.as_of}</div><h2 id="offline-overview-title">Portfolio snapshot</h2></div>
          <span className="offline-snapshot-badge">Packaged data</span>
        </div>
        <div className="offline-summary-metrics" role="group" aria-label="Invoice counts">
          <article><span>Open invoices</span><strong>{snapshot.open_count}</strong></article>
          <article><span>Past due</span><strong>{snapshot.overdue_count}</strong></article>
          <article><span>Disputed</span><strong>{snapshot.disputed_count}</strong></article>
        </div>
        <div className="offline-currency-section">
          <div className="panel-title">Open balance by currency</div>
          <p className="fine-print">Aging counts include all open balances. Totals remain separate by currency.</p>
          <div className="offline-currency-totals">{Object.entries(snapshot.currency_totals).map(([currency, amount]) => <article key={currency}><span>Open {currency}</span><strong>{format(currency, amount)}</strong></article>)}</div>
        </div>
        <div className="offline-aging-section">
          <div className="panel-title">Invoice aging</div>
          <div className="aging-grid">{Object.entries(snapshot.aging_buckets).map(([key, bucket]) => <article className="aging-bucket" key={key}><span>{key.replaceAll("_", " ")}</span><strong>{bucket.count}</strong><small>{Object.entries(bucket.amounts).map(([currency, amount]) => format(currency, amount)).join(" · ")}</small></article>)}</div>
        </div>
      </section>
      <section className="panel guided-stop offline-step-card" aria-labelledby="offline-stop-title">
        <div className="offline-step-heading"><div><div className="eyebrow">Fallback stop {stop} of 5</div><h2 id="offline-stop-title">{current[0]}</h2></div><span className="tour-stop-count" aria-hidden="true">{String(stop).padStart(2, "0")} <span>/ 05</span></span></div>
        <p className="offline-step-description">{current[1]}</p>
        <FallbackExample example={snapshot.workflow_examples?.[exampleKeys[stop - 1]]} snapshotDate={snapshot.as_of} />
        <nav className="tour-navigation" aria-label="Offline tour navigation"><button className="btn" disabled={stop === 1} onClick={() => selectStop(stop - 1)}>← Previous</button><div className="tour-progress" role="group" aria-label={`Stop ${stop} of 5`}>{stops.map((item, index) => <button key={item[0]} className={index + 1 === stop ? "active" : ""} onClick={() => selectStop(index + 1)} aria-label={`Offline stop ${index + 1}: ${item[0]}`} aria-current={index + 1 === stop ? "step" : undefined}>{index + 1}</button>)}</div><button className="btn" disabled={stop === stops.length} onClick={() => selectStop(stop + 1)}>Next →</button></nav>
      </section>
      <section className="panel offline-sample-panel" aria-labelledby="offline-sample-title"><div className="panel-title" id="offline-sample-title">Sample open invoices</div><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Invoice</th><th>Client</th><th>Balance</th><th>Due</th><th>Status</th></tr></thead><tbody>{snapshot.sample.map(row => <tr key={row.id}><td>{row.invoice_number}</td><td>{row.client_name}</td><td>{format(row.currency, row.outstanding)}</td><td>{row.due_date}</td><td>{row.disputed ? "Disputed · unforecastable" : row.status}</td></tr>)}</tbody></table></div><p className="fine-print">Generated from {snapshot.generated_from}. Forecasts and mutations require the live API.</p></section>
    </>}
    <div className="action-buttons offline-page-actions"><Link className="btn primary" href="/">Retry live portfolio</Link><Link className="btn" href="/tour">Open guided tour</Link></div>
  </div>;
}

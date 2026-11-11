"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

type Snapshot = {
  as_of: string; generated_from: string; invoice_count: number; open_count: number;
  overdue_count: number; disputed_count: number; currency_totals: Record<string, string>;
  aging_buckets: Record<string, { count: number; amounts: Record<string, string> }>;
  sample: { id: string; invoice_number: string; client_name: string; currency: string; outstanding: string; due_date: string; status: string; disputed: boolean }[];
};

const stops = [
  ["Portfolio", "Open balances, past-due exposure, and the reconciliation snapshot."],
  ["Verdant Market outlier", "An overdue age is a lower bound while payment remains unpaid."],
  ["Action queue", "Human approval, actual delivery preview, and collections review fields."],
  ["Model performance", "Validation metrics and their synthetic-data limits."],
  ["Reply triage", "A preview, correction, and explicit approval before invoice state changes."],
];

function format(currency: string, value: string | number) {
  return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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
  return <>
    <div className="demo-banner"><strong>Read-only fallback demo</strong><span>The API is unavailable. This snapshot is packaged with the web app and does not change shared demo data.</span></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    {snapshot && <>
      <section className="panel"><div className="eyebrow">Snapshot as of {snapshot.as_of}</div><h1>{snapshot.open_count} open invoices · {snapshot.overdue_count} past due · {snapshot.disputed_count} disputed</h1>
        <p className="subheading">Aging counts include all open balances. Totals remain separate by currency.</p>
        <div className="queue-metrics">{Object.entries(snapshot.currency_totals).map(([currency, amount]) => <span key={currency}>Open {currency} <strong>{format(currency, amount)}</strong></span>)}</div>
        <div className="aging-grid">{Object.entries(snapshot.aging_buckets).map(([key, bucket]) => <article className="aging-bucket" key={key}><span>{key.replaceAll("_", " ")}</span><strong>{bucket.count}</strong><small>{Object.entries(bucket.amounts).map(([currency, amount]) => format(currency, amount)).join(" · ")}</small></article>)}</div>
      </section>
      <section className="panel guided-stop"><div className="eyebrow">Offline stop {stop} of 5</div><h2>{current[0]}</h2><p>{current[1]}</p>
        <nav className="tour-navigation" aria-label="Offline tour navigation"><button className="btn" disabled={stop === 1} onClick={() => selectStop(stop - 1)}>← Previous</button><div className="tour-progress">{stops.map((item, index) => <button key={item[0]} className={index + 1 === stop ? "active" : ""} onClick={() => selectStop(index + 1)} aria-label={`Offline stop ${index + 1}`}>{index + 1}</button>)}</div><button className="btn" disabled={stop === stops.length} onClick={() => selectStop(stop + 1)}>Next →</button></nav>
      </section>
      <section className="panel"><div className="panel-title">Sample open invoices</div><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Invoice</th><th>Client</th><th>Balance</th><th>Due</th><th>Status</th></tr></thead><tbody>{snapshot.sample.map(row => <tr key={row.id}><td>{row.invoice_number}</td><td>{row.client_name}</td><td>{format(row.currency, row.outstanding)}</td><td>{row.due_date}</td><td>{row.disputed ? "Disputed · unforecastable" : row.status}</td></tr>)}</tbody></table></div><p className="fine-print">Generated from {snapshot.generated_from}. Forecasts and mutations require the live API.</p></section>
    </>}
    <div className="action-buttons"><Link className="btn primary" href="/">Retry live portfolio</Link><Link className="btn" href="/tour">Open guided tour</Link></div>
  </>;
}

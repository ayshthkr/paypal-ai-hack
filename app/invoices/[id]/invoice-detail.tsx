"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import SurvivalCurve from "./survival-curve";

type Quantile = { days: number; date: string } | null;
type Forecast = { curve: number[]; quantiles: Record<string, Quantile>; probabilities: Record<string, number>; probability_by_due: number; reasons: string[]; model: string; model_version: string; already_overdue: boolean; forecast_horizon_days: number; history_count: number; scoring_date: string; scored_at: string } | null;
type Recommendation = { id: string; action: string; tone: string; reason: string; draft_text: string; draft_source: string; drafted_at: string | null; edited_text: string; invoice_version: number; state: string; approved_text: string | null } | null;
type IssueForecast = { source: string; model_version: string | null; p10_date: string | null; p50_date: string | null; p90_date: string | null; probabilities: Record<string, number> | null; absolute_error_days?: number | null; within_p10_p90?: boolean | null } | null;
type Outcome = { paid_at: string | null; days_from_due: number | null; timing: "late" | "early" | "on_time" | "date_unavailable"; issue_forecast: IssueForecast } | null;
type TimelineEvent = { kind: string; at: string; detail: string | null };
type Neighbor = { id: string; invoice_number: string; query: string };
type InvoiceData = {
  id: string; paypal_id: string | null; invoice_number: string; client_id: string; client_name: string; client_email: string | null;
  issue_date: string; due_date: string; days_to_due: number; total: string; paid: string; outstanding: string; currency: string;
  status: string; source: string; material_version: number; payment_url: string | null; forecast: Forecast; why: string; disputed: boolean; promised_payment_date: string | null;
  viewed_at: string | null; viewed_by_recipient: boolean; viewed_observed_at: string | null; partial_payment_allowed: boolean; payment_term_type: string | null;
  drift: { signal: string; available: boolean; settled_count: number; message: string };
  decision: { action: string; tone: string; reason: string }; recommendation: Recommendation;
  outcome: Outcome; timeline: TimelineEvent[];
  attempts?: { state: string; provider_reference: string | null; started_at: string; finished_at: string | null; error: string | null }[];
  reminder_delivery_note?: string;
};
type ReplyTriage = { classification: string; classifier: string; promised_payment_date: string | null; reminders_paused: boolean; note: string };
type InvoicePage = { items: { id: string; invoice_number: string }[]; page: number; page_count: number };

class RequestError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new RequestError(value.detail || `Request failed (${response.status})`, response.status);
  return value as T;
}

function shortDate(value: string | null | undefined) {
  return value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Beyond 180 days";
}

export default function InvoiceDetail({ id, portfolioQuery }: { id: string; portfolioQuery: string }) {
  const [data, setData] = useState<InvoiceData | null>(null);
  const [draft, setDraft] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [replyTriage, setReplyTriage] = useState<ReplyTriage | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [missingSearch, setMissingSearch] = useState("");
  const [neighbors, setNeighbors] = useState<{ previous: Neighbor | null; next: Neighbor | null }>({ previous: null, next: null });
  const [neighborsLoading, setNeighborsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const refreshInFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const pending = Promise.all([
      requestJson<InvoiceData>(`/api/invoices/${encodeURIComponent(id)}`),
      requestJson<{ authenticated: boolean }>("/api/auth/me"),
    ]).then(([invoice, auth]) => {
      setData(invoice); setAuthenticated(auth.authenticated); setError(""); setNotFound(false);
      setDraft(previous => previous && invoice.recommendation?.state === "proposed" ? previous : invoice.recommendation?.edited_text || "");
    }).catch(cause => {
      setError(cause instanceof Error ? cause.message : "Could not load invoice.");
      setNotFound(cause instanceof RequestError && cause.status === 404);
    }).finally(() => {
      setLoading(false);
      refreshInFlight.current = null;
    });
    refreshInFlight.current = pending;
    return pending;
  }, [id]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams(portfolioQuery || "view=open&page=1&page_size=25&sort=action&direction=asc");
    const currentPage = Math.max(1, Number(params.get("page") || "1"));
    const load = async () => {
      setNeighborsLoading(true);
      try {
        const current = await requestJson<InvoicePage>(`/api/invoices?${params.toString()}`);
        const index = current.items.findIndex(item => item.id === id);
        if (index < 0) { if (!cancelled) setNeighbors({ previous: null, next: null }); return; }
        let previous = index > 0 ? { ...current.items[index - 1], query: params.toString() } : null;
        let next = index < current.items.length - 1 ? { ...current.items[index + 1], query: params.toString() } : null;
        const adjacent: Promise<InvoicePage | null>[] = [];
        if (!previous && currentPage > 1) {
          const priorParams = new URLSearchParams(params); priorParams.set("page", String(currentPage - 1));
          adjacent.push(requestJson<InvoicePage>(`/api/invoices?${priorParams.toString()}`));
        } else adjacent.push(Promise.resolve(null));
        if (!next && currentPage < current.page_count) {
          const nextParams = new URLSearchParams(params); nextParams.set("page", String(currentPage + 1));
          adjacent.push(requestJson<InvoicePage>(`/api/invoices?${nextParams.toString()}`));
        } else adjacent.push(Promise.resolve(null));
        const [priorPage, nextPage] = await Promise.all(adjacent);
        if (priorPage && priorPage.items.length) {
          const priorParams = new URLSearchParams(params); priorParams.set("page", String(currentPage - 1));
          const item = priorPage.items[priorPage.items.length - 1];
          previous = { ...item, query: priorParams.toString() };
        }
        if (nextPage && nextPage.items.length) {
          const nextParams = new URLSearchParams(params); nextParams.set("page", String(currentPage + 1));
          next = { ...nextPage.items[0], query: nextParams.toString() };
        }
        if (!cancelled) setNeighbors({ previous, next });
      } catch {
        if (!cancelled) setNeighbors({ previous: null, next: null });
      } finally {
        if (!cancelled) setNeighborsLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [id, portfolioQuery]);

  const action = async (url: string, method = "POST", body?: object) => {
    if (!data?.recommendation) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await requestJson<{ state?: string; simulated?: boolean; note?: string }>(url, { method, body: body ? JSON.stringify(body) : undefined });
      setNotice(result.note || (result.simulated ? "Demo action simulated; nothing was sent through PayPal." : `Action ${result.state || "completed"}.`));
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Action failed."); await refresh(); }
    finally { setBusy(false); }
  };

  const recordPayment = async (amount?: string) => {
    if (!data) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await requestJson<{ note: string; status: string }>(`/api/demo/invoices/${encodeURIComponent(id)}/payment`, { method: "POST", body: JSON.stringify(amount ? { amount } : {}) });
      setNotice(`${result.note} Invoice status is now ${result.status}.`);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Payment simulation failed."); }
    finally { setBusy(false); }
  };

  const generateDraft = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await requestJson<{ recommendation: NonNullable<Recommendation>; note: string }>(`/api/invoices/${encodeURIComponent(id)}/draft`, { method: "POST" });
      setData(current => current ? { ...current, recommendation: result.recommendation } : current);
      setDraft(result.recommendation.edited_text);
      setNotice(result.note);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create a reminder draft."); }
    finally { setBusy(false); }
  };

  const triageReply = async () => {
    if (!replyText.trim()) return;
    setBusy(true); setError(""); setNotice(""); setReplyTriage(null);
    try {
      const result = await requestJson<ReplyTriage>(`/api/invoices/${encodeURIComponent(id)}/reply`, { method: "POST", body: JSON.stringify({ reply: replyText }) });
      setReplyTriage(result); setReplyText(""); setNotice(result.note);
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not classify the client reply."); }
    finally { setBusy(false); }
  };

  if (loading && !data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link></header><main className="main-wrap"><div className="empty-box">Loading invoice…</div></main></div>;
  if (error && !data && notFound) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link></header><main className="main-wrap"><div className="panel"><div className="eyebrow">Invoice detail</div><h1>Invoice not found</h1><p className="subheading">This invoice may have been removed or the link may be out of date.</p><div className="toolbar-tools"><Link className="btn" href="/">Back to portfolio</Link><form action="/" method="get" className="toolbar-tools"><input className="search" name="q" aria-label="Search invoices" placeholder="Search invoice or client" value={missingSearch} onChange={event => setMissingSearch(event.target.value)}/><button className="btn primary" disabled={!missingSearch.trim()}>Search</button></form></div></div></main></div>;
  if (error && !data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link></header><main className="main-wrap"><div className="error-box">{error} {error.includes("login") && <Link className="table-link" href="/login">Operator login</Link>}</div></main></div>;
  if (!data) return null;
  const rec = data.recommendation;
  const isDemo = data.source === "demo";
  const p50 = data.forecast?.quantiles.p50;
  const actionUrl = isDemo ? `/api/demo/recommendations/${rec?.id}/send` : `/api/recommendations/${rec?.id}/send`;
  const canSend = Boolean(rec && ["proposed", "failed"].includes(rec.state) && ["friendly", "firm"].includes(data.decision.action) && rec.action === data.decision.action && (isDemo || authenticated));
  const isPartialAction = Boolean(rec && rec.action === "offer_partial_payment");
  const canApplyPartial = Boolean(isPartialAction && rec && rec.action === data.decision.action && ["proposed", "failed"].includes(rec.state) && (isDemo || authenticated));

  return <div className="app-shell">
    <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← All invoices</Link>{authenticated ? <span>Operator session</span> : <Link className="btn" href="/login">Operator login</Link>}</div></header>
    <main className="main-wrap">
      {isDemo && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Forecast and approval flow use generated records. Simulated actions do not contact PayPal.</span></div>}
      {error && <div className="error-box" role="alert" style={{ marginBottom: 14 }}>{error}</div>}
      <div className="heading-row"><div><div className="eyebrow">Invoice detail · {data.source === "demo" ? "Synthetic" : "PayPal Sandbox"}</div><h1>{data.invoice_number}</h1><p className="subheading"><Link className="table-link" href={`/clients/${encodeURIComponent(data.client_id)}`}>{data.client_name}</Link>{data.client_email ? ` · ${data.client_email}` : ""}</p></div><div className="top-actions"><span className={`status-pill ${data.disputed ? "disputed" : data.status.toLowerCase()}`}>{data.disputed ? "DISPUTED" : data.status.replaceAll("_", " ")}</span>{!isDemo && authenticated && <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { await requestJson("/api/sync", { method: "POST" }); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Sync failed."); } finally { setBusy(false); } }}>↻ Sync PayPal</button>}</div></div>
      <nav className="invoice-navigation" aria-label="Invoice navigation"><div>{neighbors.previous ? <Link className="btn" href={`/invoices/${encodeURIComponent(neighbors.previous.id)}${neighbors.previous.query ? `?${neighbors.previous.query}` : ""}`}>← {neighbors.previous.invoice_number}</Link> : <span className="fine-print">No previous invoice</span>}</div><span className="fine-print">{neighborsLoading ? "Loading current view…" : "Current portfolio view"}</span><div>{neighbors.next ? <Link className="btn" href={`/invoices/${encodeURIComponent(neighbors.next.id)}${neighbors.next.query ? `?${neighbors.next.query}` : ""}`}>{neighbors.next.invoice_number} →</Link> : <span className="fine-print">No next invoice</span>}</div></nav>
      <div className="detail-layout">
        <section>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">Payment timing forecast</div><div className="panel-subtitle">Probability the remaining balance is still unpaid over time · conditional on unpaid today</div></div>{data.forecast && <span className="status-pill">{data.forecast.model.replaceAll("_", " ")}</span>}</div>
            {!data.forecast ? <div className="empty-box">No payment forecast for a closed or fully paid invoice.</div> : <>
              <div className="forecast-number">{p50 ? shortDate(p50.date) : "Beyond 180 days"}</div>
              <div className="panel-subtitle">Median expected full settlement{data.forecast.already_overdue ? " · invoice is already overdue" : ""}</div>
              <div className="drift-card" style={{ marginTop: 12 }}><strong>Why this forecast</strong><p>{data.why}</p></div>
              <SurvivalCurve curve={data.forecast.curve} quantiles={data.forecast.quantiles}/>
              <div className="legend"><span className="legend-item"><i className="legend-line"/>Unpaid probability</span><span className="legend-item"><i className="legend-band"/>p10–p90 payment window</span></div>
              <div className="quantiles"><div className="quantile"><div className="quantile-label">Paid by due date</div><div className="quantile-value">{Math.round(data.forecast.probability_by_due * 100)}%</div></div><div className="quantile"><div className="quantile-label">Paid in 14 days</div><div className="quantile-value">{Math.round(data.forecast.probabilities["14d"] * 100)}%</div></div><div className="quantile"><div className="quantile-label">Paid in 30 days</div><div className="quantile-value">{Math.round(data.forecast.probabilities["30d"] * 100)}%</div></div></div>
              <div className="quantiles">{(["p10", "p50", "p90"] as const).map(key => <div className="quantile" key={key}><div className="quantile-label">{key.toUpperCase()} payment date</div><div className="quantile-value">{shortDate(data.forecast?.quantiles[key]?.date)}</div></div>)}</div>
              <ul className="reasons">{data.forecast.reasons.map((reason, index) => <li key={index}><span className="reason-mark">✓</span>{reason}</li>)}</ul>
              <div className="fine-print" style={{ marginTop: 15 }}>Model: {data.forecast.model_version} · Scored {new Date(data.forecast.scored_at).toLocaleString()} · 180-day horizon</div>
            </>}
          </div>
          <div className="panel"><div className="panel-head"><div><div className="panel-title">Client timing signal</div><div className="panel-subtitle">Current settled history; forecast history is point-in-time</div></div><span className="status-pill">{data.drift.signal.replaceAll("_", " ")}</span></div><div className="drift-card"><strong>{data.drift.available ? `${data.drift.settled_count} settled invoices reviewed` : "Insufficient history"}</strong><p>{data.drift.message}</p><div className="fine-print">The forecast used {data.forecast?.history_count ?? 0} prior settled invoices known on its scoring date; the timing signal uses currently available settled history.</div></div></div>
          {data.outcome && <div className="panel"><div className="panel-head"><div><div className="panel-title">Paid outcome</div><div className="panel-subtitle">Observed result against the issue-time model backtest</div></div></div><div className="invoice-facts"><div className="fact"><div className="fact-label">Paid date</div><div className="fact-value">{data.outcome.paid_at ? shortDate(data.outcome.paid_at) : "Paid date unavailable"}</div></div><div className="fact"><div className="fact-label">Against due date</div><div className="fact-value">{data.outcome.days_from_due == null ? "Unavailable" : data.outcome.timing === "early" ? `${Math.abs(data.outcome.days_from_due)} days early` : data.outcome.timing === "late" ? `${data.outcome.days_from_due} days late` : "On time"}</div></div></div>{data.outcome.issue_forecast ? <div className="drift-card" style={{ marginTop: 12 }}><strong>Issue-time p50: {shortDate(data.outcome.issue_forecast.p50_date)}</strong><p>Actual payment: {data.outcome.paid_at ? shortDate(data.outcome.paid_at) : "Date unavailable"}{data.outcome.issue_forecast.absolute_error_days != null ? ` · ${data.outcome.issue_forecast.absolute_error_days} day absolute error` : ""}</p><div className="fine-print">p10 {shortDate(data.outcome.issue_forecast.p10_date)} · p90 {shortDate(data.outcome.issue_forecast.p90_date)} · {data.outcome.issue_forecast.source.replaceAll("_", " ")}{data.outcome.issue_forecast.model_version ? ` · ${data.outcome.issue_forecast.model_version}` : ""}</div></div> : <div className="empty-inline">No issue-time prediction was stored for this invoice.</div>}</div>}
          <div className="panel"><div className="panel-head"><div><div className="panel-title">Invoice timeline</div><div className="panel-subtitle">Issue, delivery, views, reminders, payments, and provider events</div></div></div><div className="history-list">{data.timeline.length ? data.timeline.map((event, index) => <div className="history-row" key={`${event.at}-${event.kind}-${index}`}><span className="history-dot"/><div><div className="history-title">{event.kind}</div><div className="history-meta">{new Date(event.at).toLocaleString()}</div>{event.detail && <div className="history-meta">{event.detail}</div>}</div></div>) : <div className="empty-box">No dated invoice events are available.</div>}</div></div>
        </section>
        <aside>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">Invoice status</div><div className="panel-subtitle">Balance and due-date details</div></div></div>
            <div className="invoice-facts">
              <div className="fact"><div className="fact-label">Outstanding</div><div className="fact-value">{data.currency} {Number(data.outstanding).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div></div>
              <div className="fact"><div className="fact-label">Paid</div><div className="fact-value">{data.currency} {Number(data.paid).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div></div>
              <div className="fact"><div className="fact-label">Due date</div><div className="fact-value">{shortDate(data.due_date)}</div></div>
              <div className="fact"><div className="fact-label">Currency</div><div className="fact-value">{data.currency}</div></div>
              <div className="fact"><div className="fact-label">Viewed</div><div className="fact-value">{data.viewed_by_recipient ? data.viewed_at ? shortDate(data.viewed_at) : "Yes · exact time unavailable" : "Not yet"}</div></div>
              <div className="fact"><div className="fact-label">Partial payments</div><div className="fact-value">{data.partial_payment_allowed ? "Allowed" : "Not enabled"}</div></div>
              <div className="fact"><div className="fact-label">Payment terms</div><div className="fact-value">{(data.payment_term_type || "Not specified").replaceAll("_", " ")}</div></div>
              <div className="fact"><div className="fact-label">Decision</div><div className="fact-value">{data.decision.action}</div></div>
              <div className="fact"><div className="fact-label">Policy reason</div><div className="fact-value" style={{ fontSize: 11, lineHeight: 1.35 }}>{data.decision.reason}</div></div>
            </div>
            {!isDemo && data.payment_url && <a className="btn" href={data.payment_url} target="_blank" rel="noreferrer">Open PayPal invoice ↗</a>}
            {isDemo && Number(data.outstanding) > 0 && <div className="action-buttons"><button className="btn" disabled={busy} onClick={() => void recordPayment((Number(data.outstanding) / 2).toFixed(2))}>Simulate partial payment</button><button className="btn" disabled={busy} onClick={() => void recordPayment()}>Simulate full payment</button></div>}
          </div>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">{isPartialAction ? "Partial-payment review" : "Reminder review"}</div><div className="panel-subtitle">{rec ? rec.reason : data.decision.reason}</div></div>{rec && <span className="recommendation-tag">{rec.state}</span>}</div>
            {rec && isPartialAction ? <>
              <div className="drift-card"><strong>PayPal payment setting</strong><p>{rec.edited_text}</p><div className="fine-print">Approval enables partial payments on the existing invoice. The provider update may notify the recipient.</div></div>
              <div className="action-buttons">
                {canApplyPartial && <button className="btn primary" disabled={busy} onClick={() => {
                  const url = isDemo ? `/api/demo/recommendations/${rec.id}/send` : `/api/recommendations/${rec.id}/approve-partial-payment`;
                  if (isDemo || window.confirm("Enable partial payments on this PayPal Sandbox invoice? PayPal may notify the recipient of the invoice update.")) void action(url, "POST", { text: rec.edited_text });
                }}>{busy ? "Applying…" : isDemo ? "Approve & simulate" : "Approve & enable partial payment"}</button>}
                {rec.state === "proposed" && <button className="btn" disabled={busy} onClick={() => void action(`/api/recommendations/${rec.id}/skip`)}>Skip</button>}
                {rec.state === "applied" && <span className="fine-print">Partial-payment settings are enabled{isDemo ? " in the synthetic demo" : " in PayPal Sandbox"}.</span>}
                {rec.state === "unknown" && <span className="fine-print">Provider update status is uncertain. Sync PayPal before retrying.</span>}
                {rec.state === "failed" && <span className="fine-print">The provider rejected the update. Review and retry is available.</span>}
              </div>
            </> : rec ? <>
              <div className="fine-print" style={{ marginBottom: 8 }}>Draft source: {rec.draft_source === "gemini" ? "Gemini, grounded in invoice facts" : "grounded template"}{rec.drafted_at ? ` · ${new Date(rec.drafted_at).toLocaleString()}` : ""}</div>
              <textarea aria-label="Editable reminder preview" value={draft} onChange={event => setDraft(event.target.value)} disabled={rec.state === "sending" || rec.state === "sent" || rec.state === "unknown" || rec.state === "skipped"}/>
              <div className="delivery-note">{data.reminder_delivery_note || (isDemo ? "Demo mode: simulated send records the approval only. No PayPal email is sent." : "The exact approved copy is recorded for audit. PayPal's native reminder email controls the message body.")}</div>
              <div className="action-buttons">
                {rec.state === "proposed" && <button className="btn" disabled={busy} onClick={() => void generateDraft()}>{busy ? "Drafting…" : "Generate AI draft"}</button>}
                {canSend && <button className="btn primary" disabled={busy || !draft.trim()} onClick={() => { if (isDemo || window.confirm("Approve this exact text and send the PayPal invoice reminder?")) void action(actionUrl, "POST", { text: draft }); }}>{busy ? "Processing…" : isDemo ? "Approve & simulate" : "Approve & send"}</button>}
                {rec.state === "proposed" && <button className="btn" disabled={busy} onClick={() => void action(`/api/recommendations/${rec.id}/skip`)}>Skip</button>}
                {rec.state === "sent" && <span className="fine-print">{isDemo ? "Recorded as a simulated demo action." : "PayPal accepted the reminder request."}</span>}
                {rec.state === "unknown" && <span className="fine-print">Delivery is uncertain. Sync PayPal and reconcile before trying again.</span>}
                {rec.state === "failed" && <span className="fine-print">The provider rejected the request. Review and retry is available.</span>}
              </div>
            </> : <div className="empty-box">No reminder proposed. {data.decision.action === "personal_call" ? "Make a personal call and record the outcome." : data.decision.action === "offer_partial_payment" ? "Review a partial-payment option for this client." : data.decision.action === "review_for_writeoff_or_collections" ? "Review this stale balance for write-off or collections." : data.decision.action === "wait" ? "The current policy says to wait." : "Open this invoice again to refresh its recommendation."}</div>}
            {notice && <div className="action-message" role="status">{notice}</div>}
          </div>
          <div className="panel"><div className="panel-head"><div><div className="panel-title">Client reply triage</div><div className="panel-subtitle">Paste a reply to record a promise, dispute, question, or other note.</div></div></div>
            <textarea aria-label="Paste client reply" placeholder="Example: We will pay Friday." value={replyText} onChange={event => setReplyText(event.target.value)} disabled={busy}/>
            <div className="action-buttons"><button className="btn" disabled={busy || !replyText.trim()} onClick={() => void triageReply()}>{busy ? "Checking…" : "Classify reply"}</button>
              {data.disputed && <span className="status-pill">Reminders paused · disputed</span>}
              {data.promised_payment_date && !data.disputed && <span className="status-pill">Promise recorded · {shortDate(data.promised_payment_date)}</span>}
            </div>
            {replyTriage && <div className="action-message" role="status">{replyTriage.classification.replaceAll("_", " ")} · {replyTriage.classifier} · {replyTriage.note}</div>}
          </div>
          {rec && <div className="panel"><div className="panel-head"><div><div className="panel-title">Action history</div><div className="panel-subtitle">Dispatch attempts for this recommendation</div></div></div><div className="history-list">{(data.attempts || []).length ? data.attempts?.map((attempt, index) => <div className="history-row" key={`${attempt.started_at}-${index}`}><span className="history-dot"/><div><div className="history-title">{attempt.state}{attempt.provider_reference ? ` · ${attempt.provider_reference}` : ""}</div><div className="history-meta">{new Date(attempt.started_at).toLocaleString()}</div>{attempt.error && <div className="history-meta">{attempt.error}</div>}</div></div>) : <div className="fine-print">No send attempts recorded.</div>}</div></div>}
        </aside>
      </div>
    </main>
  </div>;
}

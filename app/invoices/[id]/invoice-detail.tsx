"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import SurvivalCurve from "./survival-curve";

type Quantile = { days: number; date: string } | null;
type Forecast = { curve: number[]; quantiles: Record<string, Quantile>; probabilities: Record<string, number>; reasons: string[]; model: string; model_version: string; already_overdue: boolean; forecast_horizon_days: number } | null;
type Recommendation = { id: string; action: string; tone: string; reason: string; draft_text: string; draft_source: string; drafted_at: string | null; edited_text: string; invoice_version: number; state: string; approved_text: string | null } | null;
type InvoiceData = {
  id: string; paypal_id: string | null; invoice_number: string; client_id: string; client_name: string; client_email: string | null;
  issue_date: string; due_date: string; days_to_due: number; total: string; paid: string; outstanding: string; currency: string;
  status: string; source: string; material_version: number; payment_url: string | null; forecast: Forecast; why: string; disputed: boolean; promised_payment_date: string | null;
  drift: { signal: string; available: boolean; settled_count: number; message: string };
  decision: { action: string; tone: string; reason: string }; recommendation: Recommendation;
  attempts?: { state: string; provider_reference: string | null; started_at: string; finished_at: string | null; error: string | null }[];
  reminder_delivery_note?: string;
};
type ReplyTriage = { classification: string; classifier: string; promised_payment_date: string | null; reminders_paused: boolean; note: string };

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
  return value as T;
}

function shortDate(value: string | null | undefined) {
  return value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Beyond 180 days";
}

export default function InvoiceDetail({ id }: { id: string }) {
  const [data, setData] = useState<InvoiceData | null>(null);
  const [draft, setDraft] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [replyTriage, setReplyTriage] = useState<ReplyTriage | null>(null);
  const [loading, setLoading] = useState(true);
  const refreshInFlight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const pending = Promise.all([
      requestJson<InvoiceData>(`/api/invoices/${encodeURIComponent(id)}`),
      requestJson<{ authenticated: boolean }>("/api/auth/me"),
    ]).then(([invoice, auth]) => {
      setData(invoice); setAuthenticated(auth.authenticated); setError("");
      setDraft(previous => previous && invoice.recommendation?.state === "proposed" ? previous : invoice.recommendation?.edited_text || "");
    }).catch(cause => {
      setError(cause instanceof Error ? cause.message : "Could not load invoice.");
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
  if (error && !data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link></header><main className="main-wrap"><div className="error-box">{error} {error.includes("login") && <Link className="table-link" href="/login">Operator login</Link>}</div></main></div>;
  if (!data) return null;
  const rec = data.recommendation;
  const isDemo = data.source === "demo";
  const p50 = data.forecast?.quantiles.p50;
  const actionUrl = isDemo ? `/api/demo/recommendations/${rec?.id}/send` : `/api/recommendations/${rec?.id}/send`;
  const canSend = Boolean(rec && ["proposed", "failed"].includes(rec.state) && ["friendly", "firm"].includes(data.decision.action) && rec.action === data.decision.action && (isDemo || authenticated));

  return <div className="app-shell">
    <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← All invoices</Link>{authenticated ? <span>Operator session</span> : <Link className="btn" href="/login">Operator login</Link>}</div></header>
    <main className="main-wrap">
      {isDemo && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Forecast and approval flow use generated records. Simulated actions do not contact PayPal.</span></div>}
      {error && <div className="error-box" role="alert" style={{ marginBottom: 14 }}>{error}</div>}
      <div className="heading-row"><div><div className="eyebrow">Invoice detail · {data.source === "demo" ? "Synthetic" : "PayPal Sandbox"}</div><h1>{data.invoice_number}</h1><p className="subheading">{data.client_name}{data.client_email ? ` · ${data.client_email}` : ""}</p></div><div className="top-actions"><span className={`status-pill ${data.status.toLowerCase()}`}>{data.status.replaceAll("_", " ")}</span>{!isDemo && authenticated && <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { await requestJson("/api/sync", { method: "POST" }); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Sync failed."); } finally { setBusy(false); } }}>↻ Sync PayPal</button>}</div></div>
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
              <div className="quantiles">{(["p10", "p50", "p90"] as const).map(key => <div className="quantile" key={key}><div className="quantile-label">{key.toUpperCase()} payment date</div><div className="quantile-value">{shortDate(data.forecast?.quantiles[key]?.date)}</div></div>)}</div>
              <ul className="reasons">{data.forecast.reasons.map((reason, index) => <li key={index}><span className="reason-mark">✓</span>{reason}</li>)}</ul>
              <div className="fine-print" style={{ marginTop: 15 }}>Active model: {data.forecast.model_version} · Synthetic training history · 180-day horizon</div>
            </>}
          </div>
          <div className="panel"><div className="panel-head"><div><div className="panel-title">Client timing signal</div><div className="panel-subtitle">Trend from fully settled invoices</div></div><span className="status-pill">{data.drift.signal.replaceAll("_", " ")}</span></div><div className="drift-card"><strong>{data.drift.available ? `${data.drift.settled_count} settled invoices reviewed` : "Insufficient history"}</strong><p>{data.drift.message}</p></div></div>
        </section>
        <aside>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">Invoice status</div><div className="panel-subtitle">Balance and due-date details</div></div></div>
            <div className="invoice-facts">
              <div className="fact"><div className="fact-label">Outstanding</div><div className="fact-value">{data.currency} {Number(data.outstanding).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div></div>
              <div className="fact"><div className="fact-label">Paid</div><div className="fact-value">{data.currency} {Number(data.paid).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div></div>
              <div className="fact"><div className="fact-label">Due date</div><div className="fact-value">{shortDate(data.due_date)}</div></div>
              <div className="fact"><div className="fact-label">Currency</div><div className="fact-value">{data.currency}</div></div>
              <div className="fact"><div className="fact-label">Decision</div><div className="fact-value">{data.decision.action}</div></div>
              <div className="fact"><div className="fact-label">Policy reason</div><div className="fact-value" style={{ fontSize: 11, lineHeight: 1.35 }}>{data.decision.reason}</div></div>
            </div>
            {!isDemo && data.payment_url && <a className="btn" href={data.payment_url} target="_blank" rel="noreferrer">Open PayPal invoice ↗</a>}
            {isDemo && Number(data.outstanding) > 0 && <div className="action-buttons"><button className="btn" disabled={busy} onClick={() => void recordPayment((Number(data.outstanding) / 2).toFixed(2))}>Simulate partial payment</button><button className="btn" disabled={busy} onClick={() => void recordPayment()}>Simulate full payment</button></div>}
          </div>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">Reminder review</div><div className="panel-subtitle">{rec ? rec.reason : data.decision.reason}</div></div>{rec && <span className="recommendation-tag">{rec.state}</span>}</div>
            {rec ? <>
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
            </> : <div className="empty-box">No reminder proposed. {data.decision.action === "personal_call" ? "Make a personal call and record the outcome." : data.decision.action === "offer_partial_payment" ? "Review a partial-payment option for this client." : data.decision.action === "wait" ? "The current policy says to wait." : "Open this invoice again to refresh its recommendation."}</div>}
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

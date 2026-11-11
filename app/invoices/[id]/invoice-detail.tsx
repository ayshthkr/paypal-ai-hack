"use client";
import { clearReads, requestJson, RequestError } from "../../lib/api";
import { decisionLabel, signalLabel, statusLabel, termLabel } from "../../lib/labels";
import { useAuth } from "../../providers";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import SurvivalCurve from "./survival-curve";

type Quantile = { days: number; date: string } | null;
type Forecast = { interval_method?: string; coverage_interval?: { lower_date: string | null; upper_date: string | null; target_coverage: number; method: string; caveat?: string }; promise_adjustment?: { applied: boolean; status: string; date?: string; weight: number; method?: string; caveat?: string }; display_label?: string; collection_probability?: number; collection_method?: string | null; limited_history?: boolean; curve: number[]; quantiles: Record<string, Quantile>; probabilities: Record<string, number>; probability_by_due: number; reasons: string[]; model: string; model_version: string; already_overdue: boolean; forecast_horizon_days: number; history_count: number; scoring_date: string; scored_at: string } | null;
type Recommendation = { id: string; action: string; tone: string; reason: string; draft_text: string; draft_source: string; draft_model: string | null; drafted_at: string | null; edited_text: string; invoice_version: number; state: string; approved_text: string | null } | null;
type IssueForecast = { source: string; model_version: string | null; model_label?: string | null; p10_date: string | null; p50_date: string | null; p90_date: string | null; probabilities: Record<string, number> | null; absolute_error_days?: number | null; within_p10_p90?: boolean | null } | null;
type Outcome = { paid_at: string | null; days_from_due: number | null; timing: "late" | "early" | "on_time" | "date_unavailable"; issue_forecast: IssueForecast } | null;
type TimelineEvent = { kind: string; at: string; detail: string | null };
type ReminderOutcome = { sent_at: string; tone: string; status: "paid" | "partial" | "silent" | "checking"; amount_paid: string; currency: string; observed_through: string; window_ends: string };
type Neighbor = { id: string; invoice_number: string; query: string };
type InvoiceData = {
  id: string; paypal_id: string | null; invoice_number: string; client_id: string; client_name: string; client_email: string | null;
  issue_date: string; due_date: string; days_to_due: number; total: string; paid: string; outstanding: string; currency: string;
  simulation_disabled?: boolean; status: string; source: string; material_version: number; payment_url: string | null; forecast: Forecast; why: string; disputed: boolean; promised_payment_date: string | null;
  viewed_at: string | null; viewed_by_recipient: boolean; viewed_observed_at: string | null; partial_payment_allowed: boolean; payment_term_type: string | null;
  drift: { signal: string; available: boolean; settled_count: number; message: string };
  decision: { action: string; tone: string; reason: string }; recommendation: Recommendation;
  outcome: Outcome; timeline: TimelineEvent[];
  reminder_outcomes?: ReminderOutcome[];
  attempts?: { state: string; provider_reference: string | null; started_at: string; finished_at: string | null; error: string | null }[];
  reply_triages?: { id: string; reply_text: string; classification: string; promised_payment_date: string | null; classifier: string; state: string; created_at: string }[];
  reminder_delivery_note?: string;
  outlier_hint?: string | null;
};
type ReplyTriage = { id: string; reply_text: string; classification: string; classifier: string; promised_payment_date: string | null; state: string; reminders_paused: boolean; note: string };
type ReplyTriageStatus = { id: string; state: string; classification: string; promised_payment_date: string | null; disputed: boolean; invoice_promised_payment_date: string | null; reviewed_at: string | null };
type InvoicePage = { items: { id: string; invoice_number: string }[]; page: number; page_count: number };


function shortDate(value: string | null | undefined) {
  return value ? new Date(`${value.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" }) : "Beyond 180 days";
}

function formatIST(value: string) {
  return new Date(value).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function InvoiceDetail({ id, portfolioQuery }: { id: string; portfolioQuery: string }) {
  const router = useRouter();
  const clockDate = new URLSearchParams(portfolioQuery).get("as_of");
  const clockQuery = clockDate ? `?as_of=${encodeURIComponent(clockDate)}` : "";
  const [data, setData] = useState<InvoiceData | null>(null);
  const [draft, setDraft] = useState("");
  const { authenticated } = useAuth();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [replyTriage, setReplyTriage] = useState<ReplyTriage | null>(null);
  const pendingTriage = data?.reply_triages?.find(row => row.state === "pending");
  const [replyReviewUnresolved, setReplyReviewUnresolved] = useState(false);
  const visibleReplyTriage = (replyTriage?.state === "pending" ? replyTriage : null) || (pendingTriage && data ? {
    ...pendingTriage,
    reminders_paused: Boolean(data.disputed || data.promised_payment_date),
    note: "Preview only. Review or correct the extracted type and date, then approve it to update invoice state.",
  } : null);
  const [notFound, setNotFound] = useState(false);
  const [missingSearch, setMissingSearch] = useState("");
  const [neighbors, setNeighbors] = useState<{ previous: Neighbor | null; next: Neighbor | null }>({ previous: null, next: null });
  const [neighborsLoading, setNeighborsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const refreshInFlight = useRef<{ key: string; promise: Promise<InvoiceData | null> } | null>(null);
  const refreshVersion = useRef(0);
  const draftRecommendation = useRef<string | null>(null);
  const [approvalOutcomeUnresolved, setApprovalOutcomeUnresolved] = useState(false);

  const refresh = useCallback(async (force = false): Promise<InvoiceData | null> => {
    const requestKey = `${id}${clockQuery}`;
    if (!force && refreshInFlight.current?.key === requestKey) return refreshInFlight.current.promise;
    const requestVersion = ++refreshVersion.current;
    const pending = Promise.all([
      requestJson<InvoiceData>(`/api/invoices/${encodeURIComponent(id)}${clockQuery}`),

    ]).then(([invoice]) => {
      if (refreshVersion.current !== requestVersion) return null;
      setData(invoice); setError(""); setNotFound(false);
      setApprovalOutcomeUnresolved(false);
      const nextId = invoice.recommendation?.id || null;
      const sameDraft = draftRecommendation.current === nextId;
      setDraft(previous => sameDraft && ["proposed", "failed"].includes(invoice.recommendation?.state || "") ? previous : sessionStorage.getItem(`latepayer-draft:${nextId}`) ?? invoice.recommendation?.edited_text ?? "");
      draftRecommendation.current = nextId;
      return invoice;
    }).catch(cause => {
      if (refreshVersion.current !== requestVersion) return null;
      setError(cause instanceof Error ? cause.message : "Could not load invoice.");
      setNotFound(cause instanceof RequestError && cause.status === 404);
      return null;
    }).finally(() => {
      if (refreshInFlight.current?.promise === pending) {
        setLoading(false);
        refreshInFlight.current = null;
      }
    });
    refreshInFlight.current = { key: requestKey, promise: pending };
    return pending;
  }, [id, clockQuery]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (notFound) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh, notFound]);

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
    const providerApproval = !data.source.startsWith("demo")
      && /^\/api\/recommendations\/[^/]+\/(send|approve-partial-payment)$/.test(url);
    setBusy(true); setError(""); setNotice("");
    if (providerApproval) setApprovalOutcomeUnresolved(true);
    try {
      const result = await requestJson<{ state?: string; simulated?: boolean; note?: string }>(url, { method, body: body ? JSON.stringify(body) : undefined });
      if (providerApproval && result.state) {
        setData(current => current?.recommendation
          ? { ...current, recommendation: { ...current.recommendation, state: result.state! } }
          : current);
        setApprovalOutcomeUnresolved(false);
      }
      setNotice(result.note || (result.simulated ? "Demo action simulated; nothing was sent through PayPal." : `Action ${result.state || "completed"}.`));
      await refresh(providerApproval);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Action failed.";
      if (!providerApproval) {
        setError(message);
        clearReads();
        await refresh(true);
      } else {
        clearReads();
        const current = await refresh(true);
        if (!current) {
          setApprovalOutcomeUnresolved(true);
          setError("Could not confirm the PayPal approval outcome. This action stays disabled until the latest server state can be loaded. Retry the status check before continuing.");
        } else {
          const state = current.recommendation?.state || "unavailable";
          const lastAttempt = current.attempts?.[0];
          if (state === "sent") {
            setNotice("Approval outcome reconciled: the latest invoice record confirms PayPal accepted the reminder.");
            setError("");
          } else if (state === "applied") {
            setNotice("Approval outcome reconciled: the latest invoice record confirms partial payments are enabled.");
            setError("");
          } else if (state === "sending" || lastAttempt?.state === "sending") {
            setNotice("The approval is still processing. The server records an active provider attempt; leave this page open and do not submit again.");
            setError("");
          } else if (state === "unknown") {
            setNotice("The provider outcome remains uncertain. Sync PayPal and reconcile the invoice before retrying.");
            setError("");
          } else if (state === "failed") {
            setError(`The latest server record confirms the provider rejected the approval. Review the recorded attempt before retrying. ${message}`);
          } else {
            setError(`The latest server record shows ${state}; review the refreshed invoice and attempt history before continuing. ${message}`);
          }
        }
      }
    }
    finally { setBusy(false); }
  };

  const recordPayment = async (amount?: string) => {
    if (!data || data.simulation_disabled) return;
    setBusy(true); setError(""); setNotice("");
    const paymentAmount = amount || Number(data.outstanding).toFixed(2);
    const storageKey = `latepayer-demo-payment:${id}:${paymentAmount}`;
    let idempotencyKey = sessionStorage.getItem(storageKey);
    if (!idempotencyKey) {
      idempotencyKey = crypto.randomUUID();
      sessionStorage.setItem(storageKey, idempotencyKey);
    }
    try {
      const result = await requestJson<{ note: string; status: string; replayed?: boolean }>(`/api/demo/invoices/${encodeURIComponent(id)}/payment`, {
        method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ amount: paymentAmount }),
      });
      sessionStorage.removeItem(storageKey);
      setNotice(`${result.note} Invoice status is now ${result.status}.`);
      await refresh();
      if (!amount && result.status === "PAID") {
        window.localStorage.setItem("latepayer-demo-payment-updated", new Date().toISOString());
        const query = new URLSearchParams(portfolioQuery || "view=open&page=1&page_size=25&sort=action&direction=asc");
        query.set("view", "all"); query.set("page", "1"); query.delete("as_of");
        query.delete("age_bucket"); query.delete("action_only"); query.delete("drifting_only");
        router.push(`/?${query.toString()}`);
      }
    } catch (cause) { setError(`${cause instanceof Error ? cause.message : "Payment simulation failed."} The request identity is retained; retrying the same amount checks the existing result before recording another payment.`); }
    finally { setBusy(false); }
  };

  const generateDraft = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await requestJson<{ recommendation: NonNullable<Recommendation>; note: string; provider: string; model: string | null; grounded_in: string }>(`/api/invoices/${encodeURIComponent(id)}/draft`, { method: "POST", signal: AbortSignal.timeout(45_000) });
      setData(current => current ? { ...current, recommendation: result.recommendation } : current);
      setDraft(result.recommendation.edited_text);
      sessionStorage.setItem(`latepayer-draft:${result.recommendation.id}`, result.recommendation.edited_text);
      draftRecommendation.current = result.recommendation.id;
      setNotice(`${result.note} Source: ${result.provider}${result.model ? ` · ${result.model}` : ""}. Grounded in: ${result.grounded_in}.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create a reminder draft."); }
    finally { setBusy(false); }
  };

  const triageReply = async () => {
    if (!replyText.trim()) return;
    setBusy(true); setError(""); setNotice(""); setReplyTriage(null);
    try {
      const result = await requestJson<ReplyTriage>(`/api/invoices/${encodeURIComponent(id)}/reply`, { method: "POST", body: JSON.stringify({ reply: replyText }) });
      setReplyTriage(result); setNotice(result.note);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not classify the client reply."); }
    finally { setBusy(false); }
  };

  const reviewTriage = async (apply: boolean) => {
    if (!visibleReplyTriage) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const path = `/api/invoices/${encodeURIComponent(id)}/reply-triage/${encodeURIComponent(visibleReplyTriage.id)}/${apply ? "apply" : "dismiss"}`;
      const result = await requestJson<{ note: string }>(path, {
        method: "POST",
        ...(apply ? { body: JSON.stringify({ classification: visibleReplyTriage.classification, promised_payment_date: visibleReplyTriage.promised_payment_date }) } : {}),
      });
      setNotice(result.note); setReplyTriage(null); setReplyText(""); setReplyReviewUnresolved(false);
      const latest = await refresh(true);
      if (!latest) setError("Reply review saved, but the invoice refresh failed. The server accepted the review; retry the refresh when ready.");
    } catch (cause) {
      clearReads();
      const authoritative = await requestJson<ReplyTriageStatus>(`/api/invoices/${encodeURIComponent(id)}/reply-triage/${encodeURIComponent(visibleReplyTriage.id)}`).catch(() => null);
      if (authoritative?.state === "applied" || authoritative?.state === "dismissed") {
        setData(current => current ? { ...current, disputed: authoritative.disputed, promised_payment_date: authoritative.invoice_promised_payment_date,
          reply_triages: current.reply_triages?.map(row => row.id === authoritative.id ? { ...row, state: authoritative.state, classification: authoritative.classification, promised_payment_date: authoritative.promised_payment_date } : row) } : current);
        setReplyTriage(null); setReplyText(""); setReplyReviewUnresolved(false);
        setNotice(`Status check confirms this reply was ${authoritative.state === "applied" ? "approved" : "dismissed"}. The invoice now reflects the saved review.`);
        setError("");
      } else if (authoritative?.state === "pending") {
        setReplyTriage({ ...visibleReplyTriage, state: "pending" }); setReplyReviewUnresolved(false);
        setError(`Status check confirms the preview is still pending, so the review did not apply. You can correct it and retry. ${cause instanceof Error ? cause.message : ""}`);
      } else {
        setReplyReviewUnresolved(true);
        setError(`Could not confirm the reply review outcome. Use Check status before approving again. ${cause instanceof Error ? cause.message : ""}`);
      }
    }
    finally { setBusy(false); }
  };

  const checkReplyReviewStatus = async () => {
    clearReads();
    const triageId = replyTriage?.id || pendingTriage?.id;
    if (!triageId) { setReplyReviewUnresolved(true); setError("No reply preview is available to check."); return; }
    const status = await requestJson<ReplyTriageStatus>(`/api/invoices/${encodeURIComponent(id)}/reply-triage/${encodeURIComponent(triageId)}`).catch(() => null);
    if (!status) { setReplyReviewUnresolved(true); setError("The server status is still unavailable. Retry this check before approving again."); return; }
    setData(current => current ? { ...current, disputed: status.disputed, promised_payment_date: status.invoice_promised_payment_date,
      reply_triages: current.reply_triages?.map(row => row.id === status.id ? { ...row, state: status.state, classification: status.classification, promised_payment_date: status.promised_payment_date } : row) } : current);
    if (status.state === "applied" || status.state === "dismissed") {
      setReplyTriage(null); setReplyText(""); setReplyReviewUnresolved(false);
      setNotice(`Status check confirms this reply was ${status.state === "applied" ? "approved" : "dismissed"}.`); setError("");
    } else {
      setReplyTriage({ ...(replyTriage || pendingTriage!), ...status, reminders_paused: Boolean(status.disputed || status.invoice_promised_payment_date), note: "Preview only. Review or correct the extracted type and date, then approve it to update invoice state." });
      setReplyReviewUnresolved(false); setNotice("Status check confirms this preview is still pending. No invoice change was applied."); setError("");
    }
  };

  const resolveDispute = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await requestJson<{ note: string }>(`/api/invoices/${encodeURIComponent(id)}/resolve-dispute`, { method: "POST" });
      setNotice(result.note); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not resolve the dispute."); }
    finally { setBusy(false); }
  };

  if (loading && !data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></header><main className="main-wrap"><div className="empty-box">Loading invoice…</div></main></div>;
  if (error && !data && notFound) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></header><main className="main-wrap"><div className="panel"><div className="eyebrow">Invoice detail</div><h1>Invoice not found</h1><p className="subheading">This invoice may have been removed or the link may be out of date.</p><div className="toolbar-tools"><Link className="btn" href="/">Back to portfolio</Link><form action="/" method="get" className="toolbar-tools"><input className="search" name="q" aria-label="Search invoices" placeholder="Search invoice or client" value={missingSearch} onChange={event => setMissingSearch(event.target.value)}/><button className="btn primary" disabled={!missingSearch.trim()}>Search</button></form></div></div></main></div>;
  if (error && !data) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></header><main className="main-wrap"><div className="error-box">{error.includes("unavailable") || error.includes("502") ? "The API may be waking up. Refresh to load this invoice." : error} <button className="btn" onClick={() => void refresh()}>Retry</button> {error.includes("login") && <Link className="table-link" href="/login">Operator login</Link>}</div></main></div>;
  if (!data) return null;
  const rec = data.recommendation;
  const isDemo = data.source.startsWith("demo");
  const p50 = data.forecast?.quantiles.p50;
  const actionUrl = isDemo ? `/api/demo/recommendations/${rec?.id}/send` : `/api/recommendations/${rec?.id}/send`;
  const canSend = Boolean(!approvalOutcomeUnresolved && !clockDate && !data.simulation_disabled && rec && ["proposed", "failed"].includes(rec.state) && ["friendly", "firm"].includes(data.decision.action) && rec.action === data.decision.action && (isDemo || authenticated));
  const isPartialAction = Boolean(rec && rec.action === "offer_partial_payment");
  const canApplyPartial = Boolean(!approvalOutcomeUnresolved && !clockDate && !data.simulation_disabled && isPartialAction && rec && rec.action === data.decision.action && ["proposed", "failed"].includes(rec.state) && (isDemo || authenticated));

  return <div className="app-shell">
    <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← All invoices</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link>{authenticated ? <span>Operator session</span> : <Link className="btn" href="/login">Operator login</Link>}</div></header>
    <main className="main-wrap">
      {data.simulation_disabled && <div className="demo-banner" role="status">Viewing as of {clockDate} · return the clock to today to simulate actions.</div>}
      {isDemo && <div className="demo-banner"><span className="demo-dot"/><strong>Synthetic demo</strong><span>Forecast and approval flow use generated records. Simulated actions do not contact PayPal.</span></div>}
      {error && <div className="error-box" role="alert" style={{ marginBottom: 14 }}>{error} <button className="btn" onClick={() => void refresh()}>Retry</button></div>}
      <div className="heading-row"><div><div className="eyebrow">Invoice detail · {isDemo ? "Synthetic" : "PayPal Sandbox"}</div><h1>{data.invoice_number}</h1><p className="subheading"><Link className="table-link" href={`/clients/${encodeURIComponent(data.client_id)}`}>{data.client_name}</Link>{data.client_email ? ` · ${data.client_email}` : ""}</p></div><div className="top-actions"><span className={`status-pill ${data.disputed ? "disputed" : data.status.toLowerCase()}`}>{statusLabel(data.status, data.disputed)}</span>{!isDemo && authenticated && <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { await requestJson("/api/sync", { method: "POST" }); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Sync failed."); } finally { setBusy(false); } }}>↻ Sync PayPal</button>}</div></div>
      <div className="fine-print" style={{ marginBottom: 10 }}>Displayed timestamps use the merchant timezone, IST.</div>
      <nav className="invoice-navigation" aria-label="Invoice navigation"><div>{neighbors.previous ? <Link className="btn" href={`/invoices/${encodeURIComponent(neighbors.previous.id)}${neighbors.previous.query ? `?${neighbors.previous.query}` : ""}`}>← {neighbors.previous.invoice_number}</Link> : <span className="fine-print">No previous invoice</span>}</div><span className="fine-print">{neighborsLoading ? "Loading current view…" : "Current portfolio view"}</span><div>{neighbors.next ? <Link className="btn" href={`/invoices/${encodeURIComponent(neighbors.next.id)}${neighbors.next.query ? `?${neighbors.next.query}` : ""}`}>{neighbors.next.invoice_number} →</Link> : <span className="fine-print">No next invoice</span>}</div></nav>
      <div className="detail-layout">
        <section>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">Payment timing forecast</div><div className="panel-subtitle">Probability the remaining balance is still unpaid over time · conditional on unpaid today</div></div>{data.forecast && <span className="status-pill">{data.forecast.display_label || data.forecast.model.replaceAll("_", " ")}</span>}</div>
            {!data.forecast ? <div className="empty-box">No payment forecast for a closed or fully paid invoice.</div> : <>
              <div className="forecast-number">{p50 ? shortDate(p50.date) : "Beyond 180 days"}</div>
              <div className="panel-subtitle">Median expected full settlement{data.forecast.already_overdue ? " · invoice is already overdue" : ""}</div>
              {data.outlier_hint && <div className="delivery-note" style={{ marginTop: 12 }}>{data.outlier_hint}</div>}
              <div className="drift-card" style={{ marginTop: 12 }}><strong>Why this forecast</strong><p>{data.why}</p></div>
              <SurvivalCurve curve={data.forecast.curve} quantiles={data.forecast.quantiles}/>
              {data.forecast.limited_history && <div className="delivery-note">Limited history · fewer than 5 settled invoices; estimates use pooled priors.</div>}
              <div className="fine-print">{data.forecast.coverage_interval?.method || data.forecast.interval_method}</div><div className="legend"><span className="legend-item"><i className="legend-line"/>Unpaid probability</span><span className="legend-item"><i className="legend-band"/>Curve P10–P90 range</span></div>
              {data.forecast.coverage_interval && <div className="delivery-note" style={{ marginTop: 8 }}>Validation-calibrated {Math.round(data.forecast.coverage_interval.target_coverage * 100)}% coverage interval: {shortDate(data.forecast.coverage_interval.lower_date)} – {shortDate(data.forecast.coverage_interval.upper_date)}. This wider interval is separate from the curve percentiles. {data.forecast.coverage_interval.caveat}</div>}
              {data.forecast.promise_adjustment?.applied && <div className="delivery-note" style={{ marginTop: 8 }}><strong>Promise scenario · {shortDate(data.forecast.promise_adjustment.date)}</strong><p>{data.forecast.promise_adjustment.method} {data.forecast.promise_adjustment.caveat}</p></div>}
              <div className="quantiles"><div className="quantile"><div className="quantile-label">Paid by due date</div><div className="quantile-value">{Math.round(data.forecast.probability_by_due * 100)}%</div></div><div className="quantile"><div className="quantile-label">Paid in 14 days</div><div className="quantile-value">{Math.round(data.forecast.probabilities["14d"] * 100)}%</div></div><div className="quantile"><div className="quantile-label">Paid in 30 days</div><div className="quantile-value">{Math.round(data.forecast.probabilities["30d"] * 100)}%</div></div></div>
              <div className="quantiles">{(["p10", "p50", "p90"] as const).map(key => <div className="quantile" key={key}><div className="quantile-label">{key.toUpperCase()} payment date</div><div className="quantile-value">{shortDate(data.forecast?.quantiles[key]?.date)}</div></div>)}</div>
              <ul className="reasons">{data.forecast.reasons.map((reason, index) => <li key={index}><span className="reason-mark">✓</span>{reason}</li>)}</ul>
              {data.forecast.collection_method && <div className="delivery-note" style={{ marginTop: 12 }}><strong>Recovery estimate (heuristic): {Math.round((data.forecast.collection_probability || 0) * 100)}%</strong><p>{data.currency} {(Number(data.outstanding) * (data.forecast.collection_probability || 0)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} of the current balance. Sparse historical recovery evidence; this is not a measured collection probability.</p></div>}
              <div className="fine-print" style={{ marginTop: 15 }}>Model: {data.forecast.model_version} · Scored {formatIST(data.forecast.scored_at)} · 180-day horizon · curve uses validation-fitted 14-day calibration</div>
            </>}
          </div>
          <div className="panel"><div className="panel-head"><div><div className="panel-title">Client timing signal</div><div className="panel-subtitle">Settled delays plus open overdue invoices as right-censored lower bounds</div></div><span className="status-pill">{signalLabel(data.drift.signal)}</span></div><div className="drift-card"><strong>{data.drift.available ? `${data.drift.settled_count} settled invoices reviewed` : "Insufficient history"}</strong><p>{data.drift.message}</p><div className="fine-print">The forecast used {data.forecast?.history_count ?? 0} prior settled invoices known on its scoring date; current drift also includes unpaid delay lower bounds.</div></div></div>
          {data.outcome && <div className="panel"><div className="panel-head"><div><div className="panel-title">Paid outcome</div><div className="panel-subtitle">Observed result against the issue-time model backtest</div></div></div><div className="invoice-facts"><div className="fact"><div className="fact-label">Paid date</div><div className="fact-value">{data.outcome.paid_at ? shortDate(data.outcome.paid_at) : "Paid date unavailable"}</div></div><div className="fact"><div className="fact-label">Against due date</div><div className="fact-value">{data.outcome.days_from_due == null ? "Unavailable" : data.outcome.timing === "early" ? `${Math.abs(data.outcome.days_from_due)} days early` : data.outcome.timing === "late" ? `${data.outcome.days_from_due} days late` : "On time"}</div></div></div>{data.outcome.issue_forecast ? <div className="drift-card" style={{ marginTop: 12 }}><strong>Issue-time p50: {shortDate(data.outcome.issue_forecast.p50_date)}</strong><p>Actual payment: {data.outcome.paid_at ? shortDate(data.outcome.paid_at) : "Date unavailable"}{data.outcome.issue_forecast.absolute_error_days != null ? ` · ${data.outcome.issue_forecast.absolute_error_days} day absolute error` : ""}</p><div className="fine-print">p10 {shortDate(data.outcome.issue_forecast.p10_date)} · p90 {shortDate(data.outcome.issue_forecast.p90_date)} · {data.outcome.issue_forecast.source.replaceAll("_", " ")}{data.outcome.issue_forecast.model_label ? ` · ${data.outcome.issue_forecast.model_label.replaceAll("_", " ")}` : ""}{data.outcome.issue_forecast.model_version ? ` · ${data.outcome.issue_forecast.model_version}` : ""}</div></div> : <div className="empty-inline">No issue-time prediction was stored for this invoice.</div>}</div>}
          <div className="panel"><div className="panel-head"><div><div className="panel-title">Invoice timeline</div><div className="panel-subtitle">Issue, delivery, views, reminders, payments, and provider events</div></div></div><div className="history-list">{data.timeline.length ? data.timeline.map((event, index) => <div className="history-row" key={`${event.at}-${event.kind}-${index}`}><span className="history-dot"/><div><div className="history-title">{event.kind}</div><div className="history-meta">{["Issued", "Sent"].includes(event.kind) ? shortDate(event.at) : formatIST(event.at)}</div>{event.detail && <div className="history-meta">{event.detail}</div>}</div></div>) : <div className="empty-box">No dated invoice events are available.</div>}</div></div>
        </section>
        <aside>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">Invoice status</div><div className="panel-subtitle">Balance and due-date details</div></div></div>
            <div className="invoice-facts">
              <div className="fact"><div className="fact-label">Outstanding</div><div className="fact-value">{data.currency} {Number(data.outstanding).toLocaleString("en-US", { minimumFractionDigits: 2 })}</div></div>
              <div className="fact"><div className="fact-label">Paid</div><div className="fact-value">{data.currency} {Number(data.paid).toLocaleString("en-US", { minimumFractionDigits: 2 })}</div></div>
              <div className="fact"><div className="fact-label">Due date</div><div className="fact-value">{shortDate(data.due_date)}</div></div>
              <div className="fact"><div className="fact-label">Currency</div><div className="fact-value">{data.currency}</div></div>
              <div className="fact"><div className="fact-label">Viewed</div><div className="fact-value">{data.viewed_by_recipient ? data.viewed_at ? shortDate(data.viewed_at) : "Yes · exact time unavailable" : "Not yet"}</div></div>
              <div className="fact"><div className="fact-label">Partial payments</div><div className="fact-value">{data.partial_payment_allowed ? "Allowed" : "Not enabled"}</div></div>
              <div className="fact"><div className="fact-label">Payment terms</div><div className="fact-value">{termLabel(data.payment_term_type)}</div></div>
              <div className="fact"><div className="fact-label">Decision</div><div className="fact-value">{decisionLabel(data.decision.action, data.status, data.disputed)}</div></div>
              <div className="fact"><div className="fact-label">Policy reason</div><div className="fact-value" style={{ fontSize: 11, lineHeight: 1.35 }}>{data.decision.reason}</div></div>
            </div>
            <button className="btn" onClick={() => { void navigator.clipboard.writeText(window.location.href).then(() => setNotice("Invoice link copied.")).catch(() => setError("Could not copy the invoice link.")); }}>Copy invoice link</button>
            {!isDemo && data.payment_url && <a className="btn" href={data.payment_url} target="_blank" rel="noreferrer">Open PayPal invoice ↗</a>}
            {isDemo && Number(data.outstanding) > 0 && <div className="action-buttons"><button className="btn" disabled={busy || data.simulation_disabled} onClick={() => void recordPayment((Number(data.outstanding) / 2).toFixed(2))}>Simulate partial payment</button><button className="btn" disabled={busy || data.simulation_disabled} onClick={() => void recordPayment()}>Simulate full payment</button></div>}
          </div>
          <div className="panel">
            <div className="panel-head"><div><div className="panel-title">{isPartialAction ? "Partial-payment review" : "Reminder review"}</div><div className="panel-subtitle">{rec ? rec.reason : data.decision.reason}</div></div>{rec && <span className="recommendation-tag">{rec.state}</span>}</div>
            {approvalOutcomeUnresolved && <div className="demo-banner" role="status"><span>The last approval timed out and its provider outcome could not be confirmed. Approval controls stay locked until the latest invoice and attempt state loads.</span><button className="btn" disabled={busy} onClick={() => { clearReads(); void refresh(true); }}>Check latest status</button></div>}
            {rec && isPartialAction ? <>
              <div className="drift-card"><strong>PayPal payment setting</strong><p>{rec.edited_text}</p><div className="fine-print">Approval enables partial payments on the existing invoice. The provider update may notify the recipient.</div></div>
              <div className="action-buttons">
                {canApplyPartial && <button className="btn primary" disabled={busy} onClick={() => {
                  const url = isDemo ? `/api/demo/recommendations/${rec.id}/send` : `/api/recommendations/${rec.id}/approve-partial-payment`;
                  if (isDemo || window.confirm("Enable partial payments on this PayPal Sandbox invoice? PayPal may notify the recipient of the invoice update.")) void action(url, "POST", { text: rec.edited_text });
                }}>{busy ? "Applying…" : isDemo ? "Approve & simulate" : "Approve & enable partial payment"}</button>}
                {rec.state === "proposed" && <button className="btn" disabled={busy || approvalOutcomeUnresolved || Boolean(clockDate) || data.simulation_disabled} onClick={() => void action(`/api/recommendations/${rec.id}/skip`)}>Skip</button>}
                {rec.state === "applied" && <span className="fine-print">Partial-payment settings are enabled{isDemo ? " in the synthetic demo" : " in PayPal Sandbox"}.</span>}
                {rec.state === "unknown" && <span className="fine-print">Provider update status is uncertain. Sync PayPal before retrying.</span>}
                {rec.state === "failed" && <span className="fine-print">The provider rejected the update. Review and retry is available.</span>}
              </div>
            </> : rec ? <>
              <div className="fine-print">Grounded in: {data.invoice_number}, {data.currency} {Number(data.outstanding).toLocaleString("en-US")}, due {shortDate(data.due_date)}, recorded reminder and settlement history.</div><div className="fine-print" style={{ marginBottom: 8 }}>Draft source: {rec.draft_source === "gemini" ? `Google Gemini${rec.draft_model ? ` · ${rec.draft_model}` : " · model not recorded"} · grounded in invoice facts` : "grounded template"}{rec.drafted_at ? ` · ${formatIST(rec.drafted_at)}` : ""}</div>
              {!isDemo && <div className="delivery-note"><strong>What the recipient receives</strong><p>{data.client_email ? `To: ${data.client_email}. ` : "To the recipient saved on this PayPal invoice. "}PayPal sends its own system reminder for {data.invoice_number}, including the hosted invoice and payment details. PayPal controls the subject and body; the editable text below is an internal collection note and is not sent.</p></div>}
              {isDemo && <div className="delivery-note"><strong>Demo preview</strong><p>This edited copy is retained as the simulated approval note. The synthetic demo sends no email.</p></div>}
              <label className="field-label">{isDemo ? "Editable simulated reminder note" : "Internal collection note · not emailed"}<textarea aria-label="Editable reminder preview" value={draft} onChange={event => { setDraft(event.target.value); sessionStorage.setItem(`latepayer-draft:${rec.id}`, event.target.value); }} onBlur={() => { if (!clockDate && !data.simulation_disabled && draft.trim() && ["proposed", "failed"].includes(rec.state)) void requestJson(`/api/recommendations/${rec.id}/draft`, { method: "POST", body: JSON.stringify({ text: draft }) }).catch(cause => setError(cause.message)); }} disabled={approvalOutcomeUnresolved || Boolean(clockDate) || data.simulation_disabled || rec.state === "sending" || rec.state === "sent" || rec.state === "unknown" || rec.state === "skipped"}/></label>
              <div className="action-buttons">
                {rec.state === "proposed" && <button className="btn" disabled={busy || approvalOutcomeUnresolved || Boolean(clockDate) || data.simulation_disabled} onClick={() => void generateDraft()}>{busy ? "Drafting…" : "Generate AI draft"}</button>}
                {canSend && <button className="btn primary" disabled={busy || !draft.trim()} onClick={() => { if (isDemo || window.confirm("Approve this PayPal Sandbox reminder? PayPal sends its system-generated email; this internal note is saved for review and is not emailed.")) void action(actionUrl, "POST", { text: draft }); }}>{busy ? "Processing…" : isDemo ? "Approve & simulate" : "Approve PayPal reminder"}</button>}
                {rec.state === "proposed" && <button className="btn" disabled={busy || approvalOutcomeUnresolved || Boolean(clockDate) || data.simulation_disabled} onClick={() => void action(`/api/recommendations/${rec.id}/skip`)}>Skip</button>}
                {rec.state === "sent" && <span className="fine-print">{isDemo ? "Recorded as a simulated demo action." : "PayPal accepted the reminder request."}</span>}
                {rec.state === "unknown" && <span className="fine-print">Delivery is uncertain. Sync PayPal and reconcile before trying again.</span>}
                {rec.state === "failed" && <span className="fine-print">The provider rejected the request. Review and retry is available.</span>}
              </div>
            </> : <div className="empty-box">No reminder proposed. {data.decision.action === "personal_call" ? "Make a personal call and record the outcome." : data.decision.action === "offer_partial_payment" ? "Review a partial-payment option for this client." : data.decision.action === "review_for_writeoff_or_collections" ? "Review this stale balance for write-off or collections." : data.decision.action === "wait" ? "The current policy says to wait." : "Open this invoice again to refresh its recommendation."}</div>}
            {notice && <div className="action-message" role="status">{notice}</div>}
          </div>
          <div className="panel" id="reply-triage"><div className="panel-head"><div><div className="panel-title">Client reply triage</div><div className="panel-subtitle">Preview the extracted type and date, then approve or dismiss. Invoice state does not change until approval.</div></div></div>
            {replyReviewUnresolved && <div className="demo-banner" role="status"><span>The last reply review timed out and its saved state could not be confirmed. Check status before approving again.</span><button className="btn" disabled={busy} onClick={() => void checkReplyReviewStatus()}>Check status</button></div>}
            <textarea aria-label="Paste client reply" placeholder="Example: We will pay Friday." value={replyText} onChange={event => setReplyText(event.target.value)} disabled={busy}/>
            <div className="action-buttons"><button className="btn" disabled={busy || !replyText.trim()} onClick={() => void triageReply()}>{busy ? "Checking…" : "Classify reply"}</button>
              {data.disputed && <span className="status-pill">Reminders paused · disputed</span>}
              {data.promised_payment_date && !data.disputed && <span className="status-pill">Promise recorded · {shortDate(data.promised_payment_date)}</span>}
              {data.disputed && <button className="btn" disabled={busy} onClick={() => void resolveDispute()}>Resolve dispute</button>}
            </div>
            {visibleReplyTriage && <div className="reply-review" aria-label="Reply classification preview">
              <div className="fine-print"><strong>Preview · {visibleReplyTriage.classifier === "gemini" ? "Google Gemini classification" : "rule-based classification"}</strong> · {visibleReplyTriage.note}</div>
              <blockquote>{visibleReplyTriage.reply_text}</blockquote>
              <label className="field-label">Classification<select className="select" value={visibleReplyTriage.classification} onChange={event => setReplyTriage(current => ({ ...(current || visibleReplyTriage), classification: event.target.value }))}><option value="promise_to_pay">Promise to pay</option><option value="dispute">Dispute</option><option value="question">Question</option><option value="other">Other</option></select></label>
              {visibleReplyTriage.classification === "promise_to_pay" && <label className="field-label">Promised payment date<input type="date" value={visibleReplyTriage.promised_payment_date || ""} onChange={event => setReplyTriage(current => ({ ...(current || visibleReplyTriage), promised_payment_date: event.target.value || null }))}/></label>}
              <div className="action-buttons"><button className="btn primary" disabled={busy || replyReviewUnresolved} onClick={() => void reviewTriage(true)}>Approve classification</button><button className="btn" disabled={busy || replyReviewUnresolved} onClick={() => void reviewTriage(false)}>Dismiss</button></div>
            </div>}
          </div>
          {data.reminder_outcomes?.length ? <div className="panel"><div className="panel-title">Next-week reminder outcomes</div><p className="fine-print">Payments are checked for seven days after each recorded reminder. Partial payments are kept separate from full settlement.</p><div className="history-list">{data.reminder_outcomes.map((outcome, index) => <div className="history-row" key={`${outcome.sent_at}-${index}`}><span className="history-dot"/><div><div className="history-title">{outcome.tone} reminder · {outcome.status === "checking" ? "checking through " + shortDate(outcome.window_ends) : outcome.status}</div><div className="history-meta">Sent {shortDate(outcome.sent_at)} · {outcome.amount_paid !== "0.00" ? `${outcome.currency} ${outcome.amount_paid} received` : `Observed through ${shortDate(outcome.observed_through)}`}</div></div></div>)}</div></div> : null}
          {rec && <div className="panel"><div className="panel-head"><div><div className="panel-title">Action history</div><div className="panel-subtitle">Dispatch attempts</div></div></div><div className="history-list">{(data.attempts || []).length ? data.attempts?.map((attempt, index) => <div className="history-row" key={`${attempt.started_at}-${index}`}><span className="history-dot"/><div><div className="history-title">{attempt.state}{attempt.provider_reference ? ` · ${attempt.provider_reference}` : ""}</div><div className="history-meta">{formatIST(attempt.started_at)}</div>{attempt.error && <div className="history-meta">{attempt.error}</div>}</div></div>) : <div className="fine-print">No send attempts recorded.</div>}</div></div>}
        </aside>
      </div>
    </main>
  </div>;
}

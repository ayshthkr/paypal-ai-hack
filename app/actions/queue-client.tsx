"use client";

import Link from "next/link";
import { requestJson, clearReads, ensureDemoSession } from "../lib/api";
import { decisionLabel } from "../lib/labels";
import { useCallback, useEffect, useRef, useState } from "react";

type QueueItem = {
  id: string; invoice_number: string; client_name: string; client_id: string; client_email?: string | null; source: string; currency: string; outstanding: string;
  due_date: string; days_late?: number; decision: { action: string; reason: string; expected_cash_gain: number };
  why?: string;
  reminder_outcomes?: { sent_at: string; tone: string; status: "paid" | "partial" | "silent" | "checking"; amount_paid: string; currency: string; window_ends: string }[];
  suggested_review_steps?: string[];
  collection_review?: { owner: string; notes: string; follow_up_date: string | null; resolution_status: string };
  recommendation: { id: string; action: string; tone: string; edited_text: string; reason: string; state: string } | null;
};
export type QueueResult = { items: QueueItem[]; count: number; daily_cap: number; as_of: string; synthetic_demo: boolean; pending_count: number; skipped_count: number; skipped: { id: string; invoice_id: string }[]; collections_review?: QueueItem[]; collections_totals?: Record<string, string>; review_time?: { completed_count: number; median_seconds: number | null; sample_window: string } };

function money(currency: string, value: number | string) { return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
function reviewClockNow() { return performance.now(); }
function reviewDurationSeconds(startedAt: number) { return Math.max(1, Math.ceil((reviewClockNow() - startedAt) / 1000)); }
function readDrafts(items: QueueItem[]) {
  return Object.fromEntries(items.map(item => [item.recommendation?.id || item.id,
    sessionStorage.getItem(`latepayer-draft:${item.recommendation?.id}`) ?? item.recommendation?.edited_text ?? ""]));
}

export default function ActionQueue() {
  const [data, setData] = useState<QueueResult | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const saveTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const reviewStartedAt = useRef(new Map<string, number>());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [error, setError] = useState("");
  const [waking, setWaking] = useState(false);
  const [collectionDrafts, setCollectionDrafts] = useState<Record<string, { owner: string; notes: string; follow_up_date: string; resolution_status: string }>>({});
  const refresh = useCallback(async () => {
    setLoading(true); setError(""); setWaking(false);
    const wakeMessage = window.setTimeout(() => setWaking(true), 900);
    try {
      await ensureDemoSession();
      const deadline = Date.now() + 30_000;
      let result: QueueResult | null = null;
      let lastError = "";
      while (Date.now() < deadline && !result) {
        try {
          const timeoutMs = Math.max(1_000, Math.min(10_000, deadline - Date.now()));
          const response = await fetch("/api/action-queue", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(timeoutMs) });
          const value = await response.json().catch(() => ({}));
          if (response.ok) result = value as QueueResult;
          else if (response.status >= 500 || response.status === 429) lastError = value.detail || `Service returned ${response.status}.`;
          else throw new Error(value.detail || `Request failed (${response.status})`);
        } catch (cause) {
          if (cause instanceof Error) lastError = cause.message;
        }
        if (!result && Date.now() < deadline) await new Promise(resolve => window.setTimeout(resolve, Math.min(1_500, deadline - Date.now())));
      }
      if (!result) throw new Error(`The API did not wake within 30 seconds. ${lastError || "Refresh to try again."}`);
      setData(result);
      setDrafts(readDrafts(result.items));
      setCollectionDrafts(Object.fromEntries((result.collections_review || []).map(item => [item.id, {
        owner: item.collection_review?.owner || "", notes: item.collection_review?.notes || "",
        follow_up_date: item.collection_review?.follow_up_date || "",
        resolution_status: item.collection_review?.resolution_status || "open",
      }])));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load today's action queue."); }
    finally { window.clearTimeout(wakeMessage); setWaking(false); setLoading(false); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const updateCollection = (id: string, key: "owner" | "notes" | "follow_up_date" | "resolution_status", value: string) => {
    setCollectionDrafts(current => ({ ...current, [id]: { ...{ owner: "", notes: "", follow_up_date: "", resolution_status: "open" }, ...current[id], [key]: value } }));
  };

  const saveCollectionReview = async (item: QueueItem) => {
    const draft = collectionDrafts[item.id] || { owner: "", notes: "", follow_up_date: "", resolution_status: "open" };
    setBusyId(item.id); setError("");
    try {
      await requestJson(`/api/invoices/${encodeURIComponent(item.id)}/collection-review`, {
        method: "PATCH", body: JSON.stringify({ ...draft, follow_up_date: draft.follow_up_date || null }),
      });
      clearReads();
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save collections review."); }
    finally { setBusyId(""); }
  };

  const editDraft = (id: string, text: string) => {
    setDrafts(current => ({ ...current, [id]: text }));
    sessionStorage.setItem(`latepayer-draft:${id}`, text);
    clearTimeout(saveTimers.current.get(id));
    saveTimers.current.set(id, setTimeout(() => { if (text.trim()) void requestJson(`/api/recommendations/${id}/draft`, { method: "POST", body: JSON.stringify({ text }) }).catch(cause => setError(cause.message)); }, 500));
  };
  const act = async (item: QueueItem, operation: "approve" | "skip", confirmed = false) => {
    const recommendation = item.recommendation;
    if (!recommendation) return;
    const startedAt = reviewStartedAt.current.get(recommendation.id) ?? reviewClockNow();
    clearTimeout(saveTimers.current.get(recommendation.id));
    const isPartial = recommendation.action === "offer_partial_payment";
    if (operation === "approve" && !confirmed && item.source === "paypal" && !window.confirm(isPartial ? "Enable partial payments on this PayPal Sandbox invoice?" : "Send this reminder through PayPal Sandbox?")) return;
    setBusyId(recommendation.id); setError("");
    try {
      let path: string;
      let body: string | undefined;
      if (operation === "skip") path = `/api/recommendations/${recommendation.id}/skip`;
      else if (item.source.startsWith("demo")) { path = `/api/demo/recommendations/${recommendation.id}/send`; body = drafts[recommendation.id] || recommendation.edited_text; }
      else if (isPartial) path = `/api/recommendations/${recommendation.id}/approve-partial-payment`;
      else { path = `/api/recommendations/${recommendation.id}/send`; body = drafts[recommendation.id] || recommendation.edited_text; }
      const response = await fetch(path, { method: "POST", cache: "no-store", credentials: "same-origin", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify({ text: body }) : undefined });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      const reviewSeconds = reviewDurationSeconds(startedAt);
      reviewStartedAt.current.delete(recommendation.id);
      void requestJson(`/api/invoices/${encodeURIComponent(item.id)}/review-metric`, {
        method: "POST", body: JSON.stringify({ review_seconds: reviewSeconds, operation }),
      }).catch(() => undefined);
      clearReads();
      await refresh();
      requestAnimationFrame(() => (document.querySelector(".queue-card button:not(:disabled), [data-queue-refresh]") as HTMLButtonElement | null)?.focus());
      return true;
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not ${operation} this suggestion.`); return false; }
    finally { setBusyId(""); }
  };

  const approveFriendly = async () => {
    const cap = Math.min(5, data?.daily_cap ?? 5);
    const items = (data?.items.filter(item => item.recommendation?.action === "friendly") || []).slice(0, cap);
    if (!items.length) return;
    const itemList = items.map(item => {
      const recipient = !item.source.startsWith("demo")
        ? item.client_email || "recipient email unavailable"
        : "synthetic demo; no email sent";
      return `Invoice: ${item.invoice_number} · Recipient: ${item.client_name} (${recipient})`;
    }).join("\n");
    const deliveryNote = items.some(item => !item.source.startsWith("demo"))
      ? "PayPal Sandbox reminders send test notifications; synthetic reminders are simulated."
      : "Synthetic reminders are simulated; no PayPal email is sent.";
    if (!window.confirm(`Review each item before approval. Batch capped at ${cap}.\n\n${itemList}\n\n${deliveryNote}\n\nApprove these ${items.length} friendly reminders?`)) return;
    setBulkBusy(true);
    try { for (const item of items) { if (!await act(item, "approve", true)) break; } }
    finally { setBulkBusy(false); }
  };
  return <>
    <div className="heading-row"><div><div className="eyebrow">Daily workflow</div><h1>Action queue</h1><p className="subheading">Review suggested reminders and partial-payment changes. The five-action cap is shared across currencies by currency lane.</p></div><button data-queue-refresh className="btn" disabled={loading || Boolean(busyId)} onClick={() => void refresh()}>↻ Refresh queue</button></div>
    {data && <div className="fine-print" style={{ marginBottom: 12 }}>As of {data.as_of} · {data.pending_count} pending · {data.skipped_count} skipped · cap {data.daily_cap} · {data.synthetic_demo ? "Synthetic actions are simulated" : "PayPal actions use Sandbox"}</div>}
    {data?.review_time && <div className="fine-print" style={{ marginBottom: 12 }}>Measured queue review time: {data.review_time.median_seconds == null ? "no completed actions yet" : `${data.review_time.median_seconds}s median`} across {data.review_time.completed_count} completed actions. This is elapsed review time; no manual baseline is collected, so it is not reported as time saved.</div>}
    {loading && data && waking && <div className="empty-inline" role="status">Waking the API… refreshing today&apos;s queue. The first request can take up to 30 seconds.</div>}
    {data?.items.length ? <div className="fine-print" style={{ marginBottom: 12 }}>The 14-day uplift is a 10% assumed scenario, not an observed effect. The outcome rows below show recorded payments after reminders; results are descriptive and do not establish causality.</div> : null}
    {data && <div className="action-buttons"><button className="btn primary" disabled={bulkBusy || Boolean(busyId) || !data.items.some(item => item.recommendation?.action === "friendly")} onClick={() => void approveFriendly()}>Approve all friendly</button>{data.skipped.map(item => <button key={item.id} className="btn" disabled={Boolean(busyId) || bulkBusy} onClick={() => { void requestJson(`/api/recommendations/${item.id}/undo-skip`, { method: "POST" }).then(() => refresh()).catch(cause => setError(cause.message)); }}>Undo skip · {item.invoice_id}</button>)}</div>}
    {error && <div className="error-box" role="alert">{error}</div>}
    {loading && !data ? <div className="empty-box" role="status">{waking ? "Waking the API… the first request can take up to 30 seconds." : "Loading today’s suggested actions…"}</div> : error && !data ? <div className="empty-box">The action queue could not load. {error} <button className="btn" onClick={() => void refresh()}>Retry</button></div> : !data?.items.length ? <div className="empty-box">No reminder or partial-payment action is pending today. The policy may recommend waiting, or all suggestions may already be reviewed.</div> : <div className="queue-list">{data.items.map(item => {
      const recommendation = item.recommendation;
      if (!recommendation) return null;
      const busy = busyId === recommendation.id;
      const partial = recommendation.action === "offer_partial_payment";
      return <article className="queue-card" key={recommendation.id}
        onPointerDownCapture={() => { if (!reviewStartedAt.current.has(recommendation.id)) reviewStartedAt.current.set(recommendation.id, reviewClockNow()); }}
        onFocusCapture={() => { if (!reviewStartedAt.current.has(recommendation.id)) reviewStartedAt.current.set(recommendation.id, reviewClockNow()); }}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className="recommendation-tag">{decisionLabel(recommendation.action)}</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</strong></span><span>Assumed 14-day uplift <strong>{money(item.currency, item.decision.expected_cash_gain)}</strong></span></div>
        <p className="panel-subtitle">{item.why || item.decision.reason}</p>
        {item.reminder_outcomes?.length ? <div className="fine-print">Next-week outcome: {item.reminder_outcomes.map(outcome => `${outcome.tone} · ${outcome.status === "checking" ? `checking through ${outcome.window_ends}` : outcome.status}${outcome.amount_paid !== "0.00" ? ` (${money(outcome.currency, outcome.amount_paid)})` : ""}`).join(" · ")}</div> : null}
        {partial ? <div className="delivery-note">Approval will enable partial payments on this invoice; it does not send a reminder email.</div> : <label className="field-label">Internal reminder note · recipient receives PayPal&apos;s native message<textarea value={drafts[recommendation.id] || ""} onChange={event => editDraft(recommendation.id, event.target.value)}/></label>}
        <div className="action-buttons"><button className="btn primary" disabled={busy || bulkBusy || (!partial && !(drafts[recommendation.id] || "").trim())} onClick={() => void act(item, "approve")}>{busy ? "Applying…" : partial ? "Approve partial payments" : "Approve & send"}</button><button className="btn" disabled={busy || bulkBusy} onClick={() => void act(item, "skip")}>Skip</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
      </article>;
    })}</div>}
    {data?.collections_review?.length ? <section className="collections-lane" aria-labelledby="collections-review-title">
      <div className="panel-head"><div><div className="panel-title" id="collections-review-title">Collections review</div><div className="panel-subtitle">Open balances at least 90 days overdue. These need a person to review disputes, lost invoices, write-off, or collections options; no reminder is sent from this lane.</div><div className="fine-print">Lane total · {Object.entries(data.collections_totals || {}).map(([currency, amount]) => money(currency, amount)).join(" · ") || "No balances"}</div></div></div>
      <div className="queue-list">{data.collections_review.map(item => <article className="queue-card collection-card" key={item.id}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className="recommendation-tag">Collections review</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</strong></span><span>Overdue <strong>{item.days_late} days</strong></span></div>
        <p className="panel-subtitle">{item.why || item.decision.reason}</p>
        <div className="fine-print">Suggested review steps: {(item.suggested_review_steps || []).map(value => value.replaceAll("_", " ")).join(" · ")}</div>
        {(() => {
          const review = collectionDrafts[item.id] || { owner: item.collection_review?.owner || "", notes: item.collection_review?.notes || "", follow_up_date: item.collection_review?.follow_up_date || "", resolution_status: item.collection_review?.resolution_status || "open" };
          return <div className="collection-review-form" aria-label={`Collections workflow for ${item.invoice_number}`}>
            <label className="field-label">Owner<input value={review.owner} maxLength={128} placeholder="Assign a reviewer" onChange={event => updateCollection(item.id, "owner", event.target.value)}/></label>
            <label className="field-label">Follow-up date<input type="date" value={review.follow_up_date} onChange={event => updateCollection(item.id, "follow_up_date", event.target.value)}/></label>
            <label className="field-label">Resolution status<select className="select" value={review.resolution_status} onChange={event => updateCollection(item.id, "resolution_status", event.target.value)}><option value="open">Open</option><option value="in_progress">In progress</option><option value="awaiting_response">Awaiting response</option><option value="disputed">Disputed</option><option value="resolved">Resolved</option><option value="written_off">Written off</option></select></label>
            <label className="field-label">Review notes<textarea maxLength={4000} value={review.notes} placeholder="Call outcome, final notice, dispute details, or resolution" onChange={event => updateCollection(item.id, "notes", event.target.value)}/></label>
            <div className="action-buttons"><button className="btn primary" disabled={busyId === item.id} onClick={() => void saveCollectionReview(item)}>{busyId === item.id ? "Saving…" : "Save collections review"}</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
          </div>;
        })()}
      </article>)}</div>
    </section> : null}
  </>;
}

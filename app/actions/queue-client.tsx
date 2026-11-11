"use client";

import Link from "next/link";
import { requestJson, clearReads, ensureDemoSession, RequestError } from "../lib/api";
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
  const [collectionStates, setCollectionStates] = useState<Record<string, "dirty" | "saving" | "saved" | "saved_refresh_failed" | "failed" | "uncertain">>({});
  const [operationStates, setOperationStates] = useState<Record<string, "uncertain" | "saved_refresh_failed" | "confirmed">>({});
  const collectionStatesRef = useRef(collectionStates);
  collectionStatesRef.current = collectionStates;
  const refresh = useCallback(async (): Promise<QueueResult | null> => {
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
      const serverDrafts = Object.fromEntries((result.collections_review || []).map(item => [item.id, {
        owner: item.collection_review?.owner || "", notes: item.collection_review?.notes || "",
        follow_up_date: item.collection_review?.follow_up_date || "",
        resolution_status: item.collection_review?.resolution_status || "open",
      }]));
      setCollectionDrafts(current => Object.fromEntries(Object.entries(serverDrafts).map(([id, value]) => [
        id, ["dirty", "saving", "saved_refresh_failed", "failed", "uncertain"].includes(collectionStatesRef.current[id]) ? current[id] || value : value,
      ])));
      return result;
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load today's action queue."); return null; }
    finally { window.clearTimeout(wakeMessage); setWaking(false); setLoading(false); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const updateCollection = (id: string, key: "owner" | "notes" | "follow_up_date" | "resolution_status", value: string) => {
    setCollectionDrafts(current => ({ ...current, [id]: { ...{ owner: "", notes: "", follow_up_date: "", resolution_status: "open" }, ...current[id], [key]: value } }));
    setCollectionStates(current => ({ ...current, [id]: "dirty" }));
  };

  const matchesCollectionDraft = (item: QueueItem | undefined, draft: { owner: string; notes: string; follow_up_date: string; resolution_status: string }) => Boolean(item?.collection_review
    && item.collection_review.owner === draft.owner.trim()
    && item.collection_review.notes === draft.notes.trim()
    && (item.collection_review.follow_up_date || "") === draft.follow_up_date
    && item.collection_review.resolution_status === draft.resolution_status);

  const checkCollectionStatus = async (item: QueueItem) => {
    const draft = collectionDrafts[item.id] || { owner: "", notes: "", follow_up_date: "", resolution_status: "open" };
    clearReads();
    const latest = await refresh();
    if (!latest) { setCollectionStates(current => ({ ...current, [item.id]: "uncertain" })); return; }
    const currentItem = latest.collections_review?.find(row => row.id === item.id);
    if (matchesCollectionDraft(currentItem, draft)) setCollectionStates(current => ({ ...current, [item.id]: "saved" }));
    else setCollectionStates(current => ({ ...current, [item.id]: "failed" }));
  };

  const saveCollectionReview = async (item: QueueItem) => {
    const draft = collectionDrafts[item.id] || { owner: "", notes: "", follow_up_date: "", resolution_status: "open" };
    setCollectionStates(current => ({ ...current, [item.id]: "saving" }));
    setBusyId(item.id); setError("");
    try {
      await requestJson(`/api/invoices/${encodeURIComponent(item.id)}/collection-review`, {
        method: "PATCH", body: JSON.stringify({ ...draft, follow_up_date: draft.follow_up_date || null }),
      });
      setCollectionStates(current => ({ ...current, [item.id]: "saved" }));
      clearReads();
      const latest = await refresh();
      if (!latest) setCollectionStates(current => ({ ...current, [item.id]: "saved_refresh_failed" }));
    } catch (cause) {
      clearReads();
      setCollectionStates(current => ({ ...current, [item.id]: "uncertain" }));
      const latest = await refresh();
      if (!latest) {
        setError("Could not confirm the collections save. Your edits remain on this page. Check status before retrying.");
      } else {
        const currentItem = latest.collections_review?.find(row => row.id === item.id);
        if (matchesCollectionDraft(currentItem, draft)) {
          setCollectionStates(current => ({ ...current, [item.id]: "saved" }));
          setError("");
        } else {
          setCollectionStates(current => ({ ...current, [item.id]: "failed" }));
          setError(`Collections save failed. Your edits remain on this page. ${cause instanceof Error ? cause.message : "Retry this record."}`);
        }
      }
    }
    finally { setBusyId(""); }
  };

  const checkActionStatus = async (item: QueueItem) => {
    const recommendation = item.recommendation;
    if (!recommendation) return;
    clearReads();
    try {
      const latest = await requestJson<{ recommendation: { state: string } | null; attempts?: { state: string }[] }>(
        `/api/invoices/${encodeURIComponent(item.id)}`,
      );
      const state = latest.recommendation?.state || latest.attempts?.[0]?.state || "not available";
      if (["proposed", "failed"].includes(state)) {
        setOperationStates(current => { const next = { ...current }; delete next[recommendation.id]; return next; });
        setError(`Status check confirms ${item.invoice_number} is still ${state}. You can review the details and retry the action.`);
      } else {
        setOperationStates(current => ({ ...current, [recommendation.id]: "confirmed" }));
        setError(`Status check confirms ${item.invoice_number} is ${state}. The action controls are locked while the queue refreshes.`);
      }
      const refreshed = await refresh();
      if (!refreshed) setOperationStates(current => ({ ...current, [recommendation.id]: "saved_refresh_failed" }));
    } catch (cause) {
      setError(`Could not confirm the status for ${item.invoice_number}. Retry Check status before submitting the action again. ${cause instanceof Error ? cause.message : ""}`);
    }
  };

  const editDraft = (id: string, text: string) => {
    setDrafts(current => ({ ...current, [id]: text }));
    sessionStorage.setItem(`latepayer-draft:${id}`, text);
    clearTimeout(saveTimers.current.get(id));
    saveTimers.current.set(id, setTimeout(() => { if (text.trim()) void requestJson(`/api/recommendations/${id}/draft`, { method: "POST", body: JSON.stringify({ text }) }).catch(cause => setError(cause.message)); }, 500));
  };
  const act = async (item: QueueItem, operation: "approve" | "skip", confirmed = false) => {
    const recommendation = item.recommendation;
    if (!recommendation || operationStates[recommendation.id]) return;
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
      await requestJson<{ state?: string }>(path, {
        method: "POST",
        ...(body ? { body: JSON.stringify({ text: body }) } : {}),
      });
      const reviewSeconds = reviewDurationSeconds(startedAt);
      reviewStartedAt.current.delete(recommendation.id);
      void requestJson(`/api/invoices/${encodeURIComponent(item.id)}/review-metric`, {
        method: "POST", body: JSON.stringify({ review_seconds: reviewSeconds, operation }),
      }).catch(() => undefined);
      clearReads();
      const refreshed = await refresh();
      if (!refreshed) {
        setOperationStates(current => ({ ...current, [recommendation.id]: "saved_refresh_failed" }));
        setError(`${item.invoice_number} action saved, but the queue refresh failed. Check invoice status before continuing.`);
      }
      requestAnimationFrame(() => (document.querySelector(".queue-card button:not(:disabled), [data-queue-refresh]") as HTMLButtonElement | null)?.focus());
      return true;
    } catch (cause) {
      if (!(cause instanceof RequestError) || cause.status >= 500) {
        setOperationStates(current => ({ ...current, [recommendation.id]: "uncertain" }));
        setError(`${item.invoice_number} action did not return a confirmed result. Check its status before retrying.`);
      } else {
        setError(cause instanceof Error ? cause.message : `Could not ${operation} this suggestion.`);
      }
      return false;
    }
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
      const operationState = operationStates[recommendation.id];
      const partial = recommendation.action === "offer_partial_payment";
      const synthetic = item.source.startsWith("demo");
      return <article className="queue-card" key={recommendation.id}
        onPointerDownCapture={() => { if (!reviewStartedAt.current.has(recommendation.id)) reviewStartedAt.current.set(recommendation.id, reviewClockNow()); }}
        onFocusCapture={() => { if (!reviewStartedAt.current.has(recommendation.id)) reviewStartedAt.current.set(recommendation.id, reviewClockNow()); }}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className="recommendation-tag">{decisionLabel(recommendation.action)}</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</strong></span><span>Assumed 14-day uplift <strong>{money(item.currency, item.decision.expected_cash_gain)}</strong></span></div>
        <p className="panel-subtitle">{item.why || item.decision.reason}</p>
        {item.reminder_outcomes?.length ? <div className="fine-print">Next-week outcome: {item.reminder_outcomes.map(outcome => `${outcome.tone} · ${outcome.status === "checking" ? `checking through ${outcome.window_ends}` : outcome.status}${outcome.amount_paid !== "0.00" ? ` (${money(outcome.currency, outcome.amount_paid)})` : ""}`).join(" · ")}</div> : null}
        {partial ? <div className="delivery-note">{synthetic ? "Approval simulates enabling partial payments in the synthetic demo; no PayPal change or email occurs." : "Approval enables partial payments on this PayPal Sandbox invoice; PayPal may notify the recipient."}</div> : <label className="field-label">{synthetic ? "Simulated reminder note · no email is sent" : "Internal reminder note · recipient receives PayPal&apos;s native message"}<textarea value={drafts[recommendation.id] || ""} onChange={event => editDraft(recommendation.id, event.target.value)}/></label>}
        {operationState === "uncertain" && <div className="delivery-note" role="status"><strong>Action status needs reconciliation.</strong><p>The saved server state is not confirmed. Check it before retrying.</p><button className="btn" disabled={busy} onClick={() => void checkActionStatus(item)}>Check status</button></div>}
        {operationState === "saved_refresh_failed" && <div className="delivery-note" role="status"><strong>Action saved; queue refresh failed.</strong><p>Check the invoice record or retry the queue refresh before continuing.</p><div className="action-buttons"><button className="btn" disabled={busy} onClick={() => void checkActionStatus(item)}>Check status</button><button className="btn" disabled={loading} onClick={() => void refresh()}>Retry refresh</button></div></div>}
        {operationState === "confirmed" && <div className="fine-print" role="status">Server status checked. Refresh the queue before taking another action.</div>}
        <div className="action-buttons"><button className="btn primary" disabled={busy || bulkBusy || Boolean(operationState) || (!partial && !(drafts[recommendation.id] || "").trim())} onClick={() => void act(item, "approve")}>{busy ? "Applying…" : partial ? synthetic ? "Approve & simulate" : "Approve partial payments" : synthetic ? "Approve & simulate" : "Approve & send"}</button><button className="btn" disabled={busy || bulkBusy || Boolean(operationState)} onClick={() => void act(item, "skip")}>Skip</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
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
            {collectionStates[item.id] === "dirty" && <div className="fine-print" role="status">Unsaved changes · use Save collections review to persist this record.</div>}
            {collectionStates[item.id] === "saving" && <div className="fine-print" role="status">Saving collections review…</div>}
            {collectionStates[item.id] === "saved" && <div className="fine-print" role="status">Saved to the server.</div>}
            {collectionStates[item.id] === "saved_refresh_failed" && <div className="delivery-note" role="status"><strong>Saved, but the queue refresh failed.</strong><p>Your changes are retained on this page. Check status to reconcile the record, or retry the refresh.</p><div className="action-buttons"><button className="btn" onClick={() => void checkCollectionStatus(item)}>Check status</button><button className="btn" onClick={() => void refresh()}>Retry refresh</button></div></div>}
            {collectionStates[item.id] === "uncertain" && <div className="delivery-note" role="status"><strong>Save outcome not confirmed.</strong><p>Your changes are retained. Check the server before retrying.</p><button className="btn" onClick={() => void checkCollectionStatus(item)}>Check status</button></div>}
            {collectionStates[item.id] === "failed" && <div className="delivery-note" role="alert"><strong>Save failed.</strong><p>Your changes are retained and can be saved again.</p></div>}
            <div className="action-buttons"><button className="btn primary" disabled={busyId === item.id} onClick={() => void saveCollectionReview(item)}>{busyId === item.id ? "Saving…" : collectionStates[item.id] === "failed" ? "Retry save" : "Save collections review"}</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
          </div>;
        })()}
      </article>)}</div>
    </section> : null}
  </>;
}

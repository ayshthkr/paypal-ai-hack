"use client";

import Link from "next/link";
import { requestJson, clearReads } from "../lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

type QueueItem = {
  id: string; invoice_number: string; client_name: string; client_id: string; source: string; currency: string; outstanding: string;
  due_date: string; decision: { action: string; reason: string; expected_cash_gain: number };
  recommendation: { id: string; action: string; tone: string; edited_text: string; reason: string; state: string } | null;
};
type QueueResult = { items: QueueItem[]; count: number; daily_cap: number; as_of: string; synthetic_demo: boolean; pending_count: number; skipped_count: number; skipped: { id: string; invoice_id: string }[] };

function money(currency: string, value: number | string) { return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }

export default function ActionQueue() {
  const [data, setData] = useState<QueueResult | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const saveTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const result = await requestJson<QueueResult>("/api/action-queue");
      setData(result);
      setDrafts(Object.fromEntries(result.items.map(item => [item.recommendation?.id || item.id, sessionStorage.getItem(`latepayer-draft:${item.recommendation?.id}`) ?? item.recommendation?.edited_text ?? ""])));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load today's action queue."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void refresh(), 0); return () => window.clearTimeout(timer); }, [refresh]);

  const editDraft = (id: string, text: string) => {
    setDrafts(current => ({ ...current, [id]: text }));
    sessionStorage.setItem(`latepayer-draft:${id}`, text);
    clearTimeout(saveTimers.current.get(id));
    saveTimers.current.set(id, setTimeout(() => { if (text.trim()) void requestJson(`/api/recommendations/${id}/draft`, { method: "POST", body: JSON.stringify({ text }) }).catch(cause => setError(cause.message)); }, 500));
  };
  const act = async (item: QueueItem, operation: "approve" | "skip", confirmed = false) => {
    const recommendation = item.recommendation;
    if (!recommendation) return;
    clearTimeout(saveTimers.current.get(recommendation.id));
    const isPartial = recommendation.action === "offer_partial_payment";
    if (operation === "approve" && !confirmed && item.source === "paypal" && !window.confirm(isPartial ? "Enable partial payments on this PayPal Sandbox invoice?" : "Send this reminder through PayPal Sandbox?")) return;
    setBusyId(recommendation.id); setError("");
    try {
      let path: string;
      let body: string | undefined;
      if (operation === "skip") path = `/api/recommendations/${recommendation.id}/skip`;
      else if (item.source === "demo") { path = `/api/demo/recommendations/${recommendation.id}/send`; body = drafts[recommendation.id] || recommendation.edited_text; }
      else if (isPartial) path = `/api/recommendations/${recommendation.id}/approve-partial-payment`;
      else { path = `/api/recommendations/${recommendation.id}/send`; body = drafts[recommendation.id] || recommendation.edited_text; }
      const response = await fetch(path, { method: "POST", cache: "no-store", credentials: "same-origin", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify({ text: body }) : undefined });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      clearReads();
      await refresh();
      requestAnimationFrame(() => (document.querySelector(".queue-card button:not(:disabled), [data-queue-refresh]") as HTMLButtonElement | null)?.focus());
      return true;
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not ${operation} this suggestion.`); return false; }
    finally { setBusyId(""); }
  };

  const approveFriendly = async () => {
    const items = data?.items.filter(item => item.recommendation?.action === "friendly") || [];
    if (!items.length || !window.confirm(`Approve all ${items.length} friendly reminders? Synthetic reminders are simulated; PayPal Sandbox reminders send test notifications.`)) return;
    setBulkBusy(true);
    try { for (const item of items) { if (!await act(item, "approve", true)) break; } }
    finally { setBulkBusy(false); }
  };
  return <>
    <div className="heading-row"><div><div className="eyebrow">Daily workflow</div><h1>Action queue</h1><p className="subheading">Today&apos;s suggested actions, ranked by expected cash gained. Review each draft, then approve or skip.</p></div><button data-queue-refresh className="btn" disabled={loading || Boolean(busyId)} onClick={() => void refresh()}>↻ Refresh queue</button></div>
    {data && <div className="fine-print" style={{ marginBottom: 12 }}>As of {data.as_of} · {data.pending_count} pending · {data.skipped_count} skipped · cap {data.daily_cap} · {data.synthetic_demo ? "Synthetic actions are simulated" : "PayPal actions use Sandbox"}</div>}
    {data && <div className="action-buttons"><button className="btn primary" disabled={bulkBusy || Boolean(busyId) || !data.items.some(item => item.recommendation?.action === "friendly")} onClick={() => void approveFriendly()}>Approve all friendly</button>{data.skipped.map(item => <button key={item.id} className="btn" disabled={Boolean(busyId) || bulkBusy} onClick={() => { void requestJson(`/api/recommendations/${item.id}/undo-skip`, { method: "POST" }).then(() => refresh()).catch(cause => setError(cause.message)); }}>Undo skip · {item.invoice_id}</button>)}</div>}
    {error && <div className="error-box" role="alert">{error}</div>}
    {loading && !data ? <div className="empty-box">Loading today&apos;s suggested actions…</div> : error && !data ? <div className="empty-box">The action queue could not load. Check the service connection and refresh to try again.</div> : !data?.items.length ? <div className="empty-box">No reminder or partial-payment action is pending today. The policy may recommend waiting, or all suggestions may already be reviewed.</div> : <div className="queue-list">{data.items.map(item => {
      const recommendation = item.recommendation;
      if (!recommendation) return null;
      const busy = busyId === recommendation.id;
      const partial = recommendation.action === "offer_partial_payment";
      return <article className="queue-card" key={recommendation.id}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className="recommendation-tag">{recommendation.action.replaceAll("_", " ")}</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</strong></span><span>Expected cash gain <strong>{money(item.currency, item.decision.expected_cash_gain)}</strong></span></div>
        <p className="panel-subtitle">{item.decision.reason}</p>
        {partial ? <div className="delivery-note">Approval will enable partial payments on this invoice; it does not send a reminder email.</div> : <label className="field-label">Reminder draft<textarea value={drafts[recommendation.id] || ""} onChange={event => editDraft(recommendation.id, event.target.value)}/></label>}
        <div className="action-buttons"><button className="btn primary" disabled={busy || bulkBusy || (!partial && !(drafts[recommendation.id] || "").trim())} onClick={() => void act(item, "approve")}>{busy ? "Applying…" : partial ? "Approve partial payments" : "Approve & send"}</button><button className="btn" disabled={busy || bulkBusy} onClick={() => void act(item, "skip")}>Skip</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
      </article>;
    })}</div>}
  </>;
}

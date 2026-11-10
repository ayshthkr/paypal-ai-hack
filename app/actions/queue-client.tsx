"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type QueueItem = {
  id: string; invoice_number: string; client_name: string; client_id: string; source: string; currency: string; outstanding: string;
  due_date: string; decision: { action: string; reason: string; expected_cash_gain: number };
  recommendation: { id: string; action: string; tone: string; edited_text: string; reason: string; state: string } | null;
};
type QueueResult = { items: QueueItem[]; count: number; daily_cap: number; as_of: string; synthetic_demo: boolean };

function money(currency: string, value: number | string) { return `${currency} ${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }

export default function ActionQueue() {
  const [data, setData] = useState<QueueResult | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/action-queue", { cache: "no-store", credentials: "same-origin" });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      const result = value as QueueResult;
      setData(result);
      setDrafts(Object.fromEntries(result.items.map(item => [item.recommendation?.id || item.id, item.recommendation?.edited_text || ""])));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load today's action queue."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const act = async (item: QueueItem, operation: "approve" | "skip") => {
    const recommendation = item.recommendation;
    if (!recommendation) return;
    const isPartial = recommendation.action === "offer_partial_payment";
    if (operation === "approve" && item.source === "paypal" && !window.confirm(isPartial ? "Enable partial payments on this PayPal Sandbox invoice?" : "Send this reminder through PayPal Sandbox?")) return;
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
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not ${operation} this suggestion.`); }
    finally { setBusyId(""); }
  };

  return <>
    <div className="heading-row"><div><div className="eyebrow">Daily workflow</div><h1>Action queue</h1><p className="subheading">Today&apos;s suggested actions, ranked by expected cash gained. Review each draft, then approve or skip.</p></div><button className="btn" disabled={loading || Boolean(busyId)} onClick={() => void refresh()}>↻ Refresh queue</button></div>
    {data && <div className="fine-print" style={{ marginBottom: 12 }}>As of {data.as_of} · {data.count} of {data.daily_cap} daily suggestions · {data.synthetic_demo ? "Synthetic actions are simulated" : "PayPal actions use Sandbox"}</div>}
    {error && <div className="error-box" role="alert">{error}</div>}
    {loading && !data ? <div className="empty-box">Loading today&apos;s suggested actions…</div> : error && !data ? <div className="empty-box">The action queue could not load. Check the service connection and refresh to try again.</div> : !data?.items.length ? <div className="empty-box">No reminder or partial-payment action is pending today. The policy may recommend waiting, or all suggestions may already be reviewed.</div> : <div className="queue-list">{data.items.map(item => {
      const recommendation = item.recommendation;
      if (!recommendation) return null;
      const busy = busyId === recommendation.id;
      const partial = recommendation.action === "offer_partial_payment";
      return <article className="queue-card" key={recommendation.id}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className="recommendation-tag">{recommendation.action.replaceAll("_", " ")}</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</strong></span><span>Expected cash gain <strong>{money(item.currency, item.decision.expected_cash_gain)}</strong></span></div>
        <p className="panel-subtitle">{item.decision.reason}</p>
        {partial ? <div className="delivery-note">Approval will enable partial payments on this invoice; it does not send a reminder email.</div> : <label className="field-label">Reminder draft<textarea value={drafts[recommendation.id] || ""} onChange={event => setDrafts(current => ({ ...current, [recommendation.id]: event.target.value }))}/></label>}
        <div className="action-buttons"><button className="btn primary" disabled={busy || (!partial && !(drafts[recommendation.id] || "").trim())} onClick={() => void act(item, "approve")}>{busy ? "Applying…" : partial ? "Approve partial payments" : "Approve & send"}</button><button className="btn" disabled={busy} onClick={() => void act(item, "skip")}>Skip</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
      </article>;
    })}</div>}
  </>;
}

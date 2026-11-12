"use client";

import Link from "next/link";
import { requestJson, clearReads, ensureDemoSession, RequestError, currentReadTimeoutMs } from "../lib/api";
import { decisionLabel } from "../lib/labels";
import { useCallback, useEffect, useRef, useState } from "react";
import ConfirmDialog from "../confirm-dialog";

type QueueItem = {
  id: string; invoice_number: string; client_name: string; client_id: string; client_email?: string | null; source: string; currency: string; outstanding: string;
  due_date: string; days_late?: number; decision: { action: string; reason: string; expected_cash_gain: number };
  why?: string;
  reminder_outcomes?: { sent_at: string; tone: string; status: "paid" | "partial" | "silent" | "checking"; amount_paid: string; currency: string; window_ends: string }[];
  suggested_review_steps?: string[];
  collection_review?: { owner: string; notes: string; follow_up_date: string | null; resolution_status: string; updated_at?: string | null };
  recommendation: { id: string; action: string; tone: string; edited_text: string; draft_text?: string; draft_source?: string; draft_model?: string | null; drafted_at?: string; approved_text?: string | null; approved_at?: string | null; reason: string; state: string } | null;
};
export type QueueResult = { items: QueueItem[]; count: number; daily_cap: number; as_of: string; synthetic_demo: boolean; pending_count: number; skipped_count: number; skipped: { id: string; invoice_id: string; invoice_number: string; client_name: string; replacement_state?: string | null; replacement_id?: string | null }[]; collections_review?: QueueItem[]; collections_closed?: QueueItem[]; collections_totals?: Record<string, string>; review_time?: { completed_count: number; median_seconds: number | null; sample_window: string } };
type BulkApprovalPreview = { eligibleCount: number; cap: number; items: QueueItem[] };

function money(currency: string, value: number | string) { return `${currency} ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
function reviewClockNow() { return performance.now(); }
function reviewDurationSeconds(startedAt: number) { return Math.max(1, Math.ceil((reviewClockNow() - startedAt) / 1000)); }
function readDrafts(items: QueueItem[]) {
  return Object.fromEntries(items.map(item => [item.recommendation?.id || item.id,
    sessionStorage.getItem(`latepayer-draft:${item.recommendation?.id}`) ?? item.recommendation?.edited_text ?? ""]));
}

export default function ActionQueue({ collectionsOnly = false }: { collectionsOnly?: boolean }) {
  const [data, setData] = useState<QueueResult | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const saveTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const reviewStartedAt = useRef(new Map<string, number>());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkPreview, setBulkPreview] = useState<BulkApprovalPreview | null>(null);
  const [providerApproval, setProviderApproval] = useState<QueueItem | null>(null);
  const [error, setError] = useState("");
  const [waking, setWaking] = useState(false);
  const [collectionDrafts, setCollectionDrafts] = useState<Record<string, { owner: string; notes: string; follow_up_date: string; resolution_status: string }>>({});
  const [collectionStates, setCollectionStates] = useState<Record<string, "dirty" | "saving" | "saved" | "saved_refresh_failed" | "failed" | "uncertain">>({});
  const [operationStates, setOperationStates] = useState<Record<string, "uncertain" | "saved_refresh_failed" | "confirmed">>({});
  const [draftSaveStates, setDraftSaveStates] = useState<Record<string, "saving" | "saved" | "failed">>({});
  const [bulkResults, setBulkResults] = useState<string[]>([]);
  const [skipRecovery, setSkipRecovery] = useState<Record<string, string>>({});
  const [skipBusy, setSkipBusy] = useState("");
  const [collectionTab, setCollectionTab] = useState<"open" | "closed" | "all">("open");
  const [collectionSearch, setCollectionSearch] = useState("");
  const [collectionOwner, setCollectionOwner] = useState("");
  const [collectionStatus, setCollectionStatus] = useState("");
  const [followUpFrom, setFollowUpFrom] = useState("");
  const [followUpTo, setFollowUpTo] = useState("");
  const [unassignedOnly, setUnassignedOnly] = useState(false);
  const [overdueFollowupsOnly, setOverdueFollowupsOnly] = useState(false);
  const collectionStatesRef = useRef(collectionStates);
  collectionStatesRef.current = collectionStates;
  const refresh = useCallback(async (): Promise<QueueResult | null> => {
    setLoading(true); setError(""); setWaking(false);
    const wakeMessage = window.setTimeout(() => setWaking(true), 900);
    try {
      await ensureDemoSession();
      const deadline = Date.now() + currentReadTimeoutMs();
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
      if (!result) throw new Error(`Could not refresh the ${collectionsOnly ? "collections" : "action queue"} after repeated service checks. ${lastError ? "The last read did not complete." : "Choose Retry or the read-only fallback."}`);
      setData(result);
      setDrafts(readDrafts(result.items));
      const allCollectionRecords = [...(result.collections_review || []), ...(result.collections_closed || [])];
      const serverDrafts = Object.fromEntries(allCollectionRecords.map(item => [item.id, {
        owner: item.collection_review?.owner || "", notes: item.collection_review?.notes || "",
        follow_up_date: item.collection_review?.follow_up_date || "",
        resolution_status: item.collection_review?.resolution_status || "open",
      }]));
      setCollectionDrafts(current => Object.fromEntries(Object.entries(serverDrafts).map(([id, value]) => {
        let stored: typeof value | null = null;
        try { stored = JSON.parse(sessionStorage.getItem(`latepayer-collection:${id}`) || "null") as typeof value | null; } catch { stored = null; }
        return [id, ["dirty", "saving", "saved_refresh_failed", "failed", "uncertain"].includes(collectionStatesRef.current[id]) ? current[id] || stored || value : stored || value];
      })));
      return result;
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load the action queue."); return null; }
    finally { window.clearTimeout(wakeMessage); setWaking(false); setLoading(false); }
  }, [collectionsOnly]);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const updateCollection = (id: string, key: "owner" | "notes" | "follow_up_date" | "resolution_status", value: string) => {
    setCollectionDrafts(current => {
      const next = { ...{ owner: "", notes: "", follow_up_date: "", resolution_status: "open" }, ...current[id], [key]: value };
      sessionStorage.setItem(`latepayer-collection:${id}`, JSON.stringify(next));
      return { ...current, [id]: next };
    });
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
    const currentItem = [...(latest.collections_review || []), ...(latest.collections_closed || [])].find(row => row.id === item.id);
    if (matchesCollectionDraft(currentItem, draft)) { sessionStorage.removeItem(`latepayer-collection:${item.id}`); setCollectionStates(current => ({ ...current, [item.id]: "saved" })); }
    else setCollectionStates(current => ({ ...current, [item.id]: "failed" }));
  };

  const saveCollectionReview = async (item: QueueItem, override?: { owner: string; notes: string; follow_up_date: string; resolution_status: string }) => {
    const draft = override || collectionDrafts[item.id] || { owner: "", notes: "", follow_up_date: "", resolution_status: "open" };
    setCollectionStates(current => ({ ...current, [item.id]: "saving" }));
    setBusyId(item.id); setError("");
    try {
      await requestJson(`/api/invoices/${encodeURIComponent(item.id)}/collection-review`, {
        method: "PATCH", body: JSON.stringify({ ...draft, follow_up_date: draft.follow_up_date || null }),
      });
      setCollectionStates(current => ({ ...current, [item.id]: "saved" }));
      sessionStorage.removeItem(`latepayer-collection:${item.id}`);
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
        const currentItem = [...(latest.collections_review || []), ...(latest.collections_closed || [])].find(row => row.id === item.id);
        if (matchesCollectionDraft(currentItem, draft)) {
          sessionStorage.removeItem(`latepayer-collection:${item.id}`);
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

  const reconcileSkipped = async (item: QueueResult["skipped"][number], checkOnly = false) => {
    setSkipBusy(item.id); setSkipRecovery(current => ({ ...current, [item.id]: "Checking the current invoice action…" }));
    clearReads();
    try {
      if (!checkOnly) await requestJson(`/api/recommendations/${encodeURIComponent(item.id)}/undo-skip`, { method: "POST" });
      clearReads();
      const current = await requestJson<{ recommendation: { id: string; state: string; action: string } | null }>(`/api/invoices/${encodeURIComponent(item.invoice_id)}`);
      clearReads();
      const recommendation = current.recommendation;
      if (recommendation?.id === item.id && recommendation.state === "proposed") {
        setSkipRecovery(value => ({ ...value, [item.id]: "The skip is restored and the same recommendation is pending again." }));
      } else if (recommendation?.id !== item.id && recommendation) {
        setSkipRecovery(value => ({ ...value, [item.id]: `Skip remains recorded. Current invoice action is ${recommendation.action} · ${recommendation.state}; review that record instead of restoring the old suggestion.` }));
      } else if (recommendation?.state === "skipped") {
        setSkipRecovery(value => ({ ...value, [item.id]: "Status check confirms this recommendation is still skipped. You can retry restoring it." }));
      } else {
        setSkipRecovery(value => ({ ...value, [item.id]: `Current invoice state: ${recommendation?.state || "no active recommendation"}. Review the invoice timeline before continuing.` }));
      }
      await refresh();
    } catch (cause) {
      clearReads();
      try {
        const current = await requestJson<{ recommendation: { id: string; state: string; action: string } | null }>(`/api/invoices/${encodeURIComponent(item.invoice_id)}`);
        const recommendation = current.recommendation;
        if (recommendation?.id === item.id && recommendation.state === "proposed") {
          setSkipRecovery(value => ({ ...value, [item.id]: "Status check confirms the skip was restored; the response was lost. The same action is pending." }));
        } else if (recommendation?.id !== item.id && recommendation) {
          setSkipRecovery(value => ({ ...value, [item.id]: `A ${recommendation.state} replacement exists. The old skip remains in history; review the current invoice action.` }));
        } else {
          setSkipRecovery(value => ({ ...value, [item.id]: `Restore was not confirmed. Status check shows ${recommendation?.state || "no active recommendation"}; retry Check status before restoring again.` }));
        }
        await refresh();
      } catch {
        setSkipRecovery(value => ({ ...value, [item.id]: `Restore result is uncertain. Check status before retrying. ${cause instanceof Error ? cause.message : ""}` }));
      }
    } finally { setSkipBusy(""); }
  };

  const editDraft = (id: string, text: string) => {
    setDrafts(current => ({ ...current, [id]: text }));
    sessionStorage.setItem(`latepayer-draft:${id}`, text);
    setDraftSaveStates(current => ({ ...current, [id]: "saving" }));
    clearTimeout(saveTimers.current.get(id));
    saveTimers.current.set(id, setTimeout(() => {
      if (!text.trim()) return;
      void requestJson(`/api/recommendations/${id}/draft`, { method: "POST", body: JSON.stringify({ text }) })
        .then(() => setDraftSaveStates(current => ({ ...current, [id]: "saved" })))
        .catch(cause => { setDraftSaveStates(current => ({ ...current, [id]: "failed" })); setError(`Draft save failed. The text remains in this session and can be retried. ${cause.message}`); });
    }, 500));
  };
  const act = async (item: QueueItem, operation: "approve" | "skip") => {
    const recommendation = item.recommendation;
    if (!recommendation || operationStates[recommendation.id]) return;
    const startedAt = reviewStartedAt.current.get(recommendation.id) ?? reviewClockNow();
    clearTimeout(saveTimers.current.get(recommendation.id));
    const isPartial = recommendation.action === "offer_partial_payment";
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

  const openFriendlyPreview = () => {
    const cap = Math.min(5, data?.daily_cap ?? 5);
    const items = (data?.items.filter(item => item.recommendation?.action === "friendly") || []).slice(0, cap);
    if (!items.length) return;
    const eligibleCount = data?.items.filter(item => item.recommendation?.action === "friendly").length || 0;
    setBulkPreview({ eligibleCount, cap, items });
  };
  const approveFriendly = async (items: QueueItem[]) => {
    setBulkBusy(true);
    setBulkResults([]);
    try {
      const outcomes: string[] = [];
      for (const item of items) {
        const ok = await act(item, "approve");
        outcomes.push(`${item.invoice_number}: ${ok ? "success" : "failed or needs status check"}`);
        setBulkResults([...outcomes]);
        if (!ok) break;
      }
    }
    finally { setBulkBusy(false); }
  };
  const allCollectionRecords = [...(data?.collections_review || []), ...(data?.collections_closed || [])];
  const collectionOwners = Array.from(new Set(allCollectionRecords.map(item => item.collection_review?.owner.trim() || "").filter(Boolean))).sort((a, b) => a.localeCompare(b));
  const collectionRecords = allCollectionRecords.filter(item => {
    const review = item.collection_review;
    const closed = ["resolved", "written_off"].includes(review?.resolution_status || "");
    if (collectionTab === "open" && closed || collectionTab === "closed" && !closed) return false;
    if (collectionSearch && !`${item.invoice_number} ${item.client_name} ${item.client_id}`.toLowerCase().includes(collectionSearch.toLowerCase())) return false;
    if (collectionOwner && (review?.owner || "") !== collectionOwner) return false;
    if (collectionStatus && review?.resolution_status !== collectionStatus) return false;
    const followUp = review?.follow_up_date || "";
    if (followUpFrom && (!followUp || followUp < followUpFrom)) return false;
    if (followUpTo && (!followUp || followUp > followUpTo)) return false;
    if (overdueFollowupsOnly && (!followUp || followUp >= (data?.as_of || "" ) || closed)) return false;
    return true;
  });
  const openCollectionCount = data?.collections_review?.length || 0;
  const archivedCollectionCount = data?.collections_closed?.length || 0;
  const unassignedCollectionCount = allCollectionRecords.filter(item => !(item.collection_review?.owner || "").trim() && !["resolved", "written_off"].includes(item.collection_review?.resolution_status || "")).length;
  const overdueCollectionFollowUpCount = allCollectionRecords.filter(item => item.collection_review?.follow_up_date && item.collection_review.follow_up_date < (data?.as_of || "") && !["resolved", "written_off"].includes(item.collection_review?.resolution_status || "")).length;
  const eligibleFriendlyCount = data?.items.filter(item => item.recommendation?.action === "friendly").length || 0;
  return <>
    <div className="heading-row"><div><div className="eyebrow">{collectionsOnly ? "Collections workflow" : "Daily workflow"}</div><h1>{collectionsOnly ? "Collections" : "Action queue"}</h1><p className="subheading">{collectionsOnly ? "Assign owners, set follow-ups, document resolution, and reopen a case when needed." : "Review suggested reminders and partial-payment changes. The five-action cap is shared across currencies."}</p></div><button data-queue-refresh className="btn" disabled={loading || Boolean(busyId)} onClick={() => void refresh()}>↻ {collectionsOnly ? "Refresh collections" : "Refresh queue"}</button></div>
    {data && <div className="fine-print" style={{ marginBottom: 12 }}>Snapshot {data.as_of} · {collectionsOnly ? `${data.collections_review?.length || 0} open collections cases · ${data.collections_closed?.length || 0} archived cases` : `${data.pending_count} pending · ${data.skipped_count} skipped · cap ${data.daily_cap}`} · {data.synthetic_demo ? "Synthetic actions are simulated" : "PayPal actions use Sandbox"}</div>}
    {data && !collectionsOnly && <section className="stats-grid queue-overview" aria-label="Action queue summary">
      <div className="stat-card"><div className="stat-label">Ready for review</div><div className="stat-value">{data.items.length}</div><div className="stat-note">ranked suggestions · snapshot {data.as_of}</div></div>
      <div className="stat-card"><div className="stat-label">Daily approval cap</div><div className="stat-value">{data.daily_cap}</div><div className="stat-note">shared across currency lanes</div></div>
      <div className="stat-card"><div className="stat-label">Skipped records</div><div className="stat-value">{data.skipped_count}</div><div className="stat-note">original decisions remain in history</div></div>
      <div className="stat-card"><div className="stat-label">Median review time</div><div className="stat-value">{data.review_time?.median_seconds == null ? "—" : `${data.review_time.median_seconds}s`}</div><div className="stat-note">{data.review_time?.completed_count || 0} completed actions · includes service time</div></div>
    </section>}
    {data && collectionsOnly && <section className="stats-grid queue-overview collections-overview" aria-label="Collections summary">
      <div className="stat-card"><div className="stat-label">Open cases</div><div className="stat-value">{openCollectionCount}</div><div className="stat-note">90+ days overdue · all owners</div></div>
      <div className="stat-card"><div className="stat-label">Unassigned</div><div className="stat-value">{unassignedCollectionCount}</div><div className="stat-note">open cases without an owner</div></div>
      <div className="stat-card"><div className="stat-label">Overdue follow-ups</div><div className="stat-value">{overdueCollectionFollowUpCount}</div><div className="stat-note">past the current snapshot date</div></div>
      <div className="stat-card"><div className="stat-label">Closed archive</div><div className="stat-value">{archivedCollectionCount}</div><div className="stat-note">resolved or written off in workflow</div></div>
    </section>}
    {loading && data && waking && <div className="empty-inline" role="status">Waking the API… refreshing the snapshot-date queue. The first request can take up to 30 seconds.</div>}
    {!collectionsOnly && data?.items.length ? <details className="panel policy-thresholds"><summary>How recommendations are ranked</summary><div className="queue-methodology"><p>Measured review time starts at first interaction and includes service delay; no manual baseline is collected. The 14-day cash uplift is a 10% scenario, not measured incremental cash or an observed effect.</p><p id="recorded-outcomes">Recorded payments after reminders are descriptive outcomes and do not establish causality. Each outcome links to the underlying invoice timeline.</p></div><ul><li>Due within three days: a friendly reminder is eligible when predicted payment by the due date is below the configured probability threshold.</li><li>At least 3 days overdue: friendly follow-up when the 14-day payment probability is below 80%; 5-day reminder cooldown.</li><li>At least 10 days overdue: firm follow-up when 14-day payment probability is below 95%; 7-day cooldown.</li><li>At +21 days: offer partial payment only for balances of at least 2,500 with prior partial-payment history; at +45 days, recommend a personal call.</li><li>At +90 days: move the open balance to collections review instead of sending another reminder.</li><li>The shared daily cap is allocated round-robin across currency lanes in alphabetical order (EUR, GBP, USD); within each lane rank by assumed uplift, then due date, then stable invoice ID. No FX conversion is used for ranking.</li></ul></details> : null}
    {!collectionsOnly && data && <><div className="action-buttons bulk-approval-actions"><button className="btn primary" disabled={bulkBusy || Boolean(busyId) || eligibleFriendlyCount === 0} title={eligibleFriendlyCount === 0 ? "No friendly reminder meets the current policy criteria in this queue snapshot." : undefined} onClick={openFriendlyPreview}>{eligibleFriendlyCount ? `Approve ${eligibleFriendlyCount} eligible friendly reminders` : "No eligible friendly reminders"}</button>{data.skipped.map(item => <div className="skip-recovery" key={item.id}><span>{item.invoice_number} · {item.client_name}</span><Link className="btn" href={`/invoices/${encodeURIComponent(item.invoice_id)}#invoice-timeline`}>Open skipped record</Link>{item.replacement_state ? <span className="fine-print">A replacement action is already {item.replacement_state}; the original skip remains recorded.</span> : <button className="btn" disabled={Boolean(busyId) || bulkBusy || skipBusy === item.id} onClick={() => void reconcileSkipped(item)}>{skipBusy === item.id ? "Checking…" : "Restore skipped suggestion"}</button>}{skipRecovery[item.id] && <span className="fine-print" role="status">{skipRecovery[item.id]} <button className="btn" disabled={skipBusy === item.id} onClick={() => void reconcileSkipped(item, true)}>Check status</button></span>}</div>)}</div>{bulkResults.length > 0 && <div className="delivery-note" role="status"><strong>Bulk approval results</strong>{bulkResults.map(result => <div key={result}>{result}</div>)}</div>}</>}
    {error && <div className="error-box" role="alert">{error}</div>}
    {collectionsOnly && loading && !data ? <div className="empty-box" role="status">{waking ? "Waking the API… the first request can take up to 30 seconds." : "Loading collections…"}</div> : !collectionsOnly && loading && !data ? <div className="empty-box" role="status">{waking ? "Waking the API… the first request can take up to 30 seconds." : "Loading snapshot-date suggestions…"}</div> : error && !data ? <div className="empty-box">The {collectionsOnly ? "collections" : "action queue"} could not load. {error} <button className="btn" onClick={() => void refresh()}>Retry</button></div> : collectionsOnly ? null : !data?.items.length ? <div className="empty-box">No reminder or partial-payment action is pending for this snapshot. The policy may recommend waiting, or all suggestions may already be reviewed.</div> : <div className="queue-list" id="action-approval">{data.items.map(item => {
      const recommendation = item.recommendation;
      if (!recommendation) return null;
      const busy = busyId === recommendation.id;
      const operationState = operationStates[recommendation.id];
      const partial = recommendation.action === "offer_partial_payment";
      const synthetic = item.source.startsWith("demo");
      return <article className="queue-card" key={recommendation.id}
        onPointerDownCapture={() => { if (!reviewStartedAt.current.has(recommendation.id)) reviewStartedAt.current.set(recommendation.id, reviewClockNow()); }}
        onFocusCapture={() => { if (!reviewStartedAt.current.has(recommendation.id)) reviewStartedAt.current.set(recommendation.id, reviewClockNow()); }}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className={`recommendation-tag recommendation-${recommendation.action}`}>{decisionLabel(recommendation.action)}</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</strong></span><span>Assumed 14-day cash uplift · 10% scenario <strong>{money(item.currency, item.decision.expected_cash_gain)}</strong></span></div>
        <p className="panel-subtitle">{item.why || item.decision.reason}</p>
        {item.reminder_outcomes?.length ? <div className="fine-print">Next-week outcome: {item.reminder_outcomes.map(outcome => `${outcome.tone} · ${outcome.status === "checking" ? `checking through ${outcome.window_ends}` : outcome.status}${outcome.amount_paid !== "0.00" ? ` (${money(outcome.currency, outcome.amount_paid)})` : ""}`).join(" · ")} · <Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}#invoice-timeline`}>View underlying reminder and payment events</Link></div> : null}
        <div className="fine-print">Policy evidence: {item.decision.reason || item.why}. {item.source.startsWith("demo") ? "Synthetic action." : "Sandbox action."}</div>
        {partial ? <div className="delivery-note"><strong>Approval effect</strong><p>{synthetic ? "Simulates enabling partial payments in the synthetic demo; no PayPal change or email occurs." : "Enables partial payments on this PayPal Sandbox invoice; PayPal may notify the recipient."}</p></div> : <><div className="delivery-note"><strong>Recipient preview</strong><p>{synthetic ? "This is a simulated reminder. No recipient message is sent." : "Approval sends or updates PayPal’s native reminder for the invoice. PayPal controls its subject and recipient-facing body; the note below is internal and is not transmitted."}</p></div><label className="field-label">Internal collection note · {synthetic ? "simulated; no email is sent" : "not sent to the recipient"}<textarea value={drafts[recommendation.id] || ""} onChange={event => editDraft(recommendation.id, event.target.value)}/></label><div className="draft-provenance" role="status"><span>{drafts[recommendation.id] && drafts[recommendation.id] !== recommendation.draft_text ? "Human edited" : recommendation.draft_source === "gemini" ? `AI-generated${recommendation.draft_model ? ` · ${recommendation.draft_model}` : ""}` : recommendation.draft_source === "policy" ? "Policy-generated" : "Template"}</span><span>{draftSaveStates[recommendation.id] === "saving" ? "Saving draft…" : draftSaveStates[recommendation.id] === "failed" ? "Draft save failed · text retained in session" : draftSaveStates[recommendation.id] === "saved" ? "Saved to server" : recommendation.drafted_at ? `Drafted ${new Date(recommendation.drafted_at).toLocaleString("en-GB", { timeZone: "Asia/Kolkata" })} IST` : "Draft save is automatic after typing stops"}</span>{recommendation.draft_text && <button className="btn" type="button" onClick={() => editDraft(recommendation.id, recommendation.draft_text || "")}>Revert to original draft</button>}</div></>}
        {operationState === "uncertain" && <div className="delivery-note" role="status"><strong>Action status needs reconciliation.</strong><p>The saved server state is not confirmed. Check it before retrying.</p><button className="btn" disabled={busy} onClick={() => void checkActionStatus(item)}>Check status</button></div>}
        {operationState === "saved_refresh_failed" && <div className="delivery-note" role="status"><strong>Action saved; queue refresh failed.</strong><p>Check the invoice record or retry the queue refresh before continuing.</p><div className="action-buttons"><button className="btn" disabled={busy} onClick={() => void checkActionStatus(item)}>Check status</button><button className="btn" disabled={loading} onClick={() => void refresh()}>Retry refresh</button></div></div>}
        {operationState === "confirmed" && <div className="fine-print" role="status">Server status checked. Refresh the queue before taking another action.</div>}
        <div className="action-buttons"><button className="btn primary" disabled={busy || bulkBusy || Boolean(operationState) || (!partial && !(drafts[recommendation.id] || "").trim())} onClick={() => { if (item.source === "paypal") setProviderApproval(item); else void act(item, "approve"); }}>{busy ? "Applying…" : partial ? synthetic ? "Approve & simulate" : "Review PayPal approval" : synthetic ? "Approve & simulate" : "Review PayPal reminder"}</button><button className="btn" disabled={busy || bulkBusy || Boolean(operationState)} onClick={() => void act(item, "skip")}>Skip</button><Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
      </article>;
    })}</div>}
    {collectionsOnly && data && <section className="panel collections-workspace" aria-labelledby="collections-workspace-title">
      <div className="panel-head"><div><div className="panel-title" id="collections-workspace-title">Collections cases</div><div className="panel-subtitle">Open and archived balances at least 90 days overdue · snapshot {data.as_of}. Saving a resolution changes review workflow only; it does not settle the balance or execute a write-off.</div></div></div>
      <nav className="view-tabs" aria-label="Collections status">{(["open", "closed", "all"] as const).map(tab => <button className={collectionTab === tab ? "active" : ""} key={tab} onClick={() => setCollectionTab(tab)}>{tab === "open" ? "Open" : tab === "closed" ? "Closed archive" : "All"}<span>{tab === "open" ? data.collections_review?.length || 0 : tab === "closed" ? data.collections_closed?.length || 0 : allCollectionRecords.length}</span></button>)}</nav>
      <div className="collections-filters"><label>Search invoice/client<input className="search" value={collectionSearch} onChange={event => setCollectionSearch(event.target.value)} placeholder="Invoice number or client"/></label><label>Owner<select className="select" value={collectionOwner} onChange={event => setCollectionOwner(event.target.value)}><option value="">All owners</option>{collectionOwners.map(owner => <option key={owner}>{owner}</option>)}</select></label><label>Resolution status<select className="select" value={collectionStatus} onChange={event => setCollectionStatus(event.target.value)}><option value="">All statuses</option>{["open", "in_progress", "awaiting_response", "disputed", "resolved", "written_off"].map(status => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select></label><label>Follow-up from<input type="date" value={followUpFrom} onChange={event => setFollowUpFrom(event.target.value)}/></label><label>Follow-up to<input type="date" value={followUpTo} onChange={event => setFollowUpTo(event.target.value)}/></label><label className="check-filter"><input type="checkbox" checked={unassignedOnly} onChange={event => setUnassignedOnly(event.target.checked)}/> Unassigned</label><label className="check-filter"><input type="checkbox" checked={overdueFollowupsOnly} onChange={event => setOverdueFollowupsOnly(event.target.checked)}/> Overdue follow-ups</label><button className="btn" onClick={() => { setCollectionSearch(""); setCollectionOwner(""); setCollectionStatus(""); setFollowUpFrom(""); setFollowUpTo(""); setUnassignedOnly(false); setOverdueFollowupsOnly(false); }}>Clear filters</button></div>
      <div className="fine-print">Showing {collectionRecords.length} of {allCollectionRecords.length} records · {allCollectionRecords.filter(item => !(item.collection_review?.owner || "").trim() && !["resolved", "written_off"].includes(item.collection_review?.resolution_status || "")).length} unassigned · {allCollectionRecords.filter(item => item.collection_review?.follow_up_date && item.collection_review.follow_up_date < data.as_of && !["resolved", "written_off"].includes(item.collection_review.resolution_status)).length} overdue follow-ups</div>
      {collectionRecords.length ? <div className="queue-list">{collectionRecords.map(item => {
        const review = item.collection_review || { owner: "", notes: "", follow_up_date: null, resolution_status: "open" };
        const draft = collectionDrafts[item.id] || { owner: review.owner, notes: review.notes, follow_up_date: review.follow_up_date || "", resolution_status: review.resolution_status };
        const closed = ["resolved", "written_off"].includes(review.resolution_status);
        const savedTime = review.updated_at ? new Date(review.updated_at).toLocaleString("en-GB", { timeZone: "Asia/Kolkata" }) + " IST" : "not yet saved";
        return <article className="queue-card collection-card" key={item.id}>
          <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className={`recommendation-tag collection-status-tag status-${review.resolution_status}`}>{review.resolution_status.replaceAll("_", " ")}</span></div>
          <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Overdue <strong>{item.days_late} days</strong></span><span>Due <strong>{item.due_date}</strong></span></div>
          <div className="fine-print">Last saved {savedTime} · resolution does not settle or write off this balance.</div>
          <div className="collection-review-form"><label className="field-label">Owner<input value={draft.owner} maxLength={128} placeholder="Assign a reviewer" onChange={event => updateCollection(item.id, "owner", event.target.value)}/></label><label className="field-label">Follow-up date<input type="date" value={draft.follow_up_date} onChange={event => updateCollection(item.id, "follow_up_date", event.target.value)}/></label><label className="field-label">Resolution status<select className="select" value={draft.resolution_status} onChange={event => updateCollection(item.id, "resolution_status", event.target.value)}>{["open", "in_progress", "awaiting_response", "disputed", "resolved", "written_off"].map(status => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select></label><label className="field-label">Review notes<textarea maxLength={4000} value={draft.notes} placeholder="Call outcome, dispute details, or resolution" onChange={event => updateCollection(item.id, "notes", event.target.value)}/></label>
            {collectionStates[item.id] === "dirty" && <div className="fine-print" role="status">Unsaved changes retained in this browser session · explicit Save required; blur does not save.</div>}{collectionStates[item.id] === "saving" && <div className="fine-print" role="status">Saving…</div>}{collectionStates[item.id] === "saved" && <div className="fine-print" role="status">Saved · {savedTime}</div>}{collectionStates[item.id] === "saved_refresh_failed" && <div className="delivery-note" role="status"><strong>Saved, but refresh failed.</strong><button className="btn" onClick={() => void checkCollectionStatus(item)}>Check status</button><button className="btn" onClick={() => void refresh()}>Retry refresh</button></div>}{collectionStates[item.id] === "uncertain" && <div className="delivery-note" role="status"><strong>Save result uncertain.</strong><button className="btn" onClick={() => void checkCollectionStatus(item)}>Check status</button></div>}{collectionStates[item.id] === "failed" && <div className="delivery-note" role="alert">Save failed · edits remain available for retry.</div>}
            <div className="action-buttons"><button className="btn primary" disabled={busyId === item.id} onClick={() => void saveCollectionReview(item)}>{busyId === item.id ? "Saving…" : "Save review"}</button>{closed && <button className="btn" disabled={busyId === item.id} onClick={() => { const reopened = { ...draft, resolution_status: "open" }; setCollectionDrafts(current => ({ ...current, [item.id]: reopened })); sessionStorage.setItem(`latepayer-collection:${item.id}`, JSON.stringify(reopened)); void saveCollectionReview(item, reopened); }}>Reopen case</button>}<Link className="btn" href={`/invoices/${encodeURIComponent(item.id)}`}>Open invoice</Link></div>
          </div>
        </article>;
      })}</div> : <div className="empty-box">No cases match these collection filters.</div>}
    </section>}
    {!collectionsOnly && data?.collections_review?.length ? <section className="collections-lane" aria-labelledby="collections-review-title">
      <div className="panel-head"><div><div className="panel-title" id="collections-review-title">Collections review</div><div className="panel-subtitle">Open balances at least 90 days overdue. These need a person to review disputes, lost invoices, write-off, or collections options; no reminder is sent from this lane.</div><div className="fine-print">Lane total · {Object.entries(data.collections_totals || {}).map(([currency, amount]) => money(currency, amount)).join(" · ") || "No balances"}</div></div></div>
      <div className="queue-list">{data.collections_review.map(item => <article className="queue-card collection-card" key={item.id}>
        <div className="queue-card-head"><div><Link className="table-link" href={`/invoices/${encodeURIComponent(item.id)}`}>{item.invoice_number}</Link><span className="queue-client"> · <Link href={`/clients/${encodeURIComponent(item.client_id)}`}>{item.client_name}</Link></span></div><span className="recommendation-tag">Collections review</span></div>
        <div className="queue-metrics"><span>Balance <strong>{money(item.currency, item.outstanding)}</strong></span><span>Due <strong>{new Date(`${item.due_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric" })}</strong></span><span>Overdue <strong>{item.days_late} days</strong></span></div>
        <p className="panel-subtitle">{item.why || item.decision.reason}</p>
        <div className="fine-print">A collection review status organizes follow-up work; it does not settle or write off the invoice balance. Last saved {item.collection_review?.updated_at ? new Date(item.collection_review.updated_at).toLocaleString("en-GB", { timeZone: "Asia/Kolkata" }) + " IST" : "not yet saved"}.</div>
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
    <ConfirmDialog
      open={Boolean(providerApproval)}
      title={providerApproval?.recommendation?.action === "offer_partial_payment" ? "Enable partial payments in PayPal Sandbox?" : "Approve this PayPal Sandbox reminder?"}
      description={providerApproval?.recommendation?.action === "offer_partial_payment"
        ? "This updates the existing PayPal Sandbox invoice and may notify its recipient. Review the invoice record before approval."
        : "PayPal sends its system generated reminder email. The editable internal note is saved for review and is not emailed."}
      confirmLabel={providerApproval?.recommendation?.action === "offer_partial_payment" ? "Approve payment setting" : "Approve reminder"}
      onCancel={() => setProviderApproval(null)}
      onConfirm={() => { const selected = providerApproval; setProviderApproval(null); if (selected) void act(selected, "approve"); }}
    >
      {providerApproval && <div className="approval-summary">
        <strong>{providerApproval.invoice_number} · {providerApproval.client_name}</strong>
        <div>Recipient {providerApproval.client_email || "email unavailable"}</div>
        <div>Balance {money(providerApproval.currency, providerApproval.outstanding)}</div>
        <div>Assumed 14-day cash uplift (10% scenario): {money(providerApproval.currency, providerApproval.decision.expected_cash_gain)}</div>
      </div>}
    </ConfirmDialog>
    <ConfirmDialog
      open={Boolean(bulkPreview)}
      title="Review friendly reminders before approval"
      description={bulkPreview ? `${bulkPreview.eligibleCount} reminders are eligible; ${bulkPreview.items.length} are included under the shared cap of ${bulkPreview.cap}. They will be approved one at a time with a result shown for each invoice.` : undefined}
      confirmLabel={`Approve ${bulkPreview?.items.length || 0} reminders`}
      onCancel={() => setBulkPreview(null)}
      onConfirm={() => { const preview = bulkPreview; setBulkPreview(null); if (preview) void approveFriendly(preview.items); }}
    >
      {bulkPreview && <>
        <ol className="approval-item-list">{bulkPreview.items.map(item => <li key={item.id}>
          <strong>{item.invoice_number} · {item.client_name}</strong>
          <span>Recipient: {item.source.startsWith("demo") ? "synthetic demo; no email sent" : item.client_email || "email unavailable"}</span>
          <span>Balance: {money(item.currency, item.outstanding)}</span>
          <span>Assumed 14-day cash uplift (10% scenario): {money(item.currency, item.decision.expected_cash_gain)}</span>
        </li>)}</ol>
        <p className="fine-print">{bulkPreview.items.some(item => !item.source.startsWith("demo")) ? "PayPal Sandbox reminders send test notifications; synthetic reminders are simulated." : "Synthetic reminders are simulated; no PayPal email is sent."}</p>
      </>}
    </ConfirmDialog>
  </>;
}

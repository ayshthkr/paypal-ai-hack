"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { clearReads, readFailureMessage, requestJson } from "../lib/api";
import { OperatorControls, useAuth } from "../providers";

type Recipient = { id: string; name: string; email: string };
type WebhookEvidence = { event_type: string; occurred_at: string | null; received_at: string; verified: boolean; processing_status: string; failure_reason: string | null; invoice_id: string | null };
type SyncStatus = { last_run: null | { started_at: string; completed_at: string | null; status: string; created: number; updated: number; event_refreshes: number; retry_failures: number; errors: string[] }; webhook_failures: number; webhook_events: WebhookEvidence[] };
type InvoiceEvidence = { id: string; invoice_number: string; status: string; total: string; paid: string; outstanding: string; currency: string; paypal_id: string | null; payment_url: string | null };

function ist(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) + " IST" : "timestamp unavailable";
}

export default function OperatorWorkbench() {
  const { authenticated } = useAuth();
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [invoice, setInvoice] = useState<InvoiceEvidence | null>(null);
  const [recipientId, setRecipientId] = useState("");
  const [amount, setAmount] = useState("100.00");
  const [term, setTerm] = useState("NET_30");
  const [partial, setPartial] = useState(false);
  const [creating, setCreating] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [created, setCreated] = useState<{ invoice_id: string; invoice_number: string; status: string } | null>(null);

  const refreshEvidence = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [recipientResult, status] = await Promise.all([
        requestJson<{ items: Recipient[] }>("/api/paypal/recipients"),
        requestJson<SyncStatus>("/api/sync/status"),
      ]);
      setRecipients(recipientResult.items); setSyncStatus(status);
      if (created) {
        const current = await requestJson<InvoiceEvidence>(`/api/invoices/${encodeURIComponent(created.invoice_id)}`);
        setInvoice(current);
      }
    } catch (cause) { setError(readFailureMessage(cause, "PayPal Sandbox evidence")); }
    finally { setLoading(false); }
  }, [created]);

  useEffect(() => {
    if (!authenticated) return;
    const timer = window.setTimeout(() => void refreshEvidence(), 0);
    return () => window.clearTimeout(timer);
  }, [authenticated, refreshEvidence]);

  const createSandboxInvoice = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setCreating(true); setError(""); setNotice("");
    const fingerprint = [recipientId, amount, term, partial ? "partial" : "full"].join("|");
    const keyName = `latepayer-operator-create:${fingerprint}`;
    let requestKey = sessionStorage.getItem(keyName);
    if (!requestKey) { requestKey = crypto.randomUUID().replaceAll("-", ""); sessionStorage.setItem(keyName, requestKey); }
    try {
      const response = await requestJson<{ invoice_id: string; invoice_number: string; status: string }>("/api/paypal/invoices", {
        method: "POST", headers: { "Idempotency-Key": requestKey },
        body: JSON.stringify({ client_id: recipientId, amount, payment_term_type: term, partial_payment_allowed: partial }),
      });
      sessionStorage.removeItem(keyName);
      setCreated(response); setNotice(`Sandbox invoice ${response.invoice_number} was created and sent.`);
      clearReads();
      try {
        const evidence = await requestJson<InvoiceEvidence>(`/api/invoices/${encodeURIComponent(response.invoice_id)}`);
        setInvoice(evidence);
        const latest = await requestJson<SyncStatus>("/api/sync/status"); setSyncStatus(latest);
      } catch (cause) {
        setError(`Sandbox invoice ${response.invoice_number} was created, but refreshing its provider status failed. Retry evidence before creating another invoice. ${readFailureMessage(cause, "Sandbox invoice status")}`);
      }
    } catch (cause) { setError(`Create result was not confirmed. Keep the same form values and retry to reuse the same request identity. ${cause instanceof Error ? cause.message : ""}`); }
    finally { setCreating(false); }
  };

  const syncPayPal = async () => {
    setSyncing(true); setError(""); setNotice("");
    try {
      await requestJson("/api/sync", { method: "POST" });
    } catch (cause) {
      setError(`PayPal sync did not return a confirmed result. Check the latest sync status before retrying. ${cause instanceof Error ? cause.message : ""}`);
      setSyncing(false);
      return;
    }
    clearReads();
    try {
      const status = await requestJson<SyncStatus>("/api/sync/status"); setSyncStatus(status);
      if (created) {
        const current = await requestJson<InvoiceEvidence>(`/api/invoices/${encodeURIComponent(created.invoice_id)}`);
        setInvoice(current);
        setNotice(`Sync completed at ${ist(status.last_run?.completed_at)}. ${current.currency} ${Number(current.paid).toFixed(2)} paid · ${Number(current.outstanding).toFixed(2)} outstanding.`);
      } else setNotice(`Sync completed at ${ist(status.last_run?.completed_at)}.`);
    } catch (cause) { setError(`PayPal sync completed, but the evidence refresh failed. Check status or retry evidence. ${readFailureMessage(cause, "sync evidence")}`); }
    finally { setSyncing(false); }
  };

  if (!authenticated) return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="btn" href="/offline-demo">Read-only fallback</Link></div></header><main className="main-wrap"><section className="panel"><div className="eyebrow">Operator route · PayPal Sandbox</div><h1>Sign in to run the integration checklist</h1><p className="subheading">Invoice creation, payment checks, webhooks, and sync are restricted to the authenticated operator session.</p><Link className="btn primary" href="/login">Operator login</Link></section></main></div>;

  return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">Portfolio</Link><Link className="back-link" href="/actions">Action queue</Link><Link className="back-link" href="/collections">Collections</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link><OperatorControls/></div></header><main className="main-wrap">
    <div className="heading-row"><div><div className="eyebrow">Operator route · PayPal Sandbox only</div><h1>PayPal integration checklist</h1><p className="subheading">Create an allowlisted test invoice, pay it with a Sandbox buyer, receive or sync the event, then confirm the balance update.</p></div><button className="btn" disabled={loading || syncing} onClick={() => void refreshEvidence()}>Refresh evidence</button></div>
    <div className="demo-banner"><span className="demo-dot"/><strong>Authenticated</strong><span>PayPal mode: Sandbox · test recipient allowlist enforced · no production invoice operations.</span></div>
    {error && <div className="error-box" role="alert">{error} <button className="btn" onClick={() => void refreshEvidence()}>Retry evidence</button></div>}{notice && <div className="action-message" role="status">{notice}</div>}
    <ol className="operator-checklist">
      <li><strong>Create</strong><span>Choose an allowlisted recipient and create/send a Sandbox invoice.</span><form className="operator-create-form" onSubmit={event => void createSandboxInvoice(event)}><label>Recipient<select className="select" required value={recipientId} onChange={event => setRecipientId(event.target.value)}><option value="">Choose allowlisted recipient</option>{recipients.map(recipient => <option value={recipient.id} key={recipient.id}>{recipient.name} · {recipient.email}</option>)}</select></label><label>Amount<input className="input" type="number" min="0.01" max="1000000" step="0.01" required value={amount} onChange={event => setAmount(event.target.value)}/></label><label>Terms<select className="select" value={term} onChange={event => setTerm(event.target.value)}><option value="DUE_ON_RECEIPT">Due on receipt</option><option value="NET_15">NET 15</option><option value="NET_30">NET 30</option></select></label><label className="check-filter"><input type="checkbox" checked={partial} onChange={event => setPartial(event.target.checked)}/> Allow partial payment</label><button className="btn primary" disabled={creating || !recipients.length}>{creating ? "Creating and sending…" : "Create & send Sandbox invoice"}</button></form>{!recipients.length && !loading && <div className="fine-print">No synced client is on the recipient allowlist. Run a sync and check PAYPAL_ALLOWED_RECIPIENT_EMAILS.</div>}{created && <div className="action-message">Created {created.invoice_number} · provider status {invoice?.status || created.status} · {invoice?.paypal_id ? `PayPal invoice ${invoice.paypal_id}` : "provider ID pending sync"} · <Link className="table-link" href={`/invoices/${encodeURIComponent(created.invoice_id)}`}>Open invoice record</Link>{invoice?.payment_url && <> · <a className="table-link" href={invoice.payment_url} target="_blank" rel="noreferrer">Open PayPal invoice to pay ↗</a></>}</div>}</li>
      <li><strong>Pay</strong><span>{created ? `Use the PayPal invoice link above with a Sandbox buyer. Last invoice state: ${invoice?.status || created.status}.` : "After creation, open the PayPal invoice link and pay it with a Sandbox buyer account."}</span></li>
      <li><strong>Webhook / sync</strong><span>Webhook delivery is recorded with verification state and receive time below. Sync reads PayPal invoice state when webhooks are delayed.</span><div className="action-buttons"><button className="btn primary" disabled={syncing} onClick={() => void syncPayPal()}>{syncing ? "Syncing…" : "↻ Sync PayPal Sandbox"}</button><button className="btn" disabled={loading} onClick={() => void refreshEvidence()}>Check webhook status</button></div>{syncStatus?.last_run && <div className="fine-print">Latest sync {ist(syncStatus.last_run.completed_at || syncStatus.last_run.started_at)} · {syncStatus.last_run.status} · {syncStatus.last_run.created} created · {syncStatus.last_run.updated} updated · {syncStatus.last_run.event_refreshes} event refreshes · {syncStatus.last_run.retry_failures} retries failed</div>}</li>
      <li><strong>Confirm balance</strong><span>{invoice ? `${invoice.invoice_number}: ${invoice.currency} ${Number(invoice.paid).toFixed(2)} paid of ${invoice.currency} ${Number(invoice.total).toFixed(2)} · ${invoice.currency} ${Number(invoice.outstanding).toFixed(2)} outstanding · status ${invoice.status}.` : "Create or select the test invoice, then refresh evidence to see its paid and outstanding balances."}</span>{created && <Link className="table-link" href={`/invoices/${encodeURIComponent(created.invoice_id)}#invoice-timeline`}>Open timestamped invoice timeline</Link>}</li>
    </ol>
    <section className="panel"><div className="panel-title">Recent PayPal webhook evidence</div><div className="fine-print">{syncStatus?.webhook_failures ?? "—"} verified webhook processing failures · timestamps shown in IST.</div>{syncStatus?.webhook_events.length ? <div className="history-list">{syncStatus.webhook_events.map((event, index) => <div className="history-row" key={`${event.received_at}-${index}`}><span className="history-dot"/><div><div className="history-title">{event.event_type} · {event.verified ? event.processing_status : "unverified"}</div><div className="history-meta">Occurred {ist(event.occurred_at)} · received {ist(event.received_at)}{event.invoice_id ? ` · invoice ${event.invoice_id}` : ""}</div>{event.failure_reason && <div className="history-meta">{event.failure_reason}</div>}</div></div>)}</div> : <div className="empty-inline">No webhook events recorded yet. The sync button remains available to reconcile the invoice.</div>}</section>
    </main></div>;
}

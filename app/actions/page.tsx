import Link from "next/link";
import ActionQueue from "./queue-client";
import { OperatorControls } from "../providers";

export default function ActionQueuePage() {
  return <div className="app-shell dashboard-app-shell workbench-app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← Portfolio</Link><Link className="back-link" href="/actions" aria-current="page">Action queue</Link><Link className="back-link" href="/collections">Collections</Link><Link className="back-link" href="/operator">PayPal operator</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link><OperatorControls/></div></header><main className="main-wrap queue-page action-queue-page"><ActionQueue /></main></div>;
}

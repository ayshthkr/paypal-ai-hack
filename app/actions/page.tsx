import Link from "next/link";
import ActionQueue from "./queue-client";

export default function ActionQueuePage() {
  return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← Portfolio</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></div></header><main className="main-wrap"><ActionQueue /></main></div>;
}

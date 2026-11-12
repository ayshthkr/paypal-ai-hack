import Link from "next/link";
import { Suspense } from "react";
import OfflineDemoClient from "./offline-demo-client";

export default function OfflineDemoPage() {
  return <div className="app-shell dashboard-app-shell offline-app-shell">
    <header className="topbar">
      <Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link>
      <nav className="top-actions" aria-label="Demo navigation">
        <Link className="back-link" href="/tour">Guided tour</Link>
        <Link className="back-link" href="/offline-demo" aria-current="page">Read-only fallback</Link>
      </nav>
    </header>
    <main className="main-wrap offline-page">
      <Suspense fallback={<div className="empty-box" role="status">Loading fallback demo…</div>}>
        <OfflineDemoClient />
      </Suspense>
    </main>
  </div>;
}

import Link from "next/link";
import { Suspense } from "react";
import TourClient from "./tour-client";

export default function GuidedTourPage() {
  return <div className="app-shell dashboard-app-shell tour-app-shell">
    <header className="topbar">
      <Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link>
      <nav className="top-actions" aria-label="Demo navigation">
        <Link className="back-link" href="/">Portfolio</Link>
        <Link className="back-link" href="/tour" aria-current="page">Guided tour</Link>
        <Link className="back-link" href="/offline-demo">Read-only fallback</Link>
      </nav>
    </header>
    <main className="main-wrap tour-page">
      <Suspense fallback={<div className="empty-box" role="status">Loading the five demo stops…</div>}>
        <TourClient />
      </Suspense>
    </main>
  </div>;
}

import Link from "next/link";
import { Suspense } from "react";
import OfflineDemoClient from "./offline-demo-client";

export default function OfflineDemoPage() {
  return <div className="app-shell">
    <header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="back-link" href="/tour">← Guided tour</Link></header>
    <main className="main-wrap"><Suspense fallback={<div className="empty-box">Loading fallback demo…</div>}><OfflineDemoClient /></Suspense></main>
  </div>;
}

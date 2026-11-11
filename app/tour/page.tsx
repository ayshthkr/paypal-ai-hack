import Link from "next/link";
import { Suspense } from "react";
import TourClient from "./tour-client";

export default function GuidedTourPage() {
  return <div className="app-shell">
    <header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><div className="top-actions"><Link className="back-link" href="/">← Portfolio</Link><Link className="btn" href="/offline-demo">Read-only fallback</Link></div></header>
    <main className="main-wrap"><Suspense fallback={<div className="empty-box">Loading the five demo stops…</div>}><TourClient /></Suspense></main>
  </div>;
}

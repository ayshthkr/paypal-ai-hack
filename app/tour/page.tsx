import Link from "next/link";
import { Suspense } from "react";
import TourClient from "./tour-client";

export default function GuidedTourPage() {
  return <div className="app-shell">
    <header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="back-link" href="/">← Portfolio</Link></header>
    <main className="main-wrap"><Suspense fallback={<div className="empty-box">Loading the five demo stops…</div>}><TourClient /></Suspense></main>
  </div>;
}

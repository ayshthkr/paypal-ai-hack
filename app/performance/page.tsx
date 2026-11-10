import Link from "next/link";
import ModelPerformance from "./performance-client";

export default function PerformancePage() {
  return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="back-link" href="/">← Portfolio</Link></header><main className="main-wrap"><ModelPerformance /></main></div>;
}

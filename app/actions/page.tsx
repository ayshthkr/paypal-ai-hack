import Link from "next/link";
import ActionQueue from "./queue-client";

export default function ActionQueuePage() {
  return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="back-link" href="/">← Portfolio</Link></header><main className="main-wrap"><ActionQueue /></main></div>;
}

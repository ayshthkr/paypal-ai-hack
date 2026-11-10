import Link from "next/link";
import { cookies } from "next/headers";
import ActionQueue, { type QueueResult } from "./queue-client";

async function getInitialQueue(): Promise<QueueResult | null> {
  const hostPort = process.env.API_HOSTPORT;
  const apiBase = process.env.API_URL || (hostPort ? `http://${hostPort}` : "http://127.0.0.1:8000");
  try {
    const session = (await cookies()).get("latepayer_session")?.value;
    const response = await fetch(`${apiBase.replace(/\/$/, "")}/action-queue`, {
      headers: session ? { cookie: `latepayer_session=${session}` } : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(4_000),
    });
    if (!response.ok) return null;
    return await response.json() as QueueResult;
  } catch {
    return null;
  }
}

export default async function ActionQueuePage() {
  const initialQueue = await getInitialQueue();
  return <div className="app-shell"><header className="topbar"><Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link><Link className="back-link" href="/">← Portfolio</Link></header><main className="main-wrap"><ActionQueue initialData={initialQueue} /></main></div>;
}

import { Suspense } from "react";
import ClientDetail from "./client-detail";

export default async function ClientPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Suspense fallback={<div role="status">Loading client…</div>}><ClientDetail id={id} /></Suspense>;
}

import { Suspense } from "react";
import PortfolioClient from "./portfolio-client";

export default function HomePage() {
  return <Suspense fallback={<main className="main-wrap"><div className="empty-box">Loading invoice portfolio…</div></main>}>
    <PortfolioClient />
  </Suspense>;
}

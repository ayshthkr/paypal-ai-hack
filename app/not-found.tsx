import Link from "next/link";

export default function NotFound() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link href="/" className="brand"><span className="brand-mark">L</span> Late Payer</Link>
      </header>
      <main className="main-wrap">
        <section className="panel">
          <div className="eyebrow">Page not found</div>
          <h1>This page is unavailable</h1>
          <p className="subheading">The link may be old or the page may have moved.</p>
          <Link className="btn" href="/">Back to portfolio</Link>
        </section>
      </main>
    </div>
  );
}

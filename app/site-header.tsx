"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { OperatorControls, useAuth } from "./providers";

const NAV: [string, string][] = [["/", "Portfolio"], ["/actions", "Action queue"], ["/collections", "Collections"], ["/performance", "Model performance"]];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/" || pathname.startsWith("/invoices") || pathname.startsWith("/clients");
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Shared Carbon UI shell header: product name, primary navigation, and operator sign-in. */
export default function SiteHeader() {
  const pathname = usePathname();
  const { authenticated } = useAuth();
  const links = authenticated ? [...NAV, ["/operator", "PayPal operator"] as [string, string]] : NAV;
  return <header className="topbar">
    <Link className="brand" href="/"><span className="brand-mark">L</span> Late Payer</Link>
    <nav className="top-actions" aria-label="Primary">
      {links.map(([href, label]) => {
        const active = isActive(pathname, href);
        return <Link key={href} href={href} className={`back-link${active ? " active" : ""}`} aria-current={active ? "page" : undefined}>{label}</Link>;
      })}
      <span className="topbar-end"><OperatorControls/></span>
    </nav>
  </header>;
}

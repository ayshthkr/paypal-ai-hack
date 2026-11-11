import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";
import { Suspense } from "react";
import TourNavigation from "./tour/tour-navigation";

export const metadata: Metadata = {
  title: "Late Payer | Receivables forecast",
  description: "Invoice payment forecasts and human-approved reminders.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Providers><Suspense fallback={null}><TourNavigation /></Suspense>{children}</Providers></body></html>;
}

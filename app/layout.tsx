import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Late Payer | Receivables forecast",
  description: "Invoice payment forecasts and human-approved reminders.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

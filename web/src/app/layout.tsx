import type { Metadata } from "next";

import { SiteFooter, SiteHeader } from "@/components/SiteHeader";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Memora",
    template: "%s — Memora",
  },
  description:
    "Memora tracks knowledge through one ownership chain: a tenant owns applications, an application knows its actors, an actor opens subjects, and subjects hold sources.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col">
        <SiteHeader />
        <div className="flex-1">{children}</div>
        <SiteFooter />
      </body>
    </html>
  );
}

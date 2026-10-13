import type { Metadata } from "next";

import { Shell } from "@/components/Shell";
import { TenantProvider } from "@/lib/tenant-context";

import "./globals.css";

export const metadata: Metadata = {
  title: "Memora Console",
  description:
    "Manage tenants, applications, API credentials, actors, subjects and sources.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <TenantProvider>
          <Shell>{children}</Shell>
        </TenantProvider>
      </body>
    </html>
  );
}

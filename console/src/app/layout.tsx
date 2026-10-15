import type { Metadata } from "next";

import { Shell } from "@/components/Shell";
import { TenantProvider } from "@/lib/tenant-context";

import "./globals.css";

export const metadata: Metadata = {
  title: "Memora Console",
  description:
    "Manage tenants, applications, API credentials, actors, subjects and sources.",
};

// Runs before first paint: apply the saved theme, else the system preference,
// so a dark-mode user never sees a white flash. Keep it tiny and dependency-free.
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("memora.theme");if(t!=="light"&&t!=="dark"){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=t}catch(e){}})()`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <TenantProvider>
          <Shell>{children}</Shell>
        </TenantProvider>
      </body>
    </html>
  );
}

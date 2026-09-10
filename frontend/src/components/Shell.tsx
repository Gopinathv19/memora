"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

/**
 * The console chrome: a dark utility bar and a persistent resource sidebar.
 *
 * The sidebar is ordered to match the ownership chain
 * (Tenants -> Applications -> Actors -> Subjects -> Sources) rather than
 * alphabetically, so the navigation itself teaches the data model.
 */

interface NavItem {
  href: string;
  label: string;
  hint: string;
}

const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Overview",
    items: [{ href: "/", label: "Dashboard", hint: "Live resource counts" }],
  },
  {
    title: "Ownership chain",
    items: [
      { href: "/tenants", label: "Tenants", hint: "Organizations" },
      { href: "/applications", label: "Applications", hint: "Consuming apps" },
      { href: "/actors", label: "Actors", hint: "Users, services, agents" },
      { href: "/subjects", label: "Subjects", hint: "Workspaces" },
      { href: "/sources", label: "Sources", hint: "Registered knowledge" },
    ],
  },
  {
    title: "Access",
    items: [
      { href: "/credentials", label: "API credentials", hint: "Bearer tokens" },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 flex h-12 items-center gap-3 bg-topbar px-3 text-white">
        <button
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={navOpen}
          onClick={() => setNavOpen((open) => !open)}
          className="rounded px-2 py-1 text-lg leading-none hover:bg-topbar-hover lg:hidden"
        >
          ☰
        </button>
        <Link href="/" className="flex items-center gap-2 font-bold">
          <span
            aria-hidden
            className="grid size-6 place-items-center rounded bg-accent text-xs font-bold"
          >
            M
          </span>
          Memora
          <span className="hidden font-normal text-white/60 sm:inline">
            Console
          </span>
        </Link>
        <div className="ml-auto flex items-center gap-1 text-sm">
          <a
            href={`${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"}/docs`}
            target="_blank"
            rel="noreferrer"
            className="rounded px-2.5 py-1 text-white/85 hover:bg-topbar-hover hover:text-white"
          >
            API reference
          </a>
        </div>
      </header>

      <div className="flex flex-1">
        <nav
          aria-label="Resources"
          className={`${
            navOpen ? "block" : "hidden"
          } w-full shrink-0 border-r border-line bg-panel lg:block lg:w-60`}
        >
          <div className="sticky top-12 max-h-[calc(100vh-3rem)] overflow-y-auto py-4">
            {NAV_SECTIONS.map((section) => (
              <div key={section.title} className="mb-5">
                <div className="px-4 pb-1.5 text-xs font-bold uppercase tracking-wider text-ink-tertiary">
                  {section.title}
                </div>
                <ul>
                  {section.items.map((item) => {
                    const active = isActive(pathname, item.href);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          onClick={() => setNavOpen(false)}
                          aria-current={active ? "page" : undefined}
                          className={`block border-l-[3px] px-4 py-1.5 text-sm transition-colors ${
                            active
                              ? "border-accent bg-accent-soft font-bold text-accent"
                              : "border-transparent text-ink hover:bg-muted-soft"
                          }`}
                        >
                          {item.label}
                          <span className="block text-xs font-normal text-ink-tertiary">
                            {item.hint}
                          </span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </nav>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}

/** Trail back up the ownership chain from the current resource. */
export function Breadcrumbs({
  items,
}: {
  items: { label: string; href?: string }[];
}) {
  return (
    <nav aria-label="Breadcrumb" className="mb-3">
      <ol className="flex flex-wrap items-center gap-1.5 text-sm text-ink-secondary">
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`} className="flex items-center gap-1.5">
            {index > 0 && (
              <span aria-hidden className="text-ink-tertiary">
                /
              </span>
            )}
            {item.href ? (
              <Link href={item.href} className="text-accent hover:underline">
                {item.label}
              </Link>
            ) : (
              <span className="font-bold text-ink">{item.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

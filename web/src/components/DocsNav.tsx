"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The reference's sidebar.
 *
 * Ordered by the ownership chain rather than alphabetically, because the chain
 * is the one thing a reader has to understand before any endpoint makes sense:
 * a tenant owns applications, an application knows its actors, an actor opens
 * subjects, and subjects hold sources.
 */

const SECTIONS: { title: string; items: { href: string; label: string }[] }[] = [
  {
    title: "Getting started",
    items: [
      { href: "/docs", label: "Overview" },
      { href: "/docs/quickstart", label: "Quickstart" },
      { href: "/docs/authentication", label: "Authentication" },
    ],
  },
  {
    title: "Ownership chain",
    items: [
      { href: "/docs/tenants", label: "Tenants" },
      { href: "/docs/applications", label: "Applications" },
      { href: "/docs/actors", label: "Actors" },
      { href: "/docs/subjects", label: "Subjects" },
      { href: "/docs/sources", label: "Sources" },
    ],
  },
  {
    title: "Reference",
    items: [
      { href: "/docs/credentials", label: "API credentials" },
      { href: "/docs/errors", label: "Errors" },
    ],
  },
];

export function DocsNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Documentation" className="text-sm">
      {SECTIONS.map((section) => (
        <div key={section.title} className="mb-5">
          <p className="mb-1 px-3 text-[11px] font-medium uppercase tracking-wider text-ink-tertiary">
            {section.title}
          </p>
          <ul>
            {section.items.map((item) => {
              const active = pathname === item.href;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={`mb-0.5 block rounded-md px-3 py-1.5 transition-colors ${
                      active
                        ? "bg-accent-soft font-medium text-accent"
                        : "text-ink-secondary hover:bg-topbar-hover hover:text-ink"
                    }`}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

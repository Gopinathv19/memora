"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { User } from "@/lib/types";

/**
 * The console chrome: a white breadcrumb bar above a narrow icon rail.
 *
 * The rail is 48px wide and shows icons only; hovering (or focusing) it
 * expands a labelled panel *over* the page rather than pushing it, so the
 * content never reflows while the pointer travels across the navigation.
 *
 * The rail is ordered to match the ownership chain
 * (Tenants -> Applications -> Actors -> Subjects -> Sources) rather than
 * alphabetically, so the navigation itself teaches the data model.
 *
 * It is also the session gate. Every page renders inside it, so asking
 * /auth/me once here is enough to keep the whole console behind a login --
 * there are no per-page checks to forget.
 */

type IconName =
  | "home"
  | "tenants"
  | "applications"
  | "actors"
  | "subjects"
  | "sources"
  | "credentials"
  | "docs";

/** 16px stroked icons, drawn on a 24-unit grid so they share a weight. */
const ICON_PATHS: Record<IconName, string> = {
  home: "M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5",
  tenants: "M4 21V5.5L12 3l8 2.5V21M4 21h16M9 21v-4h6v4M8 9h2M14 9h2M8 13h2M14 13h2",
  applications: "M4 5.5h6v6H4zM14 5.5h6v6h-6zM4 14.5h6v6H4zM14 14.5h6v6h-6z",
  actors:
    "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM3 20c0-3 2.7-5 6-5s6 2 6 5M16.5 5.2a3.5 3.5 0 0 1 0 6.6M18 15.4c2 .8 3 2.3 3 4.6",
  subjects: "M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.5h9A1.5 1.5 0 0 1 21 10v8.5A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5Z",
  sources:
    "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3ZM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  credentials:
    "M14.5 3a6.5 6.5 0 0 1 0 13 6.6 6.6 0 0 1-2.4-.45L10 18H8v2H6v2H3v-3l6.6-6.6A6.5 6.5 0 0 1 14.5 3ZM16 7.5h.01",
  docs: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
};

function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`size-[18px] shrink-0 ${className}`}
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

interface NavItem {
  href: string;
  label: string;
  icon: IconName;
}

const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Overview",
    items: [{ href: "/", label: "Dashboard", icon: "home" }],
  },
  {
    title: "Ownership chain",
    items: [
      { href: "/tenants", label: "Tenants", icon: "tenants" },
      { href: "/applications", label: "Applications", icon: "applications" },
      { href: "/actors", label: "Actors", icon: "actors" },
      { href: "/subjects", label: "Subjects", icon: "subjects" },
      { href: "/sources", label: "Sources", icon: "sources" },
    ],
  },
  {
    title: "Access",
    items: [
      { href: "/credentials", label: "API credentials", icon: "credentials" },
    ],
  },
];

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// The API reference lives on the public site, not behind this login -- someone
// integrating against Memora should be able to read it without an account.
const DOCS_URL = process.env.NEXT_PUBLIC_DOCS_URL ?? "http://localhost:3001/docs";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The label of the rail entry the current URL belongs to, for the breadcrumb. */
function currentSectionLabel(pathname: string): string {
  for (const section of NAV_SECTIONS) {
    for (const item of section.items) {
      if (isActive(pathname, item.href)) return item.label;
    }
  }
  return "Console";
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  const isLoginPage = pathname === "/login";

  useEffect(() => {
    if (isLoginPage) {
      setChecking(false);
      return;
    }
    let cancelled = false;
    api.auth
      .me()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        // request() has already sent the browser to /login on a 401.
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isLoginPage]);

  // The login page brings its own layout: no rail, no user menu.
  if (isLoginPage) return <>{children}</>;

  if (checking) {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-ink-secondary">
        Loading...
      </div>
    );
  }

  // Not signed in, and the redirect is already in flight. Render nothing rather
  // than a flash of empty console.
  if (!user) return null;

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar
        user={user}
        section={currentSectionLabel(pathname)}
        onToggleNav={() => setNavOpen((open) => !open)}
        navOpen={navOpen}
      />

      <div className="flex flex-1">
        {/* Desktop: the icon rail. It reserves 48px of layout width, and the
            expanded panel is absolutely positioned on top of the page. */}
        <div
          className="relative hidden w-12 shrink-0 lg:block"
          onMouseEnter={() => setRailOpen(true)}
          onMouseLeave={() => setRailOpen(false)}
        >
          <nav
            aria-label="Resources"
            onFocus={() => setRailOpen(true)}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node)) {
                setRailOpen(false);
              }
            }}
            className={`rail fixed bottom-0 top-12 z-30 flex flex-col overflow-hidden border-r border-line bg-rail ${
              railOpen ? "w-56 shadow-lg" : "w-12"
            }`}
          >
            <RailBody
              pathname={pathname}
              expanded={railOpen}
              onNavigate={() => setRailOpen(false)}
            />
          </nav>
        </div>

        {/* Mobile: the same navigation, permanently labelled, toggled by the
            hamburger in the top bar. */}
        {navOpen && (
          <nav
            aria-label="Resources"
            className="absolute inset-x-0 top-12 z-30 border-b border-line bg-panel shadow-lg lg:hidden"
          >
            <RailBody
              pathname={pathname}
              expanded
              onNavigate={() => setNavOpen(false)}
            />
          </nav>
        )}

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}

/**
 * The rail's contents, shared by the desktop rail and the mobile drawer.
 *
 * Collapsed, the section titles disappear and the labels are kept in the DOM
 * (faded and aria-hidden by width, not removed) so that expanding does not
 * re-mount anything and screen readers still read each link's name.
 */
function RailBody({
  pathname,
  expanded,
  onNavigate,
}: {
  pathname: string;
  expanded: boolean;
  onNavigate: () => void;
}) {
  return (
    <>
      <div className="flex-1 overflow-y-auto py-3">
        {NAV_SECTIONS.map((section) => (
          <div key={section.title} className="mb-3">
            <div
              className={`rail-label overflow-hidden px-3 pb-1 text-[11px] font-medium uppercase tracking-wider text-ink-tertiary ${
                expanded ? "h-4 opacity-100" : "h-0 opacity-0"
              }`}
            >
              {section.title}
            </div>
            <ul className="px-1.5">
              {section.items.map((item) => {
                const active = isActive(pathname, item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      title={expanded ? undefined : item.label}
                      aria-current={active ? "page" : undefined}
                      className={`mb-0.5 flex h-9 items-center gap-2.5 rounded-md px-[9px] transition-colors ${
                        active
                          ? "bg-accent-soft text-accent"
                          : "text-ink-secondary hover:bg-topbar-hover hover:text-ink"
                      }`}
                    >
                      <Icon name={item.icon} />
                      <span
                        className={`rail-label truncate text-sm ${
                          expanded ? "opacity-100" : "opacity-0"
                        } ${active ? "font-medium" : ""}`}
                      >
                        {item.label}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className="border-t border-line px-1.5 py-2">
        <a
          href={DOCS_URL}
          target="_blank"
          rel="noreferrer"
          title={expanded ? undefined : "API reference"}
          className="flex h-9 items-center gap-2.5 rounded-md px-[9px] text-ink-secondary transition-colors hover:bg-topbar-hover hover:text-ink"
        >
          <Icon name="docs" />
          <span
            className={`rail-label truncate text-sm ${
              expanded ? "opacity-100" : "opacity-0"
            }`}
          >
            API reference
          </span>
        </a>
      </div>
    </>
  );
}

/**
 * The breadcrumb bar. Supabase puts the product mark, then a chain of
 * switchers, in a white strip; ours carries the signed-in user in place of an
 * organization switcher, since a Memora console session has exactly one.
 */
function TopBar({
  user,
  section,
  onToggleNav,
  navOpen,
}: {
  user: User;
  section: string;
  onToggleNav: () => void;
  navOpen: boolean;
}) {
  return (
    <header className="sticky top-0 z-40 flex h-12 items-center gap-2 border-b border-line bg-topbar px-3">
      <button
        type="button"
        aria-label="Toggle navigation"
        aria-expanded={navOpen}
        onClick={onToggleNav}
        className="rounded-md px-2 py-1 text-lg leading-none text-ink-secondary hover:bg-topbar-hover lg:hidden"
      >
        ☰
      </button>

      <Link href="/" className="flex items-center gap-2" aria-label="Memora">
        <span
          aria-hidden
          className="grid size-6 place-items-center rounded-md bg-brand text-[11px] font-bold text-brand-ink"
        >
          M
        </span>
      </Link>

      <Crumb>Memora</Crumb>
      <Divider />
      <Crumb muted>{user.name || user.email}</Crumb>
      <Divider />
      <Crumb current>{section}</Crumb>

      <div className="ml-auto flex items-center gap-1.5">
        <UserMenu user={user} />
      </div>
    </header>
  );
}

function Crumb({
  children,
  muted,
  current,
}: {
  children: React.ReactNode;
  muted?: boolean;
  current?: boolean;
}) {
  return (
    <span
      className={`hidden max-w-[14rem] truncate text-sm sm:inline ${
        current
          ? "font-medium text-ink"
          : muted
            ? "text-ink-secondary"
            : "font-medium text-ink"
      }`}
    >
      {children}
    </span>
  );
}

function Divider() {
  return (
    <span aria-hidden className="hidden text-ink-tertiary sm:inline">
      /
    </span>
  );
}

/** Avatar button with a small dropdown -- the console's only menu. */
function UserMenu({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape, the two ways anyone expects to
  // dismiss a menu they opened by accident.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function signOut() {
    try {
      await api.auth.logout();
    } finally {
      window.location.href = "/login";
    }
  }

  const initial = (user.name || user.email).trim().charAt(0).toUpperCase();

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        className="grid size-7 place-items-center rounded-full border border-line bg-surface-strong text-xs font-medium text-ink-secondary hover:text-ink"
      >
        {initial}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-1.5 w-56 overflow-hidden rounded-md border border-line bg-panel py-1 shadow-lg"
        >
          <div className="border-b border-line-soft px-3 py-2">
            <p className="truncate text-sm font-medium text-ink">
              {user.name || "Signed in"}
            </p>
            <p className="truncate text-xs text-ink-secondary">{user.email}</p>
          </div>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer"
            role="menuitem"
            className="block px-3 py-1.5 text-sm text-ink-secondary hover:bg-topbar-hover hover:text-ink"
          >
            API reference
          </a>
          <a
            href={`${API_BASE_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            role="menuitem"
            className="block px-3 py-1.5 text-sm text-ink-secondary hover:bg-topbar-hover hover:text-ink"
          >
            OpenAPI schema
          </a>
          <button
            type="button"
            role="menuitem"
            onClick={signOut}
            className="block w-full px-3 py-1.5 text-left text-sm text-ink-secondary hover:bg-topbar-hover hover:text-ink"
          >
            Sign out
          </button>
        </div>
      )}
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
              <Link
                href={item.href}
                className="hover:text-ink hover:underline"
              >
                {item.label}
              </Link>
            ) : (
              <span className="font-medium text-ink">{item.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

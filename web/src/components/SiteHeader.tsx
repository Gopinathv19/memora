import Link from "next/link";

import { CONSOLE_URL } from "@/lib/config";

/**
 * The public site's one piece of chrome.
 *
 * It carries the same mark and hairline as the console so the two read as one
 * product, but it is deliberately not the console's icon rail: nothing here is
 * an operation, so there is nothing to navigate between except reading and
 * leaving for the console.
 */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-topbar/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2">
          <span
            aria-hidden
            className="grid size-6 place-items-center rounded-md bg-brand text-[11px] font-bold text-brand-ink"
          >
            M
          </span>
          <span className="text-sm font-medium text-ink">Memora</span>
        </Link>

        <nav className="ml-2 flex items-center gap-1 text-sm">
          <Link
            href="/docs"
            className="rounded-md px-2.5 py-1.5 text-ink-secondary transition-colors hover:bg-topbar-hover hover:text-ink"
          >
            API reference
          </Link>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <a
            href={CONSOLE_URL}
            className="inline-flex h-[34px] items-center justify-center rounded-md border border-brand bg-brand px-3 text-sm font-medium text-brand-ink transition-colors hover:border-brand-hover hover:bg-brand-hover"
          >
            Open console
          </a>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-line">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-xs text-ink-tertiary sm:px-6">
        <span>Memora</span>
        <div className="flex gap-4">
          <Link href="/docs" className="hover:text-ink-secondary">
            API reference
          </Link>
          <a href={CONSOLE_URL} className="hover:text-ink-secondary">
            Console
          </a>
        </div>
      </div>
    </footer>
  );
}

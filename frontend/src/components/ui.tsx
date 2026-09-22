"use client";

import Link from "next/link";
import type { ReactNode } from "react";

/* ------------------------------------------------------------------ Buttons */

/*
 * Supabase's buttons are quiet. The default is a white 6px-radius box with a
 * hairline border and ordinary text weight; only the one action a page is
 * really offering gets the green fill. Weights stay at medium throughout --
 * bold text is what made the old console look like a different product.
 */

type ButtonVariant = "primary" | "normal" | "danger" | "link";

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 h-[34px] text-sm font-medium transition-colors";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary:
    "bg-brand text-brand-ink border-brand hover:bg-brand-hover hover:border-brand-hover",
  normal:
    "bg-panel text-ink border-line hover:bg-surface-strong",
  danger:
    "bg-panel text-danger border-danger/40 hover:bg-danger-soft hover:border-danger",
  link: "bg-transparent text-ink-secondary border-transparent hover:text-ink hover:underline",
};

export function Button({
  children,
  variant = "normal",
  type = "button",
  disabled,
  onClick,
  className = "",
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  type?: "button" | "submit";
  disabled?: boolean;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`${BUTTON_BASE} disabled:cursor-not-allowed disabled:opacity-45 ${BUTTON_STYLES[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function ButtonLink({
  href,
  children,
  variant = "primary",
}: {
  href: string;
  children: ReactNode;
  variant?: ButtonVariant;
}) {
  return (
    <Link href={href} className={`${BUTTON_BASE} ${BUTTON_STYLES[variant]}`}>
      {children}
    </Link>
  );
}

/* ------------------------------------------------------------------- Badges */

/**
 * Status pill. The mapping is intentionally centralized: `pending` must look
 * the same on the dashboard, the source list and the source detail page, and a
 * status this function has not seen falls back to neutral grey rather than
 * rendering unstyled.
 */
const STATUS_TONE: Record<string, string> = {
  active: "text-ok bg-ok-soft border-ok/20",
  completed: "text-ok bg-ok-soft border-ok/20",
  pending: "text-warn bg-warn-soft border-warn/20",
  processing: "text-accent bg-accent-soft border-accent/20",
  suspended: "text-warn bg-warn-soft border-warn/20",
  failed: "text-danger bg-danger-soft border-danger/20",
  revoked: "text-danger bg-danger-soft border-danger/20",
  expired: "text-danger bg-danger-soft border-danger/20",
};

export function StatusBadge({ status }: { status: string }) {
  const tone =
    STATUS_TONE[status.toLowerCase()] ?? "text-muted bg-muted-soft border-line";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-[1px] text-xs font-medium capitalize ${tone}`}
    >
      {status}
    </span>
  );
}

export function TypeTag({ value }: { value: string }) {
  return (
    <span className="inline-flex items-center rounded border border-line bg-surface px-1.5 py-[1px] font-mono text-xs text-ink-secondary">
      {value}
    </span>
  );
}

/* ------------------------------------------------------------------- Panels */

/**
 * A bordered card. No shadow: on this near-white canvas the hairline is the
 * separation, and a shadow would make every section float.
 */
export function Panel({
  title,
  description,
  actions,
  children,
  counter,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  counter?: number;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel">
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line bg-surface px-4 py-2.5">
          <div>
            {title && (
              <h2 className="text-sm font-medium text-ink">
                {title}
                {counter !== undefined && (
                  <span className="ml-1.5 font-normal text-ink-tertiary">
                    ({counter})
                  </span>
                )}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs text-ink-secondary">{description}</p>
            )}
          </div>
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {eyebrow && (
          <div className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
            {eyebrow}
          </div>
        )}
        <h1 className="truncate text-xl font-medium text-ink">{title}</h1>
        {description && (
          <p className="mt-1 max-w-2xl text-sm text-ink-secondary">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/* --------------------------------------------------------------- Key/values */

export function KeyValueGrid({
  items,
  columns = 3,
}: {
  items: { label: string; value: ReactNode }[];
  columns?: 2 | 3;
}) {
  return (
    <dl
      className={`grid gap-x-8 gap-y-4 p-4 ${
        columns === 2 ? "sm:grid-cols-2" : "sm:grid-cols-2 lg:grid-cols-3"
      }`}
    >
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-xs font-medium uppercase tracking-wide text-ink-tertiary">
            {item.label}
          </dt>
          <dd className="mt-1 break-words text-sm text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Long opaque values (UUIDs, storage URIs) that operators copy verbatim. */
export function Mono({ children }: { children: ReactNode }) {
  return (
    <span className="font-mono text-[13px] break-all text-ink">{children}</span>
  );
}

/* ------------------------------------------------------------------- States */

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2.5 px-4 py-14 text-sm text-ink-secondary">
      <span
        aria-hidden
        className="size-4 animate-spin rounded-full border-2 border-line border-t-brand"
      />
      {label}…
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="m-4 rounded-md border border-danger/30 bg-danger-soft p-4"
    >
      <div className="flex items-start gap-2.5">
        <span aria-hidden className="mt-px text-danger">
          ⚠
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink">Request failed</p>
          <p className="mt-0.5 text-sm break-words text-ink-secondary">
            {message}
          </p>
          {onRetry && (
            <div className="mt-3">
              <Button onClick={onRetry}>Retry</Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="px-4 py-14 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && (
        <p className="mx-auto mt-1 max-w-md text-sm text-ink-secondary">
          {description}
        </p>
      )}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function InlineError({ message }: { message: string }) {
  return (
    <p role="alert" className="text-sm text-danger">
      {message}
    </p>
  );
}

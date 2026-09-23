import type { ReactNode } from "react";

/**
 * The reference's building blocks.
 *
 * Every endpoint on this site is described with the same four things in the
 * same order -- method and path, what it does, what it takes, what it returns
 * -- so a reader who has read one page can skim the rest.
 */

const METHOD_TONE: Record<string, string> = {
  GET: "border-info/25 bg-info-soft text-info",
  POST: "border-ok/25 bg-ok-soft text-ok",
  PATCH: "border-warn/25 bg-warn-soft text-warn",
  DELETE: "border-danger/25 bg-danger-soft text-danger",
};

/** The heading every endpoint section starts with. */
export function Endpoint({
  method,
  path,
  id,
  children,
}: {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  path: string;
  id?: string;
  children?: ReactNode;
}) {
  return (
    <div id={id} className="mt-10 scroll-mt-20 first:mt-0">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded border px-1.5 py-[1px] font-mono text-xs font-medium ${
            METHOD_TONE[method]
          }`}
        >
          {method}
        </span>
        <code className="font-mono text-sm text-ink">{path}</code>
      </div>
      {children && <div className="mt-2 text-sm text-ink-secondary">{children}</div>}
    </div>
  );
}

/** The parameters or body fields a call accepts. */
export function Fields({
  title = "Body",
  rows,
}: {
  title?: string;
  rows: {
    name: string;
    type: string;
    required?: boolean;
    description: ReactNode;
  }[];
}) {
  return (
    <div className="my-4 overflow-hidden rounded-lg border border-line">
      <div className="border-b border-line bg-surface px-4 py-2 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
        {title}
      </div>
      <dl className="divide-y divide-line-soft">
        {rows.map((row) => (
          <div key={row.name} className="px-4 py-3 sm:flex sm:gap-6">
            <dt className="sm:w-56 sm:shrink-0">
              <code className="font-mono text-[13px] text-ink">{row.name}</code>
              <span className="ml-2 font-mono text-xs text-ink-tertiary">
                {row.type}
              </span>
              {row.required && (
                <span className="ml-2 text-xs font-medium text-danger">
                  required
                </span>
              )}
            </dt>
            <dd className="mt-1 text-sm text-ink-secondary sm:mt-0">
              {row.description}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** A point worth stopping on: a constraint, a warning, a one-way door. */
export function Callout({
  tone = "note",
  title,
  children,
}: {
  tone?: "note" | "warn";
  title?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`my-4 rounded-lg border px-4 py-3 text-sm ${
        tone === "warn"
          ? "border-warn/30 bg-warn-soft"
          : "border-line bg-surface"
      }`}
    >
      {title && <p className="font-medium text-ink">{title}</p>}
      <div className={`text-ink-secondary ${title ? "mt-1" : ""}`}>{children}</div>
    </div>
  );
}

/** Page title and standfirst, identical on every reference page. */
export function DocHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <header className="mb-8 border-b border-line pb-6">
      {eyebrow && (
        <p className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
          {eyebrow}
        </p>
      )}
      <h1 className="mt-1 text-2xl font-medium text-ink">{title}</h1>
      {children && (
        <div className="mt-2 max-w-2xl text-sm text-ink-secondary">{children}</div>
      )}
    </header>
  );
}

export function Section({
  id,
  title,
  children,
}: {
  id?: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="mt-10 scroll-mt-20">
      <h2 className="mb-3 text-base font-medium text-ink">{title}</h2>
      {children}
    </section>
  );
}

/** Body copy. Kept narrow -- long lines are what make docs tiring to read. */
export function P({ children }: { children: ReactNode }) {
  return <p className="my-3 max-w-2xl text-sm leading-relaxed text-ink-secondary">{children}</p>;
}

export function Ul({ children }: { children: ReactNode }) {
  return (
    <ul className="my-3 max-w-2xl list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-ink-secondary marker:text-ink-tertiary">
      {children}
    </ul>
  );
}

export function C({ children }: { children: ReactNode }) {
  return (
    <code className="rounded border border-line bg-surface px-1 py-px font-mono text-[12.5px] text-ink">
      {children}
    </code>
  );
}

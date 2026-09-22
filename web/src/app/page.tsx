import Link from "next/link";

import { CONSOLE_URL } from "@/lib/config";

/**
 * The landing page.
 *
 * It explains what Memora is and then gets out of the way: every action a
 * visitor might want -- sign in, create a tenant, issue a credential -- happens
 * in the console, so every call to action here is a link into it rather than a
 * form. The only thing this site does itself is explain the API.
 */

const CHAIN = [
  {
    label: "Tenant",
    detail: "An organization. Everything below belongs to exactly one.",
  },
  {
    label: "Application",
    detail: "A consuming system. Holds its own API credentials.",
  },
  {
    label: "Actor",
    detail: "Who or what operates: a user, a service, an agent, a job.",
  },
  {
    label: "Subject",
    detail: "A workspace. The boundary sources are scoped to.",
  },
  {
    label: "Source",
    detail: "Registered knowledge: a file, a URL, a conversation.",
  },
];

export default function LandingPage() {
  return (
    <>
      <section className="mx-auto max-w-7xl px-4 pb-16 pt-20 sm:px-6">
        <div className="max-w-2xl">
          <h1 className="text-4xl font-medium leading-tight tracking-tight text-ink sm:text-5xl">
            Knowledge with an owner,
            <br />
            all the way down.
          </h1>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-ink-secondary">
            Memora records where every piece of knowledge came from and who it
            belongs to, through one ownership chain enforced on every read. No
            query reaches across a tenant it was not issued for.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <a
              href={CONSOLE_URL}
              className="inline-flex h-10 items-center justify-center rounded-md border border-brand bg-brand px-4 text-sm font-medium text-brand-ink transition-colors hover:border-brand-hover hover:bg-brand-hover"
            >
              Open the console
            </a>
            <Link
              href="/docs"
              className="inline-flex h-10 items-center justify-center rounded-md border border-line bg-panel px-4 text-sm font-medium text-ink transition-colors hover:bg-surface-strong"
            >
              Read the API reference
            </Link>
          </div>
        </div>
      </section>

      <section className="border-y border-line bg-surface">
        <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6">
          <h2 className="text-xs font-medium uppercase tracking-wider text-ink-tertiary">
            The ownership chain
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-ink-secondary">
            Five levels, each owned by the one above it. Learn these and the
            whole API follows — every endpoint is a verb applied to one of them.
          </p>

          <ol className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {CHAIN.map((level, index) => (
              <li
                key={level.label}
                className="rounded-lg border border-line bg-panel p-4"
              >
                <span className="font-mono text-xs text-ink-tertiary">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <p className="mt-1 text-sm font-medium text-ink">{level.label}</p>
                <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
                  {level.detail}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
        <div className="grid gap-8 lg:grid-cols-3">
          <Feature title="Scoped by construction">
            A caller&apos;s reach comes from its credential, never from the ids
            in its URL. An API credential is pinned to one tenant and one
            application and cannot see past them, whatever it asks for.
          </Feature>
          <Feature title="One token, one application">
            Credentials are issued per application and shown exactly once. Only
            a hash is stored, so a leaked token is revoked rather than
            recovered.
          </Feature>
          <Feature title="Audited, not guessed">
            Every subject records the actor that opened it and every source the
            actor that registered it, so provenance is a column rather than a
            reconstruction.
          </Feature>
        </div>
      </section>

      <section className="border-t border-line bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-12 sm:px-6">
          <div>
            <h2 className="text-lg font-medium text-ink">
              Everything happens in the console
            </h2>
            <p className="mt-1 max-w-xl text-sm text-ink-secondary">
              Create a tenant, register an application, issue a credential and
              watch what arrives. This site only explains the API behind it.
            </p>
          </div>
          <a
            href={CONSOLE_URL}
            className="inline-flex h-10 shrink-0 items-center justify-center rounded-md border border-brand bg-brand px-4 text-sm font-medium text-brand-ink transition-colors hover:border-brand-hover hover:bg-brand-hover"
          >
            Open the console
          </a>
        </div>
      </section>
    </>
  );
}

function Feature({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
        {children}
      </p>
    </div>
  );
}

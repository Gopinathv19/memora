"use client";

/**
 * The top-bar tenant switcher: shows the active tenant and lets you change it.
 *
 * "All tenants" is the default (the console's original behaviour); picking a
 * specific tenant scopes every list page and pre-fills every create form.
 * Tenants are created from here too -- the Tenants page manages them, but the
 * everyday "which tenant am I in" question is answered in this dropdown.
 */

import { useEffect, useRef, useState } from "react";

import { useTenant } from "@/lib/tenant-context";
import { CreateTenantModal } from "@/components/modals";

function PlusIcon() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      className="size-[13px]"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function TenantSwitcher() {
  const { tenant, tenants, loading, setTenant, refresh } = useTenant();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

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

  if (loading) {
    return <span className="text-xs text-ink-tertiary">…</span>;
  }

  const onCreated = async (created: (typeof tenants)[number]) => {
    setCreating(false);
    await refresh();
    // A brand-new tenant is almost always the one you want to work in next.
    setTenant(created);
  };

  // With no tenants at all there is nothing to switch between: the button
  // becomes the way to create the first one, so the empty state is obvious
  // instead of a dropdown that says nothing.
  if (tenants.length === 0) {
    return (
      <>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="flex items-center gap-1.5 rounded-md border border-dashed border-line bg-surface px-2.5 py-1 text-sm text-ink-secondary hover:bg-topbar-hover hover:text-ink"
        >
          <PlusIcon />
          Create tenant
        </button>
        {creating && (
          <CreateTenantModal
            onClose={() => setCreating(false)}
            onCreated={onCreated}
          />
        )}
      </>
    );
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1 text-sm text-ink hover:bg-topbar-hover"
      >
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-[15px] text-ink-secondary"
        >
          <path d="M4 21V5.5L12 3l8 2.5V21M4 21h16M9 21v-4h6v4M8 9h2M14 9h2M8 13h2M14 13h2" />
        </svg>
        <span className="max-w-[10rem] truncate">
          {tenant ? tenant.name : "All tenants"}
        </span>
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="size-[12px] text-ink-tertiary"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 mt-1.5 w-56 overflow-hidden rounded-md border border-line bg-panel py-1 shadow-lg"
        >
          <button
            type="button"
            role="option"
            aria-selected={tenant === null}
            onClick={() => {
              setTenant(null);
              setOpen(false);
            }}
            className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-topbar-hover ${
              tenant === null ? "font-medium text-ink" : "text-ink-secondary"
            }`}
          >
            All tenants
          </button>
          {tenants.map((t) => (
            <button
              key={t.id}
              type="button"
              role="option"
              aria-selected={tenant?.id === t.id}
              onClick={() => {
                setTenant(t);
                setOpen(false);
              }}
              className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-topbar-hover ${
                tenant?.id === t.id ? "font-medium text-ink" : "text-ink-secondary"
              }`}
            >
              {t.name}
            </button>
          ))}
          <div className="my-1 border-t border-line" />
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setCreating(true);
            }}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-ink-secondary hover:bg-topbar-hover hover:text-ink"
          >
            <PlusIcon />
            New tenant
          </button>
        </div>
      )}

      {creating && (
        <CreateTenantModal
          onClose={() => setCreating(false)}
          onCreated={onCreated}
        />
      )}
    </div>
  );
}

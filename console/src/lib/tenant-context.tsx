"use client";

/**
 * The console's "current tenant" context.
 *
 * Picking a tenant once scopes every list page and pre-fills every create
 * form, instead of each page asking "which tenant?" again. The choice is
 * persisted to localStorage so it survives a refresh. `null` means "All
 * tenants" -- the console's original behaviour, so nothing breaks when no
 * tenant is selected.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { usePathname } from "next/navigation";

import { api } from "@/lib/api";
import type { Tenant } from "@/lib/types";

const STORAGE_KEY = "memora.active_tenant_id";

interface TenantContextValue {
  /** The active tenant, or null for "All tenants". */
  tenant: Tenant | null;
  /** Every tenant the user owns (for the switcher). */
  tenants: Tenant[];
  /** True until the tenant list has loaded once. */
  loading: boolean;
  setTenant: (tenant: Tenant | null) => void;
  /** Refetch the tenant list, e.g. after creating one. Returns the new list. */
  refresh: () => Promise<Tenant[]>;
}

const TenantContext = createContext<TenantContextValue>({
  tenant: null,
  tenants: [],
  loading: true,
  setTenant: () => {},
  refresh: () => Promise.resolve([]),
});

export function TenantProvider({ children }: { children: React.ReactNode }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenantState] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);
  // The sign-in page has no session yet, so there is nothing to list.
  const onLoginPage = usePathname() === "/login";

  const load = useCallback(async (): Promise<Tenant[]> => {
    const list = await api.tenants.list();
    setTenants(list);
    setTenantState((current) => {
      // Keep the current selection when it still exists; otherwise restore
      // the saved choice. With exactly one tenant, select it automatically
      // -- there is nothing to switch.
      if (current && list.some((t) => t.id === current.id)) return current;
      const savedId =
        typeof window !== "undefined"
          ? window.localStorage.getItem(STORAGE_KEY)
          : null;
      const saved = list.find((t) => t.id === savedId) ?? null;
      return saved ?? (list.length === 1 ? list[0] : null);
    });
    return list;
  }, []);

  useEffect(() => {
    if (onLoginPage) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    load()
      .catch(() => {
        // A failed tenant list leaves the console in "All tenants" mode.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [load, onLoginPage]);

  const setTenant = useCallback((next: Tenant | null) => {
    setTenantState(next);
    if (typeof window !== "undefined") {
      if (next) window.localStorage.setItem(STORAGE_KEY, next.id);
      else window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const refresh = useCallback(async (): Promise<Tenant[]> => {
    try {
      return await load();
    } catch {
      return [];
    }
  }, [load]);

  return (
    <TenantContext.Provider
      value={{ tenant, tenants, loading, setTenant, refresh }}
    >
      {children}
    </TenantContext.Provider>
  );
}

export function useTenant(): TenantContextValue {
  return useContext(TenantContext);
}
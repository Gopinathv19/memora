"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

export interface ResourceState<T> {
  data: T | undefined;
  /** True only on the first load, so a refresh does not blank the table. */
  loading: boolean;
  /** True while a background reload is in flight. */
  refreshing: boolean;
  error: string | undefined;
  reload: () => void;
}

/*
 * Stale-while-revalidate. Every result is remembered for the lifetime of the
 * tab, keyed by what the fetcher does (its source text) and the `deps` it was
 * called with, which together identify a request. Coming back to a page
 * renders the last result immediately and refreshes it in the background, so
 * navigation never starts from an empty "Loading…" while the API is a network
 * hop away from its database. The refresh always runs, so the data on screen
 * is never more than one response behind.
 */
const resultCache = new Map<string, unknown>();

function cacheKey(fetcher: () => unknown, deps: unknown[]): string | null {
  try {
    return `${fetcher.toString()}|${JSON.stringify(deps)}`;
  } catch {
    return null; // deps that cannot be serialised just skip the cache
  }
}

/**
 * Load data from the API with explicit loading, error and empty handling.
 *
 * Every list and detail page in the console goes through this hook, which is
 * why all of them have the same three states rather than each page inventing
 * its own. `deps` behaves like a useEffect dependency list: change a filter and
 * the fetch re-runs.
 */
export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
): ResourceState<T> {
  const initialKey = cacheKey(fetcher, deps);
  const hasCached = initialKey !== null && resultCache.has(initialKey);
  const [data, setData] = useState<T | undefined>(() =>
    hasCached ? (resultCache.get(initialKey!) as T) : undefined,
  );
  const [loading, setLoading] = useState(!hasCached);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string>();

  // Keep the latest fetcher without making it a dependency: it is a new closure
  // on every render, and depending on it would loop forever.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const hasLoaded = useRef(hasCached);
  const depsRef = useRef(deps);
  depsRef.current = deps;

  const run = useCallback(async () => {
    if (hasLoaded.current) setRefreshing(true);
    const key = cacheKey(fetcherRef.current, depsRef.current);
    try {
      const result = await fetcherRef.current();
      if (key !== null) resultCache.set(key, result);
      setData(result);
      setError(undefined);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Something went wrong",
      );
    } finally {
      hasLoaded.current = true;
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    // A page we have already seen (or the same page with other filters we have
    // already used) paints its last result straight away.
    const key = cacheKey(fetcherRef.current, depsRef.current);
    if (key !== null && resultCache.has(key)) {
      setData(resultCache.get(key) as T);
      setLoading(false);
      hasLoaded.current = true;
    }
    // The guard stops a response from an abandoned render (a filter changed
    // mid-flight) from overwriting fresher data.
    void (async () => {
      if (!active) return;
      await run();
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, refreshing, error, reload: run };
}

/** Tracks an in-flight mutation so a form can disable itself and show errors. */
export function useMutation<Args extends unknown[], Result>(
  action: (...args: Args) => Promise<Result>,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  const mutate = useCallback(
    async (...args: Args): Promise<Result | undefined> => {
      setPending(true);
      setError(undefined);
      try {
        return await action(...args);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Something went wrong");
        return undefined;
      } finally {
        setPending(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return { mutate, pending, error, clearError: () => setError(undefined) };
}

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
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string>();

  // Keep the latest fetcher without making it a dependency: it is a new closure
  // on every render, and depending on it would loop forever.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const hasLoaded = useRef(false);

  const run = useCallback(async () => {
    if (hasLoaded.current) setRefreshing(true);
    try {
      const result = await fetcherRef.current();
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

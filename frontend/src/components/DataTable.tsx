"use client";

import type { ReactNode } from "react";

import { EmptyState, ErrorState, LoadingState } from "./ui";

export interface Column<T> {
  header: string;
  /** Rendered cell content. */
  cell: (row: T) => ReactNode;
  /** Narrow columns (badges, counts) should not stretch. */
  width?: string;
  align?: "left" | "right";
}

/**
 * The console's one table.
 *
 * It owns the loading / error / empty branches so that no page can accidentally
 * render an empty `<tbody>` and leave the operator wondering whether the
 * request failed or the resource simply has no rows.
 */
export function DataTable<T>({
  columns,
  rows,
  loading,
  error,
  onRetry,
  empty,
  rowKey,
  caption,
}: {
  columns: Column<T>[];
  rows: T[] | undefined;
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  empty: { title: string; description?: string; action?: ReactNode };
  rowKey: (row: T) => string;
  caption?: string;
}) {
  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!rows || rows.length === 0) return <EmptyState {...empty} />;

  return (
    // The wrapper scrolls, not the page: a wide table must never push the
    // whole console sideways.
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-line bg-surface">
            {columns.map((column) => (
              <th
                key={column.header}
                scope="col"
                style={column.width ? { width: column.width } : undefined}
                className={`px-4 py-2 text-xs font-medium uppercase tracking-wide text-ink-tertiary ${
                  column.align === "right" ? "text-right" : "text-left"
                }`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className="border-b border-line-soft last:border-0 transition-colors hover:bg-surface"
            >
              {columns.map((column) => (
                <td
                  key={column.header}
                  className={`px-4 py-2.5 align-middle ${
                    column.align === "right" ? "text-right" : "text-left"
                  }`}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

"use client";

import { useState } from "react";
import type { ReactNode } from "react";

import type { MetricSeries, TimeseriesPoint } from "@/lib/types";

/*
 * The dashboard's charts.
 *
 * Every one of them answers a magnitude question -- how many per day, how many
 * of each kind -- so every one of them is a bar. There are no pies, no dual
 * axes, and no colour-coded series: each chart carries a single measure, named
 * by its own title, so colour never has to stand in for a label.
 *
 * Bars are plain elements rather than SVG paths. A flex row of divs reflows at
 * any width without the rounded ends distorting the way a non-uniformly scaled
 * viewBox would, and it makes each bar a real hover target.
 */

function formatDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** `+40%`, `-12%`, or `—` when the previous window was empty. */
function deltaLabel(current: number, previous: number): string | null {
  if (previous === 0) return current === 0 ? null : "New";
  const change = Math.round(((current - previous) / previous) * 100);
  if (change === 0) return "No change";
  return `${change > 0 ? "+" : ""}${change}%`;
}

/**
 * Daily counts for one resource: a hero number, its change against the previous
 * window, and the bars behind it.
 *
 * The hero number is the point of the card; the bars exist to say whether it
 * arrived steadily or in one burst.
 */
export function ActivityCard({
  label,
  series,
  days,
  unit,
}: {
  label: string;
  series: MetricSeries;
  days: number;
  unit: string;
}) {
  const delta = deltaLabel(series.current, series.previous);
  const rising = series.current >= series.previous;

  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-ink-tertiary">
          {label}
        </div>
        {delta && (
          <span
            className={`shrink-0 rounded-full border px-1.5 py-[1px] text-[11px] font-medium ${
              series.current === series.previous
                ? "border-line bg-muted-soft text-ink-secondary"
                : rising
                  ? "border-ok/20 bg-ok-soft text-ok"
                  : "border-warn/20 bg-warn-soft text-warn"
            }`}
            title={`${series.previous} in the previous ${days} days`}
          >
            {delta}
          </span>
        )}
      </div>

      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="text-3xl font-medium tabular-nums text-ink">
          {series.current}
        </span>
        <span className="text-xs text-ink-secondary">
          in {days} days
        </span>
      </div>

      <DailyBars points={series.points} unit={unit} />
    </div>
  );
}

/**
 * The bars themselves, with a hover read-out.
 *
 * A chart nobody can interrogate is a decoration: pointing at any day names the
 * day and its count, which is the only way to read an exact value off bars this
 * thin. The same text is on each bar's `title`, so a touch device and a screen
 * reader get it too.
 */
export function DailyBars({
  points,
  unit,
  height = 56,
}: {
  points: TimeseriesPoint[];
  unit: string;
  height?: number;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const max = Math.max(...points.map((point) => point.value), 0);
  const active = hovered === null ? null : points[hovered];

  return (
    <div className="mt-3">
      <div className="relative">
        {active && (
          <div
            className="pointer-events-none absolute -top-1 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-line bg-panel px-2 py-1 text-xs shadow-md"
            style={{
              left: `${((hovered! + 0.5) / points.length) * 100}%`,
            }}
          >
            <span className="font-medium text-ink">{active.value}</span>{" "}
            <span className="text-ink-secondary">
              {unit} · {formatDay(active.date)}
            </span>
          </div>
        )}

        <div
          className="flex items-end gap-px"
          style={{ height }}
          onMouseLeave={() => setHovered(null)}
        >
          {points.map((point, index) => {
            // Zero days still get a hairline, so the axis reads as "nothing
            // happened" rather than as a gap in the data.
            const ratio = max === 0 ? 0 : point.value / max;
            return (
              <div
                key={point.date}
                onMouseEnter={() => setHovered(index)}
                title={`${point.value} ${unit} · ${formatDay(point.date)}`}
                className="flex h-full flex-1 cursor-default items-end"
              >
                <div
                  className={`w-full rounded-t-[2px] transition-colors ${
                    point.value === 0
                      ? "bg-line"
                      : hovered === index
                        ? "bg-accent"
                        : "bg-brand"
                  }`}
                  style={{
                    height: point.value === 0 ? 1 : `${Math.max(ratio * 100, 6)}%`,
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-1.5 flex justify-between text-[11px] text-ink-tertiary">
        <span>{points.length > 0 ? formatDay(points[0].date) : ""}</span>
        <span>
          {points.length > 0 ? formatDay(points[points.length - 1].date) : ""}
        </span>
      </div>
    </div>
  );
}

/**
 * A labelled horizontal bar per category: the type mix and the status mix.
 *
 * Every row carries its name and its count as text, so the bar is a second
 * encoding of something already readable rather than the only way to tell two
 * categories apart.
 */
export function BreakdownBars({
  rows,
  total,
  emptyLabel,
}: {
  rows: { key: string; label: ReactNode; value: number; tone: string }[];
  total: number;
  emptyLabel: string;
}) {
  if (total === 0) {
    return (
      <p className="px-4 py-10 text-center text-sm text-ink-secondary">
        {emptyLabel}
      </p>
    );
  }

  return (
    <div className="space-y-3 p-4">
      {rows.map((row) => {
        const percent = total ? Math.round((row.value / total) * 100) : 0;
        return (
          <div key={row.key}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
              {row.label}
              <span className="tabular-nums text-ink-secondary">
                {row.value}{" "}
                <span className="text-ink-tertiary">({percent}%)</span>
              </span>
            </div>
            <div
              className="h-1.5 overflow-hidden rounded-full bg-muted-soft"
              role="img"
              aria-label={`${row.value} of ${total} (${percent}%)`}
            >
              <div
                className={`h-full rounded-full ${row.tone}`}
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The compact metric tile of the overview strip: an icon plate, a small-caps
 * label, the number, and one line of context under it. No chart -- a single
 * current value is a number, not a plot.
 */
export function MetricTile({
  icon,
  label,
  value,
  caption,
  href,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  caption?: ReactNode;
  href?: string;
}) {
  const body = (
    <>
      <span className="grid size-9 shrink-0 place-items-center rounded-md border border-line bg-surface text-ink-secondary">
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-[11px] font-medium uppercase tracking-wide text-ink-tertiary">
          {label}
        </span>
        <span className="block truncate text-lg font-medium tabular-nums text-ink">
          {value}
        </span>
        {caption && (
          <span className="block truncate text-xs text-ink-secondary">
            {caption}
          </span>
        )}
      </span>
    </>
  );

  const className =
    "flex items-center gap-3 rounded-lg border border-line bg-panel px-3.5 py-3 transition-colors";

  if (!href) return <div className={className}>{body}</div>;
  return (
    <a href={href} className={`${className} hover:bg-surface`}>
      {body}
    </a>
  );
}

"use client";

import { useState } from "react";

/**
 * A code sample in two languages.
 *
 * Python and JavaScript sit in one component rather than in two blocks stacked
 * down the page, because a reader wants one of them and scrolling past the
 * other is noise. The choice is per-block on purpose: a shared "language mode"
 * for the whole site sounds tidier but means a reader who lands mid-page from a
 * search result sees the wrong language until they find the switch.
 */

export type Sample = { python: string; js: string; curl?: string };

const LANGUAGES = [
  ["python", "Python"],
  ["js", "JavaScript"],
  ["curl", "cURL"],
] as const;

type Language = (typeof LANGUAGES)[number][0];

export function CodeTabs({
  sample,
  caption,
}: {
  sample: Sample;
  caption?: string;
}) {
  const available = LANGUAGES.filter(([key]) => sample[key]);
  const [language, setLanguage] = useState<Language>(available[0][0]);
  const [copied, setCopied] = useState(false);

  const source = sample[language] ?? "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission).
      // The code is on screen and selectable, so there is nothing to recover.
    }
  }

  return (
    <figure className="my-4 overflow-hidden rounded-lg border border-line">
      <div className="flex items-center justify-between gap-2 border-b border-line bg-surface px-2 py-1.5">
        <div role="tablist" aria-label="Language" className="flex gap-1">
          {available.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={language === key}
              onClick={() => setLanguage(key)}
              className={`rounded-[4px] px-2.5 py-1 text-xs font-medium transition-colors ${
                language === key
                  ? "bg-panel text-ink shadow-sm"
                  : "text-ink-secondary hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={copy}
          className="rounded-[4px] px-2 py-1 text-xs font-medium text-ink-secondary transition-colors hover:bg-surface-strong hover:text-ink"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      <pre className="overflow-x-auto bg-panel p-4 text-[13px] leading-relaxed">
        <code className="font-mono text-ink">{source}</code>
      </pre>

      {caption && (
        <figcaption className="border-t border-line bg-surface px-4 py-2 text-xs text-ink-secondary">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}

/** A single response body, with no language choice to make. */
export function ResponseBlock({
  json,
  status = 200,
}: {
  json: string;
  status?: number;
}) {
  return (
    <figure className="my-4 overflow-hidden rounded-lg border border-line">
      <figcaption className="flex items-center gap-2 border-b border-line bg-surface px-3 py-1.5 text-xs">
        <span
          className={`rounded-full border px-1.5 py-[1px] font-medium ${
            status < 300
              ? "border-ok/20 bg-ok-soft text-ok"
              : "border-danger/20 bg-danger-soft text-danger"
          }`}
        >
          {status}
        </span>
        <span className="text-ink-secondary">Response</span>
      </figcaption>
      <pre className="overflow-x-auto bg-panel p-4 text-[13px] leading-relaxed">
        <code className="font-mono text-ink">{json}</code>
      </pre>
    </figure>
  );
}

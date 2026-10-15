"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "memora.theme";

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/**
 * Sun/moon button for the top bar. The theme itself is applied by the inline
 * script in app/layout.tsx before first paint; this only flips it afterwards,
 * remembers the choice, and follows the system setting until a choice is made.
 */
export function ThemeToggle() {
  // Unknown on the server and first client render, so SSR and hydration agree.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(currentTheme());

    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemChange = (event: MediaQueryListEvent) => {
      try {
        if (window.localStorage.getItem(STORAGE_KEY)) return; // an explicit choice wins
      } catch {
        // storage blocked: fall through and follow the system
      }
      const next: Theme = event.matches ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      setTheme(next);
    };
    query.addEventListener("change", onSystemChange);
    return () => query.removeEventListener("change", onSystemChange);
  }, []);

  function toggle() {
    const next: Theme = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The switch still applies for this visit.
    }
  }

  const dark = theme === "dark";
  const label = dark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      className="grid size-8 place-items-center rounded-md text-ink-secondary transition-colors hover:bg-topbar-hover hover:text-ink"
    >
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="size-[17px]"
      >
        {theme === null ? null : dark ? (
          // sun: shown in dark mode, the click goes to light
          <path d="M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
        ) : (
          // moon
          <path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7Z" />
        )}
      </svg>
    </button>
  );
}

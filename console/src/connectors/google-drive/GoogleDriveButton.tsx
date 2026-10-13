"use client";

/**
 * "Connect Google Drive": navigates the browser to the backend's
 * /gdrive/connect, which 307-redirects to Google's consent screen.
 *
 * This must be a real browser navigation, not a fetch(): fetch follows the
 * redirect and then fails on Google's cross-origin response, which is what
 * produced the misleading "Cannot reach the Memora API" error. A navigation
 * sends the session cookie automatically and lets the callback bring the
 * browser back to /gdrive?connected=1.
 */

const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export function GoogleDriveButton() {
  function connect() {
    window.location.href = `${API_BASE_URL}/api/v1/gdrive/connect`;
  }

  return (
    <button
      type="button"
      onClick={connect}
      className="inline-flex items-center gap-2 rounded-md bg-brand px-3 py-1.5 text-sm font-medium text-brand-ink hover:opacity-90"
    >
      Connect Google Drive
    </button>
  );
}
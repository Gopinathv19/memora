"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

import { InlineError } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";

// Google is the only way into the console. The first sign-in creates the
// account; an account from the old email/password login is linked by email.
export default function LoginPage() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const googleSlot = useRef<HTMLDivElement>(null);

  // A full reload rather than router.push: the shell reads the session once on
  // mount, and this is precisely the moment that answer changes.
  const finish = useCallback(() => {
    window.location.href = "/";
  }, []);

  const handleGoogle = useCallback(
    async (response: { credential: string }) => {
      setError(null);
      setBusy(true);
      try {
        await api.auth.google(response.credential);
        finish();
      } catch (cause) {
        setError(
          cause instanceof ApiError ? cause.message : "Google sign-in failed",
        );
        setBusy(false);
      }
    },
    [finish],
  );

  const initGoogle = useCallback(() => {
    const google = (window as unknown as { google?: any }).google;
    if (!GOOGLE_CLIENT_ID || !google || !googleSlot.current) return;
    google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleGoogle,
    });
    google.accounts.id.renderButton(googleSlot.current, {
      theme: "outline",
      size: "large",
      text: "continue_with",
      width: 320,
    });
  }, [handleGoogle]);

  // Covers the case where the script was already cached and onReady never fires.
  useEffect(() => {
    initGoogle();
  }, [initGoogle]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4">
      {GOOGLE_CLIENT_ID && (
        <Script src="https://accounts.google.com/gsi/client" onReady={initGoogle} />
      )}

      <div className="w-full max-w-sm rounded-lg border border-line bg-panel p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-2 font-medium text-ink">
          <span
            aria-hidden
            className="grid size-6 place-items-center rounded-md bg-brand text-[11px] font-bold text-brand-ink"
          >
            M
          </span>
          Memora Console
        </div>

        <h1 className="mb-1 text-lg font-medium text-ink">Sign in</h1>
        <p className="mb-5 text-sm text-ink-secondary">
          Use your Google account. The first sign-in creates your account.
        </p>

        {GOOGLE_CLIENT_ID ? (
          <div
            ref={googleSlot}
            aria-busy={busy}
            className={`flex justify-center ${busy ? "pointer-events-none opacity-60" : ""}`}
          />
        ) : (
          <InlineError message="Google sign-in is not configured. Set NEXT_PUBLIC_GOOGLE_CLIENT_ID in console/.env.local and restart the console." />
        )}

        {busy && (
          <p className="mt-3 text-center text-sm text-ink-secondary">Signing in…</p>
        )}
        {error && (
          <div className="mt-3">
            <InlineError message={error} />
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

import { Field, TextInput } from "@/components/form";
import { Button, InlineError } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";

type Mode = "login" | "signup";

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const googleSlot = useRef<HTMLDivElement>(null);

  // A full reload rather than router.push: the shell reads the session once on
  // mount, and this is precisely the moment that answer changes.
  const finish = useCallback(() => {
    window.location.href = "/";
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await api.auth.login({ email, password });
      } else {
        await api.auth.signup({ email, password, name });
      }
      finish();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong");
      setBusy(false);
    }
  }

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

        <h1 className="mb-4 text-lg font-medium text-ink">
          {mode === "login" ? "Sign in" : "Create an account"}
        </h1>

        <form onSubmit={submit} className="flex flex-col gap-3">
          {mode === "signup" && (
            <Field label="Name">
              <TextInput
                value={name}
                onChange={setName}
                placeholder="Your name"
                disabled={busy}
              />
            </Field>
          )}

          <Field label="Email" required>
            <TextInput
              type="email"
              value={email}
              onChange={setEmail}
              placeholder="you@example.com"
              required
              disabled={busy}
            />
          </Field>

          <Field
            label="Password"
            required
            hint={mode === "signup" ? "At least 8 characters" : undefined}
          >
            <TextInput
              type="password"
              value={password}
              onChange={setPassword}
              required
              disabled={busy}
            />
          </Field>

          {error && <InlineError message={error} />}

          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? "Working..." : mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>

        {GOOGLE_CLIENT_ID && (
          <>
            <div className="my-4 flex items-center gap-3 text-xs text-ink-tertiary">
              <span className="h-px flex-1 bg-line" />
              or
              <span className="h-px flex-1 bg-line" />
            </div>
            <div ref={googleSlot} className="flex justify-center" />
          </>
        )}

        <p className="mt-5 text-center text-sm text-ink-secondary">
          {mode === "login" ? "No account yet?" : "Already have an account?"}{" "}
          <button
            type="button"
            className="font-medium text-ink hover:underline"
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setError(null);
            }}
          >
            {mode === "login" ? "Create one" : "Sign in"}
          </button>
        </p>
      </div>
    </div>
  );
}

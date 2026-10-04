import type { NextConfig } from "next";

// Where the Memora backend runs, e.g. https://memora-yy33.onrender.com.
// Server-side only (no NEXT_PUBLIC_): it is read when the console is built,
// never sent to the browser.
//
// When set, the console forwards /api/v1/* to the backend, so the browser only
// ever talks to the console's own domain. That makes the session cookie a
// first-party cookie: a console on vercel.app and an API on onrender.com are
// different sites, and browsers (Brave, Safari, and SameSite=Lax everywhere)
// drop a cookie that comes from a different site, so sign-in "succeeds" but
// every request after it is 401.
const API_PROXY_TARGET = (process.env.API_PROXY_TARGET ?? "").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Next 16 rejects dev requests — including the HMR websocket — from any
  // origin it does not recognize, answering with a bare `Unauthorized`.
  // Because the HMR client is part of the Turbopack dev runtime, that refusal
  // stops hydration entirely: the page renders its server HTML, no effect ever
  // runs, and every panel sits on "Loading…" forever with nothing in the
  // terminal to explain why.
  //
  // `localhost` and `127.0.0.1` are different origins to a browser, so opening
  // the console on the second one triggers exactly that. Listing both makes
  // either spelling work. This affects `next dev` only.
  allowedDevOrigins: ["localhost", "127.0.0.1"],

  async rewrites() {
    if (!API_PROXY_TARGET) return [];
    return [
      {
        source: "/api/v1/:path*",
        destination: `${API_PROXY_TARGET}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

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
};

export default nextConfig;

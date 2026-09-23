import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,

  // Next 16 refuses dev requests from origins it does not recognize, including
  // the HMR websocket, which leaves the page rendered but never hydrated.
  // `localhost` and `127.0.0.1` are different origins to a browser, so both
  // spellings are listed. Affects `next dev` only.
  allowedDevOrigins: ["localhost", "127.0.0.1"],
};

export default nextConfig;

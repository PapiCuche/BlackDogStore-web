import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // No rewrites needed — /api/* is handled by app/api/[...path]/route.ts (Route Handler proxy).
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
      { protocol: "http", hostname: "localhost" },
      { protocol: "http", hostname: "127.0.0.1" },
    ],
  },
};

export default nextConfig;

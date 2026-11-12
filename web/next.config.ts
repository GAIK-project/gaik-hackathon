import type { NextConfig } from "next";

// Static export (docs/ARCHITECTURE.md section 13): FastAPI serves `out/` at "/".
// No cacheComponents or partialPrefetching: both break `output: "export"`.
// No dynamic route with run-time ids: use a query string (/soita?raportti=<id>).
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";
// Where server-side code reaches the API. In production CloudFront sends
// /api/* straight to the API service, so the rewrite below only matters locally.
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https://avatars.githubusercontent.com",
  "font-src 'self'",
  `connect-src 'self'${isDev ? " ws:" : ""}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self' https://github.com",
  "object-src 'none'",
].join("; ");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
  async headers() {
    // Signed-in pages carry private data: never let a CDN or shared cache keep them.
    const privatePage = [{ key: "Cache-Control", value: "private, no-store" }];
    return [
      { source: "/app", headers: privatePage },
      { source: "/app/:path*", headers: privatePage },
      { source: "/signin", headers: privatePage },
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), interest-cohort=()" },
        ],
      },
    ];
  },
};

export default nextConfig;

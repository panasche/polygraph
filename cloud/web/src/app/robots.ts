import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  const base = process.env.PUBLIC_BASE_URL || "https://polygraphmcp.dev";
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/app/", "/api/", "/signin"] }],
    sitemap: `${base}/sitemap.xml`,
  };
}

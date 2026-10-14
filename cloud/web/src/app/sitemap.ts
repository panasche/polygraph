import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = process.env.PUBLIC_BASE_URL || "https://polygraphmcp.dev";
  return ["", "/docs", "/benchmark", "/recipes", "/privacy", "/terms", "/security"].map((p) => ({
    url: `${base}${p}`,
    changeFrequency: p === "/benchmark" ? "weekly" : "monthly",
  }));
}

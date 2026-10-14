import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
import "./globals.css";

const sans = Schibsted_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

const siteUrl = process.env.PUBLIC_BASE_URL || "https://polygraphmcp.dev";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Polygraph for MCP: catch your AI agent lying about its tools",
    template: "%s · Polygraph for MCP",
  },
  description:
    "Polygraph breaks your AI agent's MCP tools on purpose, inside pytest, and checks what the agent said against what really happened. Free and open source.",
  applicationName: "Polygraph for MCP",
  openGraph: {
    type: "website",
    siteName: "Polygraph for MCP",
    title: "Your agent says it's done. Polygraph checks.",
    description: "Break your MCP tools on purpose, inside pytest. Free and open source.",
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f2f5f3" },
    { media: "(prefers-color-scheme: dark)", color: "#131a1e" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}

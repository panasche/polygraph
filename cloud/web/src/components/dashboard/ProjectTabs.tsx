"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import s from "./dashboard.module.css";

export function ProjectTabs({ base, isAdmin }: { base: string; isAdmin: boolean }) {
  const path = usePathname();
  const tabs = [
    { href: base, label: "Overview", match: (p: string) => p === base },
    { href: `${base}/reports`, label: "Reports", match: (p: string) => p.startsWith(`${base}/reports`) },
    { href: `${base}/compare`, label: "Compare", match: (p: string) => p.startsWith(`${base}/compare`) },
    ...(isAdmin
      ? [{ href: `${base}/settings`, label: "Settings", match: (p: string) => p.startsWith(`${base}/settings`) }]
      : []),
  ];
  return (
    <nav aria-label="Project" className={s.tabs}>
      {tabs.map((t) => {
        const active = t.match(decodeURIComponent(path));
        return (
          <Link key={t.href} href={t.href} className={s.tab} aria-current={active ? "page" : undefined}>
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}

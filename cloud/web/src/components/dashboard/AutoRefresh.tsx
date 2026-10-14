"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-render the server component every few seconds while a report is processing. */
export function AutoRefresh({ everyMs = 4000, maxTries = 45 }: { everyMs?: number; maxTries?: number }) {
  const router = useRouter();
  useEffect(() => {
    let tries = 0;
    const t = window.setInterval(() => {
      tries += 1;
      if (tries > maxTries) window.clearInterval(t);
      else router.refresh();
    }, everyMs);
    return () => window.clearInterval(t);
  }, [router, everyMs, maxTries]);
  return null;
}

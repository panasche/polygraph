"use client";

import { useEffect, useState } from "react";
import { dateTime, relativeTime } from "@/lib/format";

/** Renders an absolute IST time on the server, then a relative one in the browser. */
export function RelTime({ iso }: { iso: string | null }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const t = window.setInterval(tick, 60_000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(t);
    };
  }, []);
  if (!iso) return <span>never</span>;
  return (
    <time dateTime={iso} title={`${dateTime(iso)} IST`}>
      {now === null ? dateTime(iso) : relativeTime(iso, now)}
    </time>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiSend } from "@/lib/api-client";
import s from "./dashboard.module.css";

export function UserMenu({ login, avatarUrl }: { login: string; avatarUrl: string | null }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function signOut() {
    setBusy(true);
    try {
      await apiSend("POST", "/auth/logout");
    } finally {
      // A full reload drops any private page data the client router has cached.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
    }
  }

  return (
    <div className={s.userMenu} ref={ref}>
      <button
        type="button"
        className={s.userButton}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((o) => !o)}
      >
        {avatarUrl ? (
          // Avatars come from GitHub's CDN; next/image would proxy them for no benefit.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={avatarUrl} alt="" width={24} height={24} className={s.avatar} />
        ) : (
          <span className={s.avatarFallback} aria-hidden="true">
            {login.slice(0, 1).toUpperCase()}
          </span>
        )}
        <span>{login}</span>
      </button>
      {open ? (
        <div className={s.menu} role="menu">
          <Link href="/app/account" role="menuitem" onClick={() => setOpen(false)}>
            Account and data
          </Link>
          <button type="button" role="menuitem" onClick={signOut} disabled={busy}>
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

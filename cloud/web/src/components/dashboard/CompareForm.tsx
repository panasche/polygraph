"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Button } from "@/components/ui/Button";
import ui from "@/components/ui/ui.module.css";
import s from "./dashboard.module.css";

type Option = { value: string; label: string };

export function CompareForm({
  baseOptions,
  headOptions,
  base,
  head,
}: {
  baseOptions: Option[];
  headOptions: Option[];
  base: string;
  head: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [b, setB] = useState(base);
  const [h, setH] = useState(head);
  const [pending, start] = useTransition();

  return (
    <form
      className={s.compareForm}
      onSubmit={(e) => {
        e.preventDefault();
        const q = new URLSearchParams({ base: b, head: h });
        start(() => router.push(`${pathname}?${q.toString()}`));
      }}
    >
      <label className={ui.field}>
        <span className={ui.label}>Base</span>
        <select className={ui.select} value={b} onChange={(e) => setB(e.target.value)}>
          {baseOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className={ui.field}>
        <span className={ui.label}>Compare</span>
        <select className={ui.select} value={h} onChange={(e) => setH(e.target.value)}>
          {headOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <Button type="submit" variant="secondary" disabled={pending || !b || !h}>
        {pending ? "Comparing…" : "Compare"}
      </Button>
    </form>
  );
}

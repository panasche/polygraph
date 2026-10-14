"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";
import ui from "@/components/ui/ui.module.css";
import { OUTCOME_LABEL, OUTCOMES } from "@/lib/format";
import s from "./dashboard.module.css";

export function RunFilters({ tools, faults }: { tools: string[]; faults: string[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [pending, start] = useTransition();

  function set(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    start(() => router.replace(`${pathname}?${next.toString()}#runs`, { scroll: false }));
  }

  const select = (key: string, label: string, options: { value: string; label: string }[]) => (
    <label className={s.filter}>
      <span className={s.faint}>{label}</span>
      <select className={ui.select} value={params.get(key) ?? ""} onChange={(e) => set(key, e.target.value)}>
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className={s.filters} aria-busy={pending}>
      {select(
        "outcome",
        "Outcome",
        OUTCOMES.map((o) => ({ value: o, label: OUTCOME_LABEL[o] })),
      )}
      {select(
        "tool",
        "Tool",
        tools.map((t) => ({ value: t, label: t })),
      )}
      {select(
        "fault",
        "Fault",
        [{ value: "none", label: "No fault (control)" }, ...faults.map((f) => ({ value: f, label: f }))],
      )}
    </div>
  );
}

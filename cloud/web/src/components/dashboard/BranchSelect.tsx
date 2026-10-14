"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";
import type { Branch } from "@/lib/types";
import ui from "@/components/ui/ui.module.css";
import s from "./dashboard.module.css";

export function BranchSelect({ branches, value }: { branches: Branch[]; value: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [pending, start] = useTransition();
  const names = branches.some((b) => b.name === value) ? branches : [{ name: value, last_report_at: null, reports: 0 }, ...branches];

  return (
    <label className={s.branchSelect}>
      <span className={s.faint}>Branch</span>
      <select
        className={ui.select}
        value={value}
        aria-busy={pending}
        onChange={(e) => {
          const next = new URLSearchParams(params.toString());
          if (e.target.value) next.set("branch", e.target.value);
          else next.delete("branch");
          next.delete("cursor");
          start(() => router.push(`${pathname}?${next.toString()}`));
        }}
      >
        {names.map((b) => (
          <option key={b.name} value={b.name}>
            {b.name === "" ? "All branches" : b.name}
            {b.reports ? ` (${b.reports})` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

import Link from "next/link";
import s from "./ui.module.css";

/** Three pen traces, the bottom one flat where the others move: the product in one glyph. */
export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none">
      <path d="M2 6h6l2-3 2 3h10" stroke="var(--blue)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path
        d="M2 12h6l2-3 2 3 2-3 2 3h6"
        stroke="var(--ink)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M2 18h20" stroke="var(--crimson)" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function Logo({ href = "/", sub = true }: { href?: string; sub?: boolean }) {
  return (
    <Link href={href} className={s.logo} aria-label="Polygraph for MCP, home">
      <LogoMark />
      <span>
        Polygraph{sub ? <span className={s.logoSub}> for MCP</span> : null}
      </span>
    </Link>
  );
}

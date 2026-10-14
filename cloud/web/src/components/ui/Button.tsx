import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import s from "./ui.module.css";

type Variant = "primary" | "secondary" | "quiet" | "danger";

function cls(variant: Variant, small?: boolean, extra?: string) {
  return [s.button, s[variant], small ? s.small : "", extra ?? ""].filter(Boolean).join(" ");
}

export function Button({
  variant = "secondary",
  small,
  className,
  type = "button",
  ...rest
}: ComponentProps<"button"> & { variant?: Variant; small?: boolean }) {
  return <button type={type} className={cls(variant, small, className)} {...rest} />;
}

export function ButtonLink({
  variant = "secondary",
  small,
  className,
  href,
  children,
  external,
  ...rest
}: Omit<ComponentProps<"a">, "href"> & {
  href: string;
  variant?: Variant;
  small?: boolean;
  external?: boolean;
  children: ReactNode;
}) {
  if (external || href.startsWith("/api/")) {
    // API routes and other origins are full page loads, not client navigations.
    return (
      <a href={href} className={cls(variant, small, className)} {...rest}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={cls(variant, small, className)} {...rest}>
      {children}
    </Link>
  );
}

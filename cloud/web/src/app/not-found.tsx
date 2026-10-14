import Link from "next/link";
import { Logo } from "@/components/ui/Logo";
import s from "./status.module.css";

export default function NotFound() {
  return (
    <main id="main" className={`page ${s.wrap}`}>
      <Logo />
      <h1 className={s.title}>Nothing here</h1>
      <p className={s.text}>
        This page doesn&apos;t exist, or it belongs to a private project you can&apos;t see. Private and unknown
        projects look the same on purpose.
      </p>
      <p className={s.links}>
        <Link href="/app">Your projects</Link>
        <Link href="/">Home</Link>
      </p>
    </main>
  );
}

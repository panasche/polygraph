"use client";

import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { STATUS_URL } from "@/lib/site";
import s from "./status.module.css";

export default function RootError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main" className={`page ${s.wrap}`}>
      <h1 className={s.title}>This page didn&apos;t load</h1>
      <p className={s.text}>
        {error.message.includes("can't be reached")
          ? "The Polygraph API can't be reached right now. Your data is safe; try again in a moment."
          : "Something went wrong on our side. Try again, and if it keeps happening, check the status page."}
      </p>
      {error.digest ? <p className={s.digest}>Reference {error.digest}</p> : null}
      <p className={s.links}>
        <Button variant="secondary" onClick={reset}>
          Try again
        </Button>
        {STATUS_URL ? <a href={STATUS_URL}>Status page</a> : null}
        <Link href="/">Home</Link>
      </p>
    </main>
  );
}

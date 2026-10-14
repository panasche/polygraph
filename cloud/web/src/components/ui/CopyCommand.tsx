"use client";

import { useState } from "react";
import s from "./ui.module.css";

export function CopyCommand({ command, prompt = true }: { command: string; prompt?: boolean }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className={s.copy}>
      <code className={s.copyText}>
        {prompt ? <span className={s.copyPrompt}>$</span> : null}
        {command}
      </code>
      <button type="button" className={s.copyButton} onClick={copy} aria-label={`Copy ${command}`}>
        {copied ? "Copied" : "Copy"}
      </button>
      <span className="visually-hidden" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </div>
  );
}

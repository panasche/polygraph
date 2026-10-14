"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import ui from "@/components/ui/ui.module.css";
import { apiSend, ClientApiError } from "@/lib/api-client";
import type { Project } from "@/lib/types";
import s from "./dashboard.module.css";

type Key = "public_results" | "allow_text_upload";

export function ProjectToggles({ project, apiPath }: { project: Project; apiPath: string }) {
  const router = useRouter();
  const [state, setState] = useState({
    public_results: project.public_results,
    allow_text_upload: project.allow_text_upload,
  });
  const [saving, setSaving] = useState<Key | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  async function toggle(key: Key, value: boolean) {
    const prev = state[key];
    setState((st) => ({ ...st, [key]: value }));
    setSaving(key);
    setMessage(null);
    try {
      await apiSend("PATCH", apiPath, { [key]: value });
      setMessage({
        kind: "ok",
        text:
          key === "public_results"
            ? value
              ? "Results are now public."
              : "Results are now hidden from people outside the repo."
            : value
              ? "Text uploads are on. Your mask still runs before every upload."
              : "Text uploads are off. New uploads with text will be rejected.",
      });
      router.refresh();
    } catch (e) {
      setState((st) => ({ ...st, [key]: prev }));
      setMessage({ kind: "error", text: e instanceof ClientApiError ? e.message : "The change wasn't saved." });
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className={s.toggles}>
      <label className={ui.switch}>
        <input
          type="checkbox"
          role="switch"
          className={ui.switchInput}
          checked={state.public_results && !project.is_private}
          disabled={project.is_private || saving !== null}
          onChange={(e) => toggle("public_results", e.target.checked)}
        />
        <span className={ui.label}>Public results</span>
        <span className={ui.hint}>
          {project.is_private
            ? "This repo is private on GitHub, so its results are only visible to people who can see the repo."
            : "Anyone can see reports, trends and the badge. Turn this off to limit them to people with access to the repo."}
        </span>
      </label>

      <label className={ui.switch}>
        <input
          type="checkbox"
          role="switch"
          className={ui.switchInput}
          checked={state.allow_text_upload}
          disabled={saving !== null}
          onChange={(e) => toggle("allow_text_upload", e.target.checked)}
        />
        <span className={ui.label}>Accept payload text</span>
        <span className={ui.hint}>
          Off by default. When on, uploads may include the text of tool calls and answers so the ledger view shows
          it. Your <code>mask</code> function runs on your machine first and nothing uploads if it fails. The server
          also scans for personal data, like emails, phone numbers, PAN and Aadhaar numbers, and rejects any upload
          that contains some.
        </span>
      </label>

      <p className={s.formStatus} role="status" aria-live="polite">
        {message ? <span className={message.kind === "error" ? s.textBad : s.textGood}>{message.text}</span> : null}
      </p>
    </div>
  );
}

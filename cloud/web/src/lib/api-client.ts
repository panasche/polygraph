"use client";

import type { ApiErrorBody } from "./types";

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)pg_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export class ClientApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Mutating request from the browser. Sends the CSRF double-submit header. */
export async function apiSend<T = unknown>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "x-csrf-token": csrfToken(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let err: ApiErrorBody | null = null;
    try {
      err = (await res.json()) as ApiErrorBody;
    } catch {
      /* empty */
    }
    throw new ClientApiError(
      res.status,
      err?.error.code ?? "internal",
      err?.error.message ?? `The request failed with status ${res.status}.`,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

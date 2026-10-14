import "server-only";
import { cookies } from "next/headers";
import type { ApiErrorBody } from "./types";

const API_ORIGIN = process.env.API_ORIGIN || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

/**
 * GET a JSON resource from the API on behalf of the current visitor.
 * Forwards only the session cookie. Never cached: the result can be private.
 */
export async function apiGet<T>(path: string): Promise<T> {
  const jar = await cookies();
  const session = jar.get("pg_session")?.value;
  let res: Response;
  try {
    res = await fetch(`${API_ORIGIN}/api${path}`, {
      headers: {
        accept: "application/json",
        ...(session ? { cookie: `pg_session=${session}` } : {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(503, "unavailable", "The Polygraph API can't be reached right now.");
  }
  if (!res.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, body?.error.code ?? "internal", body?.error.message ?? res.statusText);
  }
  return (await res.json()) as T;
}

/** Same as apiGet, but returns null for 401/403/404 instead of throwing. */
export async function apiGetOrNull<T>(path: string): Promise<T | null> {
  try {
    return await apiGet<T>(path);
  } catch (e) {
    if (e instanceof ApiError && [401, 403, 404].includes(e.status)) return null;
    throw e;
  }
}

/** Public, cookie-free GET for marketing pages. Returns null on any failure. */
export async function apiPublic<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_ORIGIN}/api${path}`, { headers: { accept: "application/json" } });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

import "server-only";
import { notFound, redirect } from "next/navigation";
import { cache } from "react";
import { ApiError, apiGet, apiGetOrNull } from "./api-server";
import type { Project, User } from "./types";

export const PRIVACY_VERSION = process.env.PRIVACY_VERSION || "2026-10-13";

export function signinHref(next: string) {
  return `/signin?next=${encodeURIComponent(next)}`;
}

/** The signed-in user, or null. Never throws for auth failures. */
export const currentUser = cache(async (): Promise<User | null> => apiGetOrNull<User>("/me"));

/**
 * Require a signed-in user who has accepted the current privacy notice.
 * Redirects to sign-in or to the consent screen otherwise.
 */
export async function requireUser(next: string): Promise<User> {
  const user = await currentUser();
  if (!user) redirect(signinHref(next));
  if (user.consented_privacy_version !== PRIVACY_VERSION) {
    redirect(`/app/consent?next=${encodeURIComponent(next)}`);
  }
  return user;
}

/**
 * Load a project the visitor may see. Public projects need no session.
 * Unknown and private-without-access look identical (404), by design.
 */
export const loadProject = cache(async (owner: string, repo: string, next: string): Promise<Project> => {
  const path = `/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
  try {
    return await apiGet<Project>(path);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 404 || e.status === 403)) {
      const user = await currentUser();
      if (!user) redirect(signinHref(next));
      notFound();
    }
    throw e;
  }
});

export function projectBase(owner: string, repo: string) {
  return `/app/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
}

export function apiProjectPath(owner: string, repo: string) {
  return `/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
}

export function qs(params: Record<string, string | number | null | undefined>) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

export function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

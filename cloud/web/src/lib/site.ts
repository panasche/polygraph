/**
 * Site-wide settings, baked in when the image is built (most pages are prerendered). The defaults describe the
 * official deployment at polygraphmcp.dev; a self-hosted copy sets its own (see deploy/free).
 */
export const OFFICIAL_URL = "https://polygraphmcp.dev";

export const SITE_URL = process.env.PUBLIC_BASE_URL || OFFICIAL_URL;
export const IS_OFFICIAL = SITE_URL === OFFICIAL_URL;

/** The public source repository. */
export const SOURCE_URL = process.env.SOURCE_URL || "https://github.com/polygraph-dev/polygraph";

/** Where security reports go. */
export const SECURITY_EMAIL = process.env.SECURITY_EMAIL || "security@polygraphmcp.dev";
/** The published grievance contact the DPDP Act requires. */
export const GRIEVANCE_EMAIL = process.env.GRIEVANCE_EMAIL || "grievance@polygraphmcp.dev";

/**
 * Status page, or empty when there isn't one. NEXT_PUBLIC_ so the client-side error pages can read it too; unset
 * means the official one, an empty value means none.
 */
export const STATUS_URL = process.env.NEXT_PUBLIC_STATUS_URL ?? "https://status.polygraphmcp.dev";

/**
 * Extra `with:` lines for the GitHub Action when uploads go somewhere other than the official API. `indent` is the
 * indentation of the other `with:` keys in the snippet.
 */
export function uploadTarget(indent: string): string {
  if (IS_OFFICIAL) return "";
  return `\n${indent}api-url: ${SITE_URL}/api\n${indent}audience: ${SITE_URL}`;
}

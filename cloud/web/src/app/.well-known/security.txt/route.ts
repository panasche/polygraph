import { SECURITY_EMAIL, SITE_URL, SOURCE_URL } from "@/lib/site";

// RFC 9116. Update Expires at least once a year.
const BODY = `Contact: mailto:${SECURITY_EMAIL}
Contact: ${SOURCE_URL}/security/advisories/new
Expires: 2027-10-13T00:00:00.000Z
Preferred-Languages: en, hi
Canonical: ${SITE_URL}/.well-known/security.txt
Policy: ${SITE_URL}/security
`;

export function GET() {
  return new Response(BODY, {
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "public, max-age=86400",
    },
  });
}

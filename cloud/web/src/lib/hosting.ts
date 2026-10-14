/**
 * Where the hosted service runs. The privacy notice, consent page, security page and footer quote this, so it must
 * match the real deployment. Set HOSTING when the image is built (pages are prerendered); an unknown value fails the
 * build rather than publishing a wrong notice.
 */
type Hosting = {
  /** Sentence fragment for the privacy notice: "Stored in ..." */
  where: string;
  /** Short form for the footer and consent page. */
  short: string;
  /** How data is encrypted at rest. */
  atRest: string;
  /** Whether a web application firewall sits in front of the service. */
  waf: boolean;
};

const PROFILES: Record<string, Hosting> = {
  "aws-mumbai": {
    where: "Amazon Web Services' Mumbai region (ap-south-1)",
    short: "AWS Mumbai (ap-south-1)",
    atRest: "with keys managed in AWS KMS",
    waf: true,
  },
  "oracle-mumbai": {
    where: "Oracle Cloud's Mumbai region (ap-mumbai-1), on a single server",
    short: "Oracle Cloud Mumbai (ap-mumbai-1)",
    atRest: "on disks that Oracle Cloud encrypts by default",
    waf: false,
  },
  "oracle-hyderabad": {
    where: "Oracle Cloud's Hyderabad region (ap-hyderabad-1), on a single server",
    short: "Oracle Cloud Hyderabad (ap-hyderabad-1)",
    atRest: "on disks that Oracle Cloud encrypts by default",
    waf: false,
  },
};

const key = process.env.HOSTING || "aws-mumbai";
const profile = PROFILES[key];
if (!profile) throw new Error(`HOSTING=${key} is not one of: ${Object.keys(PROFILES).join(", ")}`);

export const HOSTING: Hosting = profile;

import type { Metadata } from "next";
import s from "@/components/marketing/marketing.module.css";
import { HOSTING } from "@/lib/hosting";
import { GRIEVANCE_EMAIL } from "@/lib/site";

export const metadata: Metadata = { title: "Privacy notice" };

// Before launch: have this notice reviewed by a lawyer familiar with the DPDP Act 2023
// and the DPDP Rules, and fill in the operator's legal name and postal address.
const VERSION = process.env.PRIVACY_VERSION || "2026-10-13";

export default function PrivacyPage() {
  return (
    <article className={`page ${s.prose}`}>
      <h1>Privacy notice</h1>
      <p className={s.proseMeta}>Version {VERSION}</p>
      <p className={s.proseLead}>
        Polygraph Cloud is a free dashboard for Polygraph test results. This notice explains what personal data it
        handles, why, and what you can do about it, under India&apos;s Digital Personal Data Protection Act, 2023.
      </p>

      <h2>Who is responsible</h2>
      <p>
        The operator of Polygraph Cloud is the Data Fiduciary for the account data described below. You can reach the
        grievance contact at <a href={`mailto:${GRIEVANCE_EMAIL}`}>{GRIEVANCE_EMAIL}</a>.
      </p>

      <h2>What we collect</h2>
      <table className={s.proseTable}>
        <thead>
          <tr>
            <th scope="col">Data</th>
            <th scope="col">Why</th>
            <th scope="col">Kept for</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>GitHub user id, username and avatar link</td>
            <td>To sign you in and show who you are</td>
            <td>Until you delete your account</td>
          </tr>
          <tr>
            <td>Which repos you can see, from the GitHub App</td>
            <td>To decide who may see private results</td>
            <td>Refreshed regularly, removed with your account</td>
          </tr>
          <tr>
            <td>The privacy notice version you accepted, and when</td>
            <td>To record your consent</td>
            <td>Until you delete your account</td>
          </tr>
          <tr>
            <td>A session cookie</td>
            <td>To keep you signed in</td>
            <td>30 days from your last visit, or until you sign out</td>
          </tr>
          <tr>
            <td>Request logs with IP addresses</td>
            <td>Security and abuse prevention</td>
            <td>Up to 90 days</td>
          </tr>
        </tbody>
      </table>
      <p>
        We never ask GitHub for your email address and never read your code. There are no analytics or advertising
        trackers on this site.
      </p>

      <h2>Test results</h2>
      <p>
        Test results belong to projects, which are GitHub repos. By default an upload contains numbers, outcome buckets,
        tool and fault names, timings and fingerprints (hashes) of payloads, not the text of tool calls or answers. A
        repo admin can choose to accept text. When they do, the uploader&apos;s own mask runs first, and our server
        rejects any upload that still looks like it contains personal data such as email addresses, phone numbers, PAN
        or Aadhaar numbers. Please don&apos;t upload personal data. Full run details are kept for 90 days, and summary
        numbers for 2 years.
      </p>

      <h2>Where it&apos;s stored</h2>
      <p>
        In {HOSTING.where}, encrypted in transit with TLS and at rest {HOSTING.atRest}. GitHub processes sign-in on our
        behalf under its own privacy terms.
      </p>

      <h2>Your rights</h2>
      <ul>
        <li>
          <strong>Access and export.</strong> Download everything tied to your account from the account page.
        </li>
        <li>
          <strong>Correction.</strong> Your username and avatar come from GitHub; change them there and they update the
          next time you sign in.
        </li>
        <li>
          <strong>Erasure.</strong> Delete a report, a project or your whole account from the dashboard at any time.
          Deletion removes the stored files as well as the database rows.
        </li>
        <li>
          <strong>Withdrawing consent.</strong> Deleting your account withdraws it. It doesn&apos;t affect what was
          lawful before.
        </li>
        <li>
          <strong>Grievances.</strong> Write to the grievance contact above. We aim to reply within 7 days and to
          resolve the matter within 30. If you&apos;re not satisfied, you may complain to the Data Protection Board of
          India.
        </li>
        <li>
          <strong>Nomination.</strong> You may nominate someone to exercise these rights for you in case of death or
          incapacity by writing to the grievance contact.
        </li>
      </ul>

      <h2>Children</h2>
      <p>Polygraph Cloud is a developer tool and isn&apos;t meant for anyone under 18.</p>

      <h2>If something goes wrong</h2>
      <p>
        If a breach affects your personal data, we&apos;ll tell you and the Data Protection Board of India without undue
        delay, as the law requires, along with what happened and what we&apos;re doing about it.
      </p>

      <h2>Changes</h2>
      <p>
        When this notice changes in a way that matters, the version date changes and you&apos;ll be asked to accept it
        again the next time you sign in.
      </p>
    </article>
  );
}

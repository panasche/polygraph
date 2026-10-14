import Link from "next/link";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { projectBase } from "@/lib/dashboard";
import { uploadTarget } from "@/lib/site";
import type { Project } from "@/lib/types";
import s from "./dashboard.module.css";

const WORKFLOW = `name: polygraph
on: [push, pull_request]

permissions:
  contents: read
  id-token: write
  pull-requests: write

jobs:
  polygraph:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: polygraph-dev/polygraph-action@v1
        with:
          upload: true${uploadTarget("          ")}`;

export function FirstUpload({ project }: { project: Project }) {
  const base = projectBase(project.owner, project.repo);
  return (
    <section className={s.firstUpload} aria-labelledby="first-title">
      <div>
        <h2 id="first-title" className={s.h2}>
          Send your first report
        </h2>
        <p className={s.lede}>
          Nothing has been uploaded for <code>{project.full_name}</code> yet. The quickest way is the GitHub Action: it
          runs your pytest suite under Polygraph and uploads the results without any secret.
        </p>
        <ol className={s.firstSteps}>
          <li>
            Install the plugin with <code>pip install pytest-polygraph</code> and point it at your MCP servers in{" "}
            <code>pyproject.toml</code>.
          </li>
          <li>
            Add the workflow on the right. <code>id-token: write</code> lets GitHub prove which repo is uploading.
          </li>
          <li>Push. The report appears here once it&apos;s processed, usually within a minute.</li>
        </ol>
        <p className={s.faint}>
          Prefer uploading from your machine?{" "}
          {project.role === "admin" ? (
            <Link href={`${base}/settings#tokens`}>Create an upload token</Link>
          ) : (
            "Ask a repo admin for an upload token"
          )}
          , set it as <code>POLYGRAPH_TOKEN</code>, then run <code>polygraph upload</code>.
        </p>
      </div>
      <CodeBlock code={WORKFLOW} lang="yaml" title=".github/workflows/polygraph.yml" />
    </section>
  );
}

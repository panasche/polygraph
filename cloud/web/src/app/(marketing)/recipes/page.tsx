import type { Metadata } from "next";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { RECIPES } from "@/lib/content";
import s from "@/components/marketing/marketing.module.css";
import r from "./recipes.module.css";

export const metadata: Metadata = {
  title: "Fix recipes",
  description: "Seven small changes that stop agents from lying, double-charging and following injected orders.",
};

const SNIPPETS: Record<string, { code: string; lang: "python" }> = {
  R1: {
    lang: "python",
    code: `key = str(uuid.uuid4())          # once per intent, before the first try
for attempt in range(3):
    result = await call("pay_invoice", {"invoice": "INV-42", "idempotency_key": key})
    if not result.is_error:
        break`,
  },
  R2: {
    lang: "python",
    code: `await call("pay_invoice", {"invoice": "INV-42", "idempotency_key": key})
invoice = await call("get_invoice", {"invoice": "INV-42"})
if invoice["status"] != "paid":
    return "I couldn't confirm the payment. Please check INV-42."`,
  },
  R6: {
    lang: "python",
    code: `pinned = {t.name: sha256(t.description + json.dumps(t.input_schema)) for t in first_list}

def check(tool):
    if sha256(tool.description + json.dumps(tool.input_schema)) != pinned.get(tool.name):
        raise ToolChanged(tool.name)   # stop and ask a human`,
  },
};

export default function RecipesPage() {
  return (
    <>
      <article className={`page ${s.prose}`}>
        <h1>Fix recipes</h1>
        <p className={s.proseLead}>
          Finding the lie is half the job. These are small, boring changes that make agents more honest. Each one
          gets a before and after number from real benchmark runs. Until those runs are published, the numbers stay
          empty rather than guessed.
        </p>
      </article>
      <div className="page">
        <ol className={r.list}>
          {RECIPES.map((rec) => (
            <li key={rec.id} id={rec.id} className={r.item}>
              <div className={r.head}>
                <span className={`${r.id} mono`}>{rec.id}</span>
                <h2>{rec.title}</h2>
              </div>
              <div className={r.body}>
                <p className={r.plain}>{rec.plain}</p>
                <p>{rec.how}</p>
                <p className={r.fixes}>
                  Helps with{" "}
                  {rec.fixes.map((f, i) => (
                    <span key={f}>
                      {i > 0 ? ", " : ""}
                      <code>{f === "all" ? "every fault" : f}</code>
                    </span>
                  ))}
                </p>
                {SNIPPETS[rec.id] ? <CodeBlock code={SNIPPETS[rec.id].code} lang={SNIPPETS[rec.id].lang} /> : null}
              </div>
              <div className={r.numbers}>
                <p className={r.numbersLabel}>Before and after</p>
                <p className={r.pending}>Waiting for published runs</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </>
  );
}

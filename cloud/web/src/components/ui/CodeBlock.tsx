import type { ReactNode } from "react";
import s from "./code.module.css";

type Lang = "python" | "toml" | "yaml" | "bash" | "text" | "markdown";

const KEYWORDS: Record<Lang, RegExp | null> = {
  python: /\b(def|return|import|from|as|assert|with|for|in|if|else|async|await|class|None|True|False)\b/g,
  toml: null,
  yaml: null,
  bash: /\b(pip|pytest|polygraph|export)\b/g,
  text: null,
  markdown: null,
};

/** Highlight one line: comments, strings, keywords, decorators. Deliberately tiny. */
function highlight(line: string, lang: Lang): ReactNode[] {
  if (lang === "text" || lang === "markdown") return [line];
  const out: ReactNode[] = [];
  const commentAt = (() => {
    let inStr: string | null = null;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (inStr) {
        if (c === inStr) inStr = null;
      } else if (c === '"' || c === "'") inStr = c;
      else if (c === "#") return i;
    }
    return -1;
  })();
  const code = commentAt >= 0 ? line.slice(0, commentAt) : line;
  const comment = commentAt >= 0 ? line.slice(commentAt) : "";

  const token = /("[^"]*"|'[^']*'|@[\w.]+|\[[\w.\-]+\](?=\s*$))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  const pushPlain = (txt: string) => {
    const kw = KEYWORDS[lang];
    if (!kw) {
      out.push(txt);
      return;
    }
    let l2 = 0;
    let m2: RegExpExecArray | null;
    kw.lastIndex = 0;
    while ((m2 = kw.exec(txt))) {
      if (m2.index > l2) out.push(txt.slice(l2, m2.index));
      out.push(
        <span key={`k${k++}`} className={s.kw}>
          {m2[0]}
        </span>,
      );
      l2 = m2.index + m2[0].length;
    }
    if (l2 < txt.length) out.push(txt.slice(l2));
  };
  while ((m = token.exec(code))) {
    if (m.index > last) pushPlain(code.slice(last, m.index));
    const t = m[0];
    const cls = t.startsWith("@") ? s.deco : t.startsWith("[") ? s.section : s.str;
    out.push(
      <span key={`t${k++}`} className={cls}>
        {t}
      </span>,
    );
    last = m.index + t.length;
  }
  if (last < code.length) pushPlain(code.slice(last));
  if (comment)
    out.push(
      <span key={`c${k++}`} className={s.comment}>
        {comment}
      </span>,
    );
  return out;
}

export function CodeBlock({
  code,
  lang = "text",
  title,
}: {
  code: string;
  lang?: Lang;
  title?: string;
}) {
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <figure className={s.block}>
      {title ? <figcaption className={s.title}>{title}</figcaption> : null}
      <pre className={s.pre} tabIndex={0}>
        <code>
          {lines.map((line, i) => (
            <span key={i} className={s.line}>
              {highlight(line, lang)}
              {"\n"}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}

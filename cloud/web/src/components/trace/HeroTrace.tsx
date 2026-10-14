import type { CSSProperties } from "react";
import s from "./HeroTrace.module.css";

/*
 * The landing page's one moving part: a three-channel chart recording of the
 * canonical failure. pay_invoice gets a lost_response, the agent retries, the
 * bank charges twice, and the agent's answer still reads as a success.
 *
 * Paths are generated once at module scope from a seeded PRNG, so the output
 * is identical on every render and prerenders into the static shell.
 */

const W = 1000;
const H = 240;
const CH = { asked: 44, happened: 124, told: 204 } as const;

function prng(seed: number) {
  let t = seed >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

type Shape =
  | { kind: "spike"; x: number; h: number }
  | { kind: "bump"; x: number; h: number; w: number }
  | { kind: "burst"; x: number; h: number; w: number };

function displacement(x: number, shapes: Shape[]): number {
  let d = 0;
  for (const sh of shapes) {
    const dx = x - sh.x;
    if (sh.kind === "spike") {
      // A pen jump: a sharp peak with a small rebound after it.
      d += sh.h * Math.exp(-(dx * dx) / 18);
      d -= sh.h * 0.28 * Math.exp(-((dx - 9) * (dx - 9)) / 30);
    } else if (sh.kind === "bump") {
      d += sh.h * Math.exp(-(dx * dx) / (2 * sh.w * sh.w));
    } else {
      const env = Math.exp(-(dx * dx) / (2 * sh.w * sh.w));
      d += sh.h * env * Math.sin(dx * 0.9) * (0.65 + 0.35 * Math.sin(dx * 2.3));
    }
  }
  return d;
}

function tracePath(baseY: number, shapes: Shape[], seed: number, from = 0, to = W): string {
  const rand = prng(seed);
  const pts: string[] = [];
  let wobble = 0;
  for (let x = 0; x <= W; x += 2) {
    // Low-frequency tremor so the line reads as ink, not a vector.
    wobble = wobble * 0.82 + (rand() - 0.5) * 0.9;
    if (x < from || x > to) continue;
    const y = baseY - displacement(x, shapes) + wobble;
    pts.push(`${pts.length === 0 ? "M" : "L"}${x},${y.toFixed(2)}`);
  }
  return pts.join("");
}

const askedShapes: Shape[] = [
  { kind: "spike", x: 200, h: 30 },
  { kind: "spike", x: 520, h: 30 },
];
const happenedShapes: Shape[] = [
  { kind: "spike", x: 236, h: 28 },
  { kind: "spike", x: 556, h: 28 },
];
const toldShapes: Shape[] = [
  { kind: "burst", x: 292, h: 22, w: 14 },
  { kind: "bump", x: 604, h: 16, w: 10 },
  { kind: "bump", x: 790, h: 9, w: 22 },
];

const PATHS = {
  asked: tracePath(CH.asked, askedShapes, 11),
  happened: tracePath(CH.happened, happenedShapes, 23),
  happenedSecond: tracePath(CH.happened, happenedShapes, 23, 536, 580),
  told: tracePath(CH.told, toldShapes, 37),
  toldError: tracePath(CH.told, toldShapes, 37, 262, 324),
};

type Note = {
  x: number;
  channel: keyof typeof CH;
  text: string;
  fault?: string;
  tone?: "bad";
  align?: "start" | "end";
};

// Notes sit just after the mark they describe, so text never covers the ink.
const NOTES: Note[] = [
  { x: 210, channel: "asked", text: "pay_invoice INV-42" },
  { x: 246, channel: "happened", text: "Payment recorded" },
  { x: 322, channel: "told", text: "Error: upstream timed out", fault: "lost_response", tone: "bad" },
  { x: 530, channel: "asked", text: "Retries pay_invoice INV-42" },
  { x: 566, channel: "happened", text: "Second payment recorded", tone: "bad" },
  { x: 618, channel: "told", text: "OK" },
  { x: 992, channel: "told", text: "“The first try failed, so I retried. INV-42 is paid.”", align: "end" },
];

const DURATION_S = 3.8;

export function HeroTrace() {
  return (
    <figure className={s.figure} aria-labelledby="hero-trace-caption">
      <div className={s.scroller}>
        <div className={s.instrument} style={{ "--duration": `${DURATION_S}s` } as CSSProperties}>
          <ul className={s.channels} aria-hidden="true">
            <li style={{ top: `${(CH.asked / H) * 100}%` }}>Asked</li>
            <li style={{ top: `${(CH.happened / H) * 100}%` }}>Happened</li>
            <li style={{ top: `${(CH.told / H) * 100}%` }}>Told</li>
          </ul>

          <div className={`${s.paper} chart-paper`}>
            <svg
              className={s.svg}
              viewBox={`0 0 ${W} ${H}`}
              preserveAspectRatio="none"
              role="img"
              aria-label="Chart recording of three channels. The agent asks to pay invoice INV-42 twice, the bank records two payments, and the agent is told an error, then OK."
            >
              <defs>
                <clipPath id="hero-reveal" clipPathUnits="userSpaceOnUse">
                  <rect className={s.reveal} x="0" y="0" width={W} height={H} />
                </clipPath>
              </defs>
              <g clipPath="url(#hero-reveal)" fill="none" strokeLinecap="round" strokeLinejoin="round">
                <path d={PATHS.asked} className={s.penAsked} />
                <path d={PATHS.happened} className={s.penHappened} />
                <path d={PATHS.happenedSecond} className={s.penBad} />
                <path d={PATHS.told} className={s.penTold} />
                <path d={PATHS.toldError} className={s.penBad} />
              </g>
              <line className={s.cursor} x1="0" x2="0" y1="0" y2={H} />
            </svg>

            {NOTES.map((n) => (
              <span
                key={`${n.channel}-${n.x}`}
                className={[s.note, n.tone === "bad" ? s.noteBad : "", n.align === "end" ? s.noteEnd : ""].join(" ")}
                style={
                  {
                    left: `${(n.x / W) * 100}%`,
                    top: `${(CH[n.channel] / H) * 100}%`,
                    "--at": n.x / W,
                  } as CSSProperties
                }
              >
                {n.fault ? <code className={s.fault}>{n.fault}</code> : null}
                {n.text}
              </span>
            ))}
          </div>
        </div>
      </div>

      <figcaption id="hero-trace-caption" className={s.caption}>
        <p className={s.verdict}>
          <span className={s.verdictMark} aria-hidden="true" />
          Verdict: double action
        </p>
        <p className={s.explain}>
          The bank recorded two payments for INV-42. Read alone, the transcript looks like a success. Polygraph
          compares what the agent asked for, what really happened on the server, and what the agent was told.
        </p>
      </figcaption>
    </figure>
  );
}

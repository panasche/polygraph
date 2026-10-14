/** A static rendering of the README badge, matching the SVG the API serves. */
export function BadgePreview({ lie, coverage }: { lie: string; coverage: string }) {
  const left = "polygraph";
  const right = `silent-lie ${lie} · coverage ${coverage}`;
  const lw = 70;
  const rw = Math.round(right.length * 6.3 + 14);
  return (
    <svg
      width={lw + rw}
      height={20}
      viewBox={`0 0 ${lw + rw} 20`}
      role="img"
      aria-label={`polygraph: ${right}`}
      style={{ fontFamily: "Verdana, DejaVu Sans, sans-serif" }}
    >
      <rect width={lw} height={20} rx={3} fill="#56636f" />
      <rect x={lw} width={rw} height={20} rx={3} fill="#2e7a5a" />
      <rect x={lw} width={4} height={20} fill="#2e7a5a" />
      <text x={lw / 2} y={14} fill="#fff" fontSize={11} textAnchor="middle">
        {left}
      </text>
      <text x={lw + rw / 2} y={14} fill="#fff" fontSize={11} textAnchor="middle">
        {right}
      </text>
    </svg>
  );
}

/**
 * Compact hand-rolled SVG sparkline (issue_015-r5 F2). No chart library.
 *
 * Renders a small filled area + line for a numeric series, scaled to a fixed
 * viewBox. Values are normalized against `max` (or the series max when `max`
 * is omitted/zero). Purely presentational and deterministic so it is easy to
 * unit-test.
 */

export function sparklinePath(
  values: number[],
  width: number,
  height: number,
  max: number,
): { line: string; area: string } {
  if (values.length === 0) return { line: "", area: "" };
  const top = max > 0 ? max : Math.max(...values, 1);
  const n = values.length;
  const stepX = n > 1 ? width / (n - 1) : 0;
  const pt = (v: number, i: number): [number, number] => {
    const x = n > 1 ? i * stepX : width / 2;
    // Clamp to [0, top]; invert Y so higher values are drawn higher.
    const clamped = Math.max(0, Math.min(top, v));
    const y = height - (clamped / top) * height;
    return [x, y];
  };
  const coords = values.map(pt);
  const line = coords
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
  const area =
    `M0,${height.toFixed(1)} ` +
    coords.map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(" ") +
    ` L${width.toFixed(1)},${height.toFixed(1)} Z`;
  return { line, area };
}

export function Sparkline(props: {
  label: string;
  values: number[];
  /** Upper bound for scaling; falls back to the series max. */
  max?: number;
  /** Formatted current value shown next to the label. */
  valueLabel: string;
  /** Colour class suffix: ok | warn | full (defaults to a neutral accent). */
  tone?: "ok" | "warn" | "full";
}) {
  const width = 120;
  const height = 34;
  const { line, area } = sparklinePath(
    props.values,
    width,
    height,
    props.max ?? 0,
  );
  const tone = props.tone ?? "ok";
  return (
    <div className="sparkline" role="group" aria-label={props.label}>
      <div className="sparkline-head">
        <span className="sparkline-label">{props.label}</span>
        <span className="sparkline-value">{props.valueLabel}</span>
      </div>
      <svg
        className="sparkline-svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {area && <path className={`spark-area spark-${tone}`} d={area} />}
        {line && <path className={`spark-line spark-${tone}`} d={line} />}
      </svg>
    </div>
  );
}

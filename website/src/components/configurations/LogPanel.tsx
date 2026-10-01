import { rowTimeFormat } from "@/lib/configurations/format";
import type { ClockLabel, LogLine } from "@/lib/configurations/types";

const LEVEL = { info: ["INFO", "cfg-log__level"], warn: ["WARN", "cfg-log__level cfg-log__level--warn"], error: ["ERROR", "cfg-log__level cfg-log__level--error"] } as const;

/**
 * Mono log rows (`role="log"`): time on the given clock (UTC by default), level,
 * source and message. `sourceLabel` can add the robot name, e.g. "router · Unit 16".
 * Place it in a panel whose eyebrow names the window and clock.
 */
export function LogPanel({ lines, label, clock, sourceLabel, empty = "No log lines in this window." }: { lines: readonly LogLine[]; label: string; clock?: ClockLabel; sourceLabel?: (line: LogLine) => string; empty?: string }) {
  if (!lines.length) return <p className="portal-empty">{empty}</p>;
  const time = rowTimeFormat(lines.map(line => line.at), clock);
  return <div className={time.dated ? "cfg-log cfg-log--dated" : "cfg-log"} role="log" aria-label={label}>
    {lines.map(line => <div className="cfg-log__row" key={line.id}>
      <time className="cfg-log__time" dateTime={line.at}>{time.format(line.at)}</time>
      <span className={LEVEL[line.level][1]}>{LEVEL[line.level][0]}</span>
      <span className="cfg-log__src">{sourceLabel ? sourceLabel(line) : line.source}</span>
      <span className="cfg-log__msg">{line.message}</span>
    </div>)}
  </div>;
}

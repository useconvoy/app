import { fmtCount, fmtFixed, fmtNumber } from "@/lib/configurations/format";
import { Sparkline, Tile } from "../Tiles";
import type { RobotView } from "../useRobots";

const value = (number: number | null | undefined, digits = 1) => number === null || number === undefined || !Number.isFinite(number) ? null : fmtFixed(number, digits);
const gib = (mib: number | null | undefined) => mib === null || mib === undefined ? null : fmtFixed(mib / 1024, 1);

/** Measured inference latency (median of the received requests) with each request's latency, oldest first. */
export function InferenceTile({ view }: { view: RobotView | null }) {
  const spans = view?.live.data?.inference ?? [];
  const p50 = view?.readings.live ? view.readings.edgeMs?.p50 ?? null : null;
  return <Tile label="Inference p50" value={p50 === null ? null : fmtNumber(p50, 0)} unit="ms"
    sub={view && p50 !== null ? `${fmtCount(spans.length)} requests` : view ? "No requests" : "No live device"}
    chart={<Sparkline points={spans.toReversed().map(span => span.latencyMs)} />} />;
}

export function CpuTile({ view }: { view: RobotView }) {
  const latest = view.readings.latest;
  return <Tile label="CPU" value={value(latest?.cpuPct, 0)} unit="%" sub={latest?.gpuPct != null ? `GPU ${fmtFixed(latest.gpuPct, 0)} %` : undefined} chart={<Sparkline points={view.readings.recent.cpuPct ?? []} />} />;
}

export function MemoryTile({ view }: { view: RobotView }) {
  const latest = view.readings.latest;
  return <Tile label="Memory free" value={gib(latest?.memAvailableMiB)} unit="GiB" sub={latest?.memTotalMiB ? `of ${fmtFixed(latest.memTotalMiB / 1024, 1)} GiB` : undefined} chart={<Sparkline points={view.readings.recent.memAvailableMiB ?? []} />} />;
}

export function TemperatureTile({ view }: { view: RobotView }) {
  return <Tile label="SoC temperature" value={value(view.readings.latest?.socTempC)} unit="°C" chart={<Sparkline points={view.readings.recent.socTempC ?? []} />} />;
}

export function PowerTile({ view }: { view: RobotView }) {
  return <Tile label="Board power" value={value(view.readings.latest?.boardPowerW)} unit="W" chart={<Sparkline points={view.readings.recent.boardPowerW ?? []} />} />;
}

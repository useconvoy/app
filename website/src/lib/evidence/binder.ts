/**
 * Evidence binder assembly: one zip per run with a manifest whose
 * checksums make the export self-verifying. File names inside the archive
 * are plain (manifest.json, run-summary.json, events.ndjson,
 * land-report.json); internal identifiers appear inside the manifest only.
 *
 * Files listed in the land report (deliverables, the report snapshot) are
 * refs into the platform's own store, whose content the control plane does
 * not expose over the edge. They appear in the manifest with their names
 * and checksums and a note saying the content is held by the platform, so
 * an auditor can still verify them against a platform-side export.
 */
import "server-only";

import { createHash } from "node:crypto";

import { controlPlaneUrl, sseHeaders, type ActorContext, type RunView } from "@/lib/api/client";
import { getRun } from "@/lib/api/runs";
import { buildZip, type ZipEntry } from "./zip";

interface ManifestFile {
  name: string;
  sha256: string;
  size_bytes: number;
  note?: string;
}

function sha256(data: Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

interface PlatformRef {
  key?: unknown;
  sha256?: unknown;
  size_bytes?: unknown;
}

/** File refs the land report carries but whose bytes stay platform-side. */
function platformFiles(report: Record<string, unknown> | null): ManifestFile[] {
  if (!report) return [];
  const refs: PlatformRef[] = [];
  const deliverables = report["deliverables"];
  if (Array.isArray(deliverables)) refs.push(...(deliverables as PlatformRef[]));
  const reportRef = report["report_ref"];
  if (reportRef && typeof reportRef === "object") refs.push(reportRef as PlatformRef);
  return refs
    .filter((ref) => typeof ref.key === "string" && typeof ref.sha256 === "string")
    .map((ref) => ({
      name: (ref.key as string).split("/").pop() ?? (ref.key as string),
      sha256: ref.sha256 as string,
      size_bytes: typeof ref.size_bytes === "number" ? ref.size_bytes : 0,
      note: "content held by the platform",
    }));
}

/**
 * The pure half: given the run view and its raw event log, produce the
 * archive entries with the manifest first. Exported for the unit suite.
 */
export function buildBinderFiles(
  run: RunView,
  eventsNdjson: string,
  exporter: string,
  exportedAt: string,
): ZipEntry[] {
  const encoder = new TextEncoder();
  const entries: ZipEntry[] = [
    { name: "run-summary.json", data: encoder.encode(JSON.stringify(run, null, 2)) },
    { name: "events.ndjson", data: encoder.encode(eventsNdjson) },
  ];
  const report = (run.land_report as Record<string, unknown> | null) ?? null;
  if (report) {
    entries.push({
      name: "land-report.json",
      data: encoder.encode(JSON.stringify(report, null, 2)),
    });
  }

  const manifest = {
    run_id: run.run_id,
    goal: run.goal,
    exported_at: exportedAt,
    exported_by: exporter,
    files: entries.map((entry) => ({
      name: entry.name,
      sha256: sha256(entry.data),
      size_bytes: entry.data.length,
    })),
    platform_files: platformFiles(report),
  };
  return [
    { name: "manifest.json", data: encoder.encode(JSON.stringify(manifest, null, 2)) },
    ...entries,
  ];
}

/**
 * The run's full event log over the edge, as NDJSON. The stream replays
 * from seq 0 and closes after a terminal event; a short idle timeout covers
 * exporting a run that is still open (possible, if unusual).
 */
async function fetchEventsNdjson(actor: ActorContext, runId: string): Promise<string> {
  const controller = new AbortController();
  const idle = setTimeout(() => controller.abort(), 5000);
  const lines: string[] = [];
  try {
    const upstream = await fetch(
      `${controlPlaneUrl()}/runs/${encodeURIComponent(runId)}/events?after=0`,
      {
        headers: { ...sseHeaders(actor), Accept: "text/event-stream" },
        cache: "no-store",
        signal: controller.signal,
      },
    );
    if (!upstream.ok || !upstream.body) return "";
    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trimEnd();
        buffer = buffer.slice(newline + 1);
        if (line.startsWith("data: ")) lines.push(line.slice(6));
        newline = buffer.indexOf("\n");
      }
    }
  } catch {
    // Abort (idle timeout) or transport failure: export what arrived.
  } finally {
    clearTimeout(idle);
    controller.abort();
  }
  return lines.length > 0 ? `${lines.join("\n")}\n` : "";
}

/** Assemble the binder zip for a run, or null when the run is not visible. */
export async function assembleBinder(
  actor: ActorContext,
  runId: string,
  exporter: string,
): Promise<Uint8Array | null> {
  const run = await getRun(actor, runId);
  if (!run) return null;
  const eventsNdjson = await fetchEventsNdjson(actor, runId);
  const files = buildBinderFiles(run, eventsNdjson, exporter, new Date().toISOString());
  return buildZip(files);
}

/**
 * Binder assembly: the manifest carries per-file sha256 checksums,
 * archive file names stay plain (internal ids live inside the manifest
 * only), and platform-held files are listed with their note.
 */
import { createHash } from "node:crypto";

import { describe, expect, it } from "vitest";

import type { RunView } from "@/lib/api/client";
import { buildBinderFiles } from "@/lib/evidence/binder";

const RUN_ID = "run-1133b02d093f";

const run: RunView = {
  run_id: RUN_ID,
  tenant_id: "tenant-fixtures",
  parent_run_id: null,
  status: "completed",
  goal: "Write exception memos and hold them for review",
  plan: null,
  budget: { cap_usd: "40", spent_usd: "0.0002", reserved_usd: "0" },
  steps: [],
  environment_id: "",
  started_via: "manual",
  land_report: {
    goal: "Write exception memos and hold them for review",
    run_id: RUN_ID,
    status: "completed",
    cost_usd: "0.0002",
    steps_done: 2,
    steps_failed: 0,
    steps_skipped: 0,
    report_ref: {
      key: `runs/${RUN_ID}/reports/land.json`,
      bucket: "convoy-artifacts",
      sha256: "ed57de00",
      size_bytes: 736,
      content_type: "application/json",
    },
    deliverables: [
      {
        key: `runs/${RUN_ID}/outputs/step-1.json`,
        bucket: "convoy-artifacts",
        sha256: "37c7df00",
        size_bytes: 57,
        content_type: "application/json",
      },
    ],
    success_criteria: [],
  },
};

const eventsNdjson = '{"seq":1,"type":"run_started"}\n{"seq":2,"type":"run_completed"}\n';

describe("buildBinderFiles", () => {
  const entries = buildBinderFiles(run, eventsNdjson, "j.doe@example.com", "2026-08-05T12:00:00Z");
  const manifest = JSON.parse(new TextDecoder().decode(entries[0]!.data)) as {
    run_id: string;
    goal: string;
    exported_at: string;
    exported_by: string;
    files: Array<{ name: string; sha256: string; size_bytes: number }>;
    platform_files: Array<{ name: string; sha256: string; note?: string }>;
  };

  it("keeps internal ids inside the manifest only", () => {
    expect(entries.map((entry) => entry.name)).toEqual([
      "manifest.json",
      "run-summary.json",
      "events.ndjson",
      "land-report.json",
    ]);
    for (const entry of entries) {
      expect(entry.name).not.toContain(RUN_ID);
    }
    expect(manifest.run_id).toBe(RUN_ID);
    expect(manifest.goal).toBe(run.goal);
    expect(manifest.exported_by).toBe("j.doe@example.com");
  });

  it("checksums every included file with sha256", () => {
    expect(manifest.files.map((file) => file.name)).toEqual([
      "run-summary.json",
      "events.ndjson",
      "land-report.json",
    ]);
    for (const listed of manifest.files) {
      const entry = entries.find((candidate) => candidate.name === listed.name)!;
      const digest = createHash("sha256").update(entry.data).digest("hex");
      expect(listed.sha256).toBe(digest);
      expect(listed.size_bytes).toBe(entry.data.length);
    }
  });

  it("lists platform-held files with their checksums and note", () => {
    expect(manifest.platform_files).toEqual([
      { name: "step-1.json", sha256: "37c7df00", size_bytes: 57, note: "content held by the platform" },
      { name: "land.json", sha256: "ed57de00", size_bytes: 736, note: "content held by the platform" },
    ]);
  });

  it("omits the land report entry when the run has none", () => {
    const bare = buildBinderFiles(
      { ...run, land_report: null },
      eventsNdjson,
      "j.doe@example.com",
      "2026-08-05T12:00:00Z",
    );
    expect(bare.map((entry) => entry.name)).toEqual([
      "manifest.json",
      "run-summary.json",
      "events.ndjson",
    ]);
  });
});

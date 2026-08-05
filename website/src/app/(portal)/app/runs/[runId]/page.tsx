import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { getRun } from "@/lib/api/runs";
import { requireRunPage } from "@/lib/runs/context";
import { RunDetail } from "./run-detail";

export const metadata: Metadata = { title: "Run" };
export const dynamic = "force-dynamic";

/**
 * Server shell for the run detail: verifies the session, resolves the org's
 * tenant, and fetches the initial view through the single authenticated
 * edge. A 404 may mean wrong tenant and renders as not-found, full stop
 * (CLAUDE.md rule 7). The live timeline hydrates purely from the SSE
 * stream inside RunDetail.
 */
export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  const { actor } = await requireRunPage();
  const run = await getRun(actor, runId);
  if (!run) notFound();
  return <RunDetail initial={run} />;
}

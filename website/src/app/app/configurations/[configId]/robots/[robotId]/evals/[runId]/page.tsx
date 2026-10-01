import { Suspense } from "react";
import { EvalRunPage } from "@/components/configurations/pages/EvalRunPage";

export default async function EvalRunRoute({ params }: { params: Promise<{ configId: string; robotId: string; runId: string }> }) {
  const { configId, robotId, runId } = await params;
  return <Suspense fallback={null}><EvalRunPage configId={configId} robotId={robotId} runId={runId} /></Suspense>;
}

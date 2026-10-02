import { Suspense } from "react";
import { ProjectPage } from "@/components/projects/Projects";
export default async function Page({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <Suspense fallback={null}><ProjectPage key={projectId} projectId={projectId} /></Suspense>;
}

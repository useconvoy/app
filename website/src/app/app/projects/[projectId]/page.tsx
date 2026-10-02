import { ProjectPage } from "@/components/projects/Projects";
export default async function Page({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectPage key={projectId} projectId={projectId} />;
}

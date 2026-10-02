import { RobotExecution } from "@/components/projects/RobotExecution";

export default async function Page({ params }: { params: Promise<{ projectId: string; robotId: string }> }) {
  const { projectId, robotId } = await params;
  return <RobotExecution key={robotId} projectId={projectId} robotId={robotId} />;
}

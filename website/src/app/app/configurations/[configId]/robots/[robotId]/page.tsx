import { Suspense } from "react";
import { RobotPage } from "@/components/configurations/pages/RobotPage";

export default async function RobotRoute({ params }: { params: Promise<{ configId: string; robotId: string }> }) {
  const { configId, robotId } = await params;
  return <Suspense fallback={null}><RobotPage configId={configId} robotId={robotId} /></Suspense>;
}

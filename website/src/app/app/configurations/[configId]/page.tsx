import { Suspense } from "react";
import { ConfigDashboardPage } from "@/components/configurations/pages/ConfigDashboardPage";

export default async function ConfigurationRoute({ params }: { params: Promise<{ configId: string }> }) {
  const { configId } = await params;
  return <Suspense fallback={null}><ConfigDashboardPage configId={configId} /></Suspense>;
}

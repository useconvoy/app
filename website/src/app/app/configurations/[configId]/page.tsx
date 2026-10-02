import { Suspense } from "react";
import { ConfigurationReleasePage } from "@/components/projects/ConfigurationReleasePage";
import { ConfigDashboardPage } from "@/components/configurations/pages/ConfigDashboardPage";

export default async function ConfigurationRoute({ params, searchParams }: { params: Promise<{ configId: string }>; searchParams: Promise<{ source?: string }> }) {
  const { configId } = await params;
  const query = await searchParams;
  return <Suspense fallback={null}>{query.source === "project" ? <ConfigurationReleasePage configurationId={configId} /> : <ConfigDashboardPage configId={configId} />}</Suspense>;
}

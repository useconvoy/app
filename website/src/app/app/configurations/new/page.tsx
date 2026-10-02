import { Suspense } from "react";
import { NewProjectConfiguration } from "@/components/projects/ConfigurationEditor";
import { NewConfigurationPage } from "@/components/configurations/pages/NewConfigurationPage";

export default async function NewConfigurationRoute({ searchParams }: { searchParams: Promise<{ project_id?: string; profile_id?: string; setup?: string }> }) {
  const query = await searchParams;
  return <Suspense fallback={null}>{query.project_id && query.setup !== "draft" ? <NewProjectConfiguration projectId={query.project_id} profileId={query.profile_id} /> : <NewConfigurationPage projectId={query.project_id} />}</Suspense>;
}

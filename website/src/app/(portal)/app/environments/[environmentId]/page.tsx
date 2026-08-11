import { redirect } from "next/navigation";

export default async function EnvironmentDetailRedirect({
  params,
}: {
  params: Promise<{ environmentId: string }>;
}) {
  const { environmentId } = await params;
  redirect(`/app/agents/${environmentId}`);
}

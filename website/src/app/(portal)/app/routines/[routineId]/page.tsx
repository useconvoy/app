import { redirect } from "next/navigation";

/** Preserve old shared URLs while consolidating the job into its Agent. */
export default async function LegacyRoutineDetailPage({
  params,
}: {
  params: Promise<{ routineId: string }>;
}) {
  const { routineId } = await params;
  redirect(`/app/agents/${routineId}`);
}

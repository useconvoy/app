import { redirect } from "next/navigation";

/** Legacy bookmark compatibility; Routine is no longer a product object. */
export default function LegacyRoutinesPage() {
  redirect("/app/agents");
}

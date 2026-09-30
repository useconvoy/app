import { redirect } from "next/navigation";

export default async function LegacyDevicePage({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const { view } = await searchParams;
  const selected = ["device", "chat", "usage", "traces"].includes(view ?? "") ? view : "device";
  redirect(`/app/applications?section=device&view=${selected}`);
}

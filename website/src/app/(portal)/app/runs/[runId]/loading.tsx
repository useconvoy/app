import { SkeletonList } from "@/components/SkeletonList";

/** Detail skeleton while the initial view loads (C5). */
export default function RunDetailLoading() {
  return (
    <div className="mx-auto max-w-6xl">
      <SkeletonList rows={6} />
    </div>
  );
}

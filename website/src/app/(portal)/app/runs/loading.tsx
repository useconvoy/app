import { SkeletonList } from "@/components/SkeletonList";

/** List skeleton while runs hydrate; never a full-page spinner. */
export default function RunsLoading() {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header>
        <h1 className="font-display text-3xl text-ink">Runs</h1>
      </header>
      <SkeletonList rows={5} />
    </div>
  );
}

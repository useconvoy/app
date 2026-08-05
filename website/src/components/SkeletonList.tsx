export interface SkeletonListProps {
  rows?: number;
}

/** List placeholder while data loads; never a full-page spinner (C5). */
export function SkeletonList({ rows = 4 }: SkeletonListProps) {
  return (
    <div role="status" aria-label="Loading" aria-busy="true" className="space-y-2">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="h-10 rounded-md border border-line-soft bg-card"
        >
          <div className="m-3 h-4 w-2/5 rounded bg-line-soft" />
        </div>
      ))}
    </div>
  );
}

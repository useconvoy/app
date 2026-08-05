/**
 * The org audit log table (DESIGN §5 Admin row, §9), presentational:
 * actor names resolved, friendly dates, and mono for the facts from the
 * log (action names and subjects). Filtering and pagination live in the
 * page; this only renders rows.
 */
import { EmptyState } from "@/components/EmptyState";
import { friendlyDateTime } from "@/lib/format";
import { adminCopy } from "@/lexicon";

export interface AuditTableRow {
  id: string;
  action: string;
  subject: string;
  /** ISO timestamp; rendered through friendlyDateTime. */
  ts: string;
  actorName: string | null;
}

export interface AuditTableProps {
  rows: AuditTableRow[];
}

export function AuditTable({ rows }: AuditTableProps) {
  if (rows.length === 0) {
    return <EmptyState title={adminCopy.auditEmpty} />;
  }
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-card">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase text-muted">
            <th className="px-4 py-2 font-medium">When</th>
            <th className="px-4 py-2 font-medium">Who</th>
            <th className="px-4 py-2 font-medium">Action</th>
            <th className="px-4 py-2 font-medium">Subject</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line-soft align-top">
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                {friendlyDateTime(row.ts)}
              </td>
              <td className="px-4 py-3 text-ink">{row.actorName ?? adminCopy.auditFormerMember}</td>
              <td className="px-4 py-3 font-mono text-xs text-ink">{row.action}</td>
              <td className="max-w-md truncate px-4 py-3 font-mono text-xs text-muted">
                {row.subject}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

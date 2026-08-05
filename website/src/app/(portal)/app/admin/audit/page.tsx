import type { Metadata } from "next";
import Link from "next/link";

import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { listAuditEntries } from "@/lib/orgs/queries";
import { adminCopy } from "@/lexicon";
import { AuditTable } from "./AuditTable";

export const metadata: Metadata = { title: "Audit log" };

const PAGE_SIZE = 50;

/**
 * The org audit log surface, admin-only: admin_audit rows
 * newest first, filterable by action prefix through a plain GET form,
 * paginated with simple limit/offset links.
 */
export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<{ action?: string; page?: string }>;
}) {
  const { session } = await requireAdminPage();
  const params = await searchParams;
  const actionPrefix = (params.action ?? "").trim();
  const page = Math.max(Number.parseInt(params.page ?? "1", 10) || 1, 1);
  const { entries, hasMore } = await listAuditEntries(session.orgId, {
    actionPrefix: actionPrefix || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });

  const pageHref = (target: number) => {
    const query = new URLSearchParams();
    if (actionPrefix) query.set("action", actionPrefix);
    if (target > 1) query.set("page", String(target));
    const qs = query.toString();
    return qs ? `/app/admin/audit?${qs}` : "/app/admin/audit";
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">{adminCopy.auditTitle}</h1>
        <p className="mt-1 text-sm text-muted">{adminCopy.auditIntro}</p>
      </header>

      <form action="/app/admin/audit" method="get" className="flex flex-wrap items-end gap-3">
        <label className="block text-sm text-ink">
          {adminCopy.auditFilterLabel}
          <input
            type="text"
            name="action"
            defaultValue={actionPrefix}
            placeholder={adminCopy.auditFilterHint}
            className="mt-1 block w-64 rounded-sm border border-line bg-card px-3 py-2 text-sm"
          />
        </label>
        <button
          type="submit"
          className="rounded-sm border border-line px-3 py-2 text-sm text-ink hover:border-pine"
        >
          {adminCopy.auditApplyFilter}
        </button>
      </form>

      <AuditTable
        rows={entries.map((entry) => ({
          id: entry.id,
          action: entry.action,
          subject: entry.subject,
          ts: entry.ts.toISOString(),
          actorName: entry.actorName,
        }))}
      />

      <nav aria-label="Audit pages" className="flex items-center gap-3">
        {page > 1 ? (
          <Link href={pageHref(page - 1)} className="text-sm text-ink underline hover:text-pine">
            {adminCopy.auditNewer}
          </Link>
        ) : null}
        {hasMore ? (
          <Link href={pageHref(page + 1)} className="text-sm text-ink underline hover:text-pine">
            {adminCopy.auditOlder}
          </Link>
        ) : null}
      </nav>
    </div>
  );
}

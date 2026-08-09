import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Chip } from "@/components/Chip";
import { environmentsClient } from "@/lib/api/environments";
import { friendlyDate } from "@/lib/format";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";

export const metadata: Metadata = { title: "Environment" };
export const dynamic = "force-dynamic";

export default async function EnvironmentDetailPage({
  params,
}: {
  params: Promise<{ environmentId: string }>;
}) {
  const { environmentId } = await params;
  const { session } = await requireWorkspacesPage();
  const client = environmentsClient();
  const environment = await client.getExecutionEnvironment(session.orgId, environmentId);
  if (!environment) notFound();
  const workspace = await client.getWorkspace(session.orgId, environment.workspaceId);
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/environments" className="underline">Environments</Link>
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="font-display text-3xl text-ink">{environment.name}</h1>
          {environment.isDefault && <Chip>Default</Chip>}
        </div>
        <p className="mt-2 text-sm text-muted">{environment.purpose}</p>
        <p className="mt-2 font-mono text-xs text-muted">
          v{environment.version} · created {friendlyDate(environment.createdAt)}
        </p>
      </header>

      <div className="grid gap-5 md:grid-cols-2">
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Workspace</h2>
          <p className="mt-2 text-sm text-muted">
            Systems and routines come from this workspace.
          </p>
          {workspace && (
            <Link href={`/app/workspaces/${workspace.id}`} className="mt-3 inline-block text-sm text-ink underline">
              {workspace.name}
            </Link>
          )}
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Compute</h2>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-muted">Template</dt>
              <dd className="mt-1 font-mono text-xs text-ink">{environment.sandboxTemplate}</dd>
            </div>
            <div>
              <dt className="text-muted">Pause and resume</dt>
              <dd className="mt-1 text-ink">Automatic checkpoint and fresh-compute restore</dd>
            </div>
          </dl>
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Browser</h2>
          {environment.browserPolicy ? (
            <dl className="mt-3 space-y-3 text-sm">
              <div>
                <dt className="text-muted">Allowed domains</dt>
                <dd className="mt-1 text-ink">
                  {environment.browserPolicy.allowedDomains.length > 0
                    ? environment.browserPolicy.allowedDomains.join(", ")
                    : "No domains allowed yet"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Sign-in state</dt>
                <dd className="mt-1 text-ink">
                  {environment.browserPolicy.persistProfile ? "Restored after pauses" : "Discarded with compute"}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mt-2 text-sm text-muted">No browser is configured.</p>
          )}
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Bindings</h2>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-muted">Production</dt>
              <dd className="mt-1 break-all font-mono text-xs text-ink">{environment.productionBindingId}</dd>
            </div>
            <div>
              <dt className="text-muted">Rehearsal</dt>
              <dd className="mt-1 break-all font-mono text-xs text-ink">{environment.rehearsalBindingId}</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  );
}

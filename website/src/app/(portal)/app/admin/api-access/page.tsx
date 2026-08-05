import type { Metadata } from "next";
import Link from "next/link";

import { controlPlaneUrl } from "@/lib/api/client";
import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { adminCopy } from "@/lexicon";
import { ApiAccessNotice } from "./ApiAccessNotice";

export const metadata: Metadata = { title: "API access" };

/** Phase-1 API access: a notice, no tables, no keys (see ApiAccessNotice). */
export default async function ApiAccessPage() {
  await requireAdminPage();
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">{adminCopy.apiAccessTitle}</h1>
        <p className="mt-1 text-sm text-muted">{adminCopy.apiAccessIntro}</p>
      </header>
      <ApiAccessNotice controlPlaneUrl={controlPlaneUrl()} />
    </div>
  );
}

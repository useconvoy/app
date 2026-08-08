import type { Metadata } from "next";
import Link from "next/link";

import {
  removeModelKeyAction,
  setModelKeyAction,
  verifyModelKeyAction,
} from "@/lib/credentials/actions";
import { getCredentialStatus, type CredentialStatus } from "@/lib/credentials/store";
import { PROVIDERS, type Provider } from "@/lib/credentials/verify";
import { friendlyDate } from "@/lib/format";
import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { modelAccessCopy, modelProviderLabels } from "@/lexicon";
import { ModelAccessForm, type ProviderView } from "./ModelAccessForm";

export const metadata: Metadata = { title: "Model access" };

/** Compose the recorded verification into one plain line for the card. */
function verifyLine(status: CredentialStatus): string {
  if (!status.verifyStatus) return modelAccessCopy.notVerifiedYet;
  if (status.verifyStatus === "ok") {
    return status.verifiedAt
      ? modelAccessCopy.lastVerified(friendlyDate(status.verifiedAt))
      : modelAccessCopy.verifyStatusLabel.ok!;
  }
  const label = modelAccessCopy.verifyStatusLabel[status.verifyStatus] ?? modelAccessCopy.notVerifiedYet;
  return status.verifiedAt ? `${label} · ${friendlyDate(status.verifiedAt)}` : label;
}

function toView(provider: Provider, status: CredentialStatus | null): ProviderView {
  const displayName = modelProviderLabels[provider] ?? provider;
  const base = { provider, displayName, prefix: modelAccessCopy.keyPrefix[provider] ?? "" };
  if (!status) {
    return { ...base, configured: false, last4: null, setLine: null, verifyLine: null, verifyOk: false };
  }
  return {
    ...base,
    configured: true,
    last4: status.last4,
    setLine: status.setByName
      ? modelAccessCopy.setByOn(status.setByName, friendlyDate(status.setAt))
      : modelAccessCopy.setOn(friendlyDate(status.setAt)),
    verifyLine: verifyLine(status),
    verifyOk: status.verifyStatus === "ok",
  };
}

/**
 * Model access, admin-only. An organization sets its own Anthropic or OpenAI
 * keys here; each is verified with the provider and sealed at rest, and the
 * key itself can never be read back. The ciphertext is admin-gated at the
 * database (migration 0008), so this read runs under the verified admin's own
 * org and user context.
 */
export default async function ModelAccessPage() {
  const { session } = await requireAdminPage();
  const ctx = { orgId: session.orgId, userId: session.userId };
  const statuses = await Promise.all(
    PROVIDERS.map((provider) => getCredentialStatus(ctx, provider)),
  );
  const views = PROVIDERS.map((provider, index) => toView(provider, statuses[index]!));

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">{modelAccessCopy.title}</h1>
        <p className="mt-1 text-sm text-muted">{modelAccessCopy.intro}</p>
      </header>
      <ModelAccessForm
        providers={views}
        setAction={setModelKeyAction}
        verifyAction={verifyModelKeyAction}
        removeAction={removeModelKeyAction}
      />
    </div>
  );
}

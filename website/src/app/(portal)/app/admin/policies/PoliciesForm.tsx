"use client";

/**
 * Org policies form: the run budget default,
 * the monthly spend notice, and the viewer evidence-export toggle.
 * Client-side validation is a courtesy; the server action re-validates
 * with zod and refuses anything off.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { adminCopy } from "@/lexicon";

export interface PoliciesFormValues {
  defaultRunBudgetCapUsd: number;
  monthlySpendNoticeUsd: number;
  viewerEvidenceExport: boolean;
}

export interface PoliciesFormProps {
  initial: PoliciesFormValues;
  save: (policies: PoliciesFormValues) => Promise<void>;
}

export function PoliciesForm({ initial, save }: PoliciesFormProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function submit(formData: FormData) {
    const cap = Number(formData.get("defaultRunBudgetCapUsd"));
    const notice = Number(formData.get("monthlySpendNoticeUsd"));
    const viewerExport = formData.get("viewerEvidenceExport") === "on";
    setSaved(false);
    if (!Number.isFinite(cap) || cap <= 0 || !Number.isFinite(notice) || notice <= 0) {
      setError(adminCopy.amountInvalid);
      return;
    }
    if (notice < cap) {
      setError(adminCopy.noticeBelowCap);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await save({
        defaultRunBudgetCapUsd: cap,
        monthlySpendNoticeUsd: notice,
        viewerEvidenceExport: viewerExport,
      });
      setSaved(true);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    // noValidate: the form's own checks produce the copy above; native
    // bubbles would preempt them (and say less).
    <form action={submit} noValidate className="space-y-4 rounded-md border border-line bg-card p-6">
      <div className="flex flex-wrap gap-6">
        <label className="block text-sm text-ink">
          {adminCopy.defaultRunBudgetCap}
          <input
            type="number"
            name="defaultRunBudgetCapUsd"
            min={1}
            step={1}
            defaultValue={initial.defaultRunBudgetCapUsd}
            className="mt-1 block w-32 rounded-sm border border-line bg-card px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs text-muted">{adminCopy.defaultRunBudgetCapHint}</span>
        </label>
        <label className="block text-sm text-ink">
          {adminCopy.monthlySpendNotice}
          <input
            type="number"
            name="monthlySpendNoticeUsd"
            min={1}
            step={1}
            defaultValue={initial.monthlySpendNoticeUsd}
            className="mt-1 block w-32 rounded-sm border border-line bg-card px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs text-muted">{adminCopy.monthlySpendNoticeHint}</span>
        </label>
      </div>
      <label className="flex items-start gap-2 text-sm text-ink">
        <input
          type="checkbox"
          name="viewerEvidenceExport"
          defaultChecked={initial.viewerEvidenceExport}
          className="mt-0.5 h-4 w-4 accent-pine"
        />
        <span>
          {adminCopy.viewerEvidenceExport}
          <span className="mt-0.5 block text-xs text-muted">{adminCopy.viewerEvidenceExportHint}</span>
        </span>
      </label>
      {error ? (
        <p role="alert" className="text-sm text-fail">
          {error}
        </p>
      ) : null}
      {saved && !error ? (
        <p role="status" className="text-sm text-pass">
          {adminCopy.policiesSaved}
        </p>
      ) : null}
      <Button type="submit" disabled={busy}>
        {adminCopy.savePolicies}
      </Button>
    </form>
  );
}

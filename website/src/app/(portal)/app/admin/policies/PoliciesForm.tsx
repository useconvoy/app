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
import { Checkbox } from "@/components/ui/choice";
import { Field } from "@/components/ui/field";
import { NumberField } from "@/components/ui/text-field";
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
        <Field label={adminCopy.defaultRunBudgetCap} hint={adminCopy.defaultRunBudgetCapHint}>
          <NumberField
            name="defaultRunBudgetCapUsd"
            min={1}
            step={1}
            defaultValue={initial.defaultRunBudgetCapUsd}
            className="w-32"
          />
        </Field>
        <Field label={adminCopy.monthlySpendNotice} hint={adminCopy.monthlySpendNoticeHint}>
          <NumberField
            name="monthlySpendNoticeUsd"
            min={1}
            step={1}
            defaultValue={initial.monthlySpendNoticeUsd}
            className="w-32"
          />
        </Field>
      </div>
      <Checkbox
        name="viewerEvidenceExport"
        defaultChecked={initial.viewerEvidenceExport}
        label={adminCopy.viewerEvidenceExport}
        hint={adminCopy.viewerEvidenceExportHint}
      />
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
      <Button type="submit" pending={busy}>
        {adminCopy.savePolicies}
      </Button>
    </form>
  );
}

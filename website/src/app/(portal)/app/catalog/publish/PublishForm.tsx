"use client";

/**
 * Minimal workshop-org publish form: pick a routine, name the
 * snapshot version, write the storefront copy, set the test score floor.
 * Capability requirements ride along per routine, derived by the page;
 * the server action validates everything again with zod.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { catalogCopy } from "@/lexicon";

export interface PublishRoutineOption {
  id: string;
  name: string;
  requirements: {
    systems: Array<{ systemId: string; scope: "read" | "write" }>;
    vendorSpecificTools: Array<{ tool: string; vendorSystemId: string }>;
  };
}

export interface PublishFormProps {
  routines: PublishRoutineOption[];
  publish: (input: {
    routineId: string;
    version: number;
    storefront: { name: string; tagline: string; description: string };
    capabilityRequirements: PublishRoutineOption["requirements"];
    evalThresholds: { minScore: number };
    note?: string;
  }) => Promise<{ id: string }>;
}

export function PublishForm({ routines, publish }: PublishFormProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(formData: FormData) {
    const routineId = String(formData.get("routineId") ?? "");
    const routine = routines.find((option) => option.id === routineId);
    const version = Number(formData.get("version"));
    const minScore = Number(formData.get("minScore"));
    const storefront = {
      name: String(formData.get("name") ?? "").trim(),
      tagline: String(formData.get("tagline") ?? "").trim(),
      description: String(formData.get("description") ?? "").trim(),
    };
    if (!routine) {
      setError("Pick a routine to publish");
      return;
    }
    if (!Number.isInteger(version) || version < 1) {
      setError("Version must be a whole number starting at 1");
      return;
    }
    if (!storefront.name || !storefront.tagline || !storefront.description) {
      setError("Give the storefront a name, a tagline, and a description");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await publish({
        routineId,
        version,
        storefront,
        capabilityRequirements: routine.requirements,
        evalThresholds: { minScore: Number.isFinite(minScore) ? minScore : 85 },
        note: String(formData.get("note") ?? "").trim() || undefined,
      });
      router.push("/app/catalog");
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form action={submit} className="space-y-4 rounded-md border border-line bg-card p-6">
      <label className="block text-sm text-ink">
        Routine
        <select
          name="routineId"
          className="mt-1 block w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
        >
          {routines.map((routine) => (
            <option key={routine.id} value={routine.id}>
              {routine.name}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-wrap gap-4">
        <label className="block text-sm text-ink">
          Version
          <input
            type="number"
            name="version"
            min={1}
            step={1}
            defaultValue={1}
            className="mt-1 block w-28 rounded-sm border border-line bg-card px-3 py-2 text-sm"
          />
        </label>
        <label className="block text-sm text-ink">
          {catalogCopy.testScoreFloorLabel}
          <input
            type="number"
            name="minScore"
            min={0}
            max={100}
            defaultValue={85}
            className="mt-1 block w-28 rounded-sm border border-line bg-card px-3 py-2 text-sm"
          />
        </label>
      </div>
      <label className="block text-sm text-ink">
        Name
        <input
          type="text"
          name="name"
          required
          className="mt-1 block w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
        />
      </label>
      <label className="block text-sm text-ink">
        Tagline
        <input
          type="text"
          name="tagline"
          required
          className="mt-1 block w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
        />
      </label>
      <label className="block text-sm text-ink">
        Description
        <textarea
          name="description"
          required
          rows={4}
          className="mt-1 block w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
        />
      </label>
      <label className="block text-sm text-ink">
        Changelog note
        <input
          type="text"
          name="note"
          placeholder="What changed in this version"
          className="mt-1 block w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
        />
      </label>
      {error ? (
        <p role="alert" className="text-sm text-fail">
          {error}
        </p>
      ) : null}
      <Button type="submit" disabled={busy}>
        {catalogCopy.publishAction}
      </Button>
    </form>
  );
}

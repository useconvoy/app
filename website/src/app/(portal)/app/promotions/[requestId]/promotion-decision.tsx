"use client";

/**
 * The promote checkpoint on a promotion review: Promote or Send back
 * with a note. Both are promoter-gated server-side; the buttons here are a
 * lens. Decisions settle the website-side request and land an admin_audit
 * row; the page refetches so the settled state renders from the store, not
 * from a local flip.
 */
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/Button";
import { copy } from "@/lexicon";
import type { PromotionDecisionResult } from "@/lib/promotions/actions";
import { Textarea } from "@/components/ui/text-field";

export interface PromotionDecisionProps {
  requestId: string;
  canPromote: boolean;
  actions?: {
    promote: (requestId: string) => Promise<PromotionDecisionResult>;
    sendBack: (requestId: string, note: string) => Promise<PromotionDecisionResult>;
  };
}

export function PromotionDecision({ requestId, canPromote, actions }: PromotionDecisionProps) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function decide(call: (() => Promise<PromotionDecisionResult>) | undefined) {
    if (!call || pending) return;
    setPending(true);
    setMessage(null);
    const result = await call();
    if (result.kind === "accepted") {
      router.refresh();
    } else {
      setMessage(result.kind === "refused" ? result.message : "That request cannot be found.");
      setPending(false);
    }
  }

  if (!canPromote) {
    return <p className="text-sm text-muted">{copy.promotionNeedsPromoter}</p>;
  }

  return (
    <div id="promote" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button pending={pending} onClick={() => void decide(() => actions!.promote(requestId))}>
          {copy.promoteAction}
        </Button>
      </div>
      <label className="flex flex-col gap-1 text-sm text-muted">
        {copy.sendBackNoteLabel}
        <Textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={2}
          className="w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink"
        />
      </label>
      <div>
        <Button
          variant="secondary"
          pending={pending}
          disabled={note.trim().length === 0}
          onClick={() => void decide(() => actions!.sendBack(requestId, note.trim()))}
        >
          {copy.sendBackAction}
        </Button>
      </div>
      {message && (
        <p role="status" className="text-sm text-hold-text">
          {message}
        </p>
      )}
    </div>
  );
}

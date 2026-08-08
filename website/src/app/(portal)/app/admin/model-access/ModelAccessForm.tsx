"use client";

/**
 * The model access cards, one per provider, in the two-state design. When no
 * key is set, the card carries the security model and a password field to add
 * one. When a key is set, it shows only what is safe to show (the provider,
 * the last four, who set it, and the last verification) with Verify, Rotate,
 * and Remove. The key input is never populated from the server: the key
 * cannot be read back, so rotation is always a fresh entry, and removal asks
 * for the provider name typed back because it fails in-flight runs.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import { Field } from "@/components/ui/field";
import { TextField } from "@/components/ui/text-field";
import type { ModelKeyResult } from "@/lib/credentials/actions";
import { modelAccessCopy } from "@/lexicon";

export interface ProviderView {
  provider: string;
  displayName: string;
  prefix: string;
  configured: boolean;
  last4: string | null;
  setLine: string | null;
  verifyLine: string | null;
  verifyOk: boolean;
}

export interface ModelAccessFormProps {
  providers: ProviderView[];
  setAction: (input: { provider: string; key: string }) => Promise<ModelKeyResult>;
  verifyAction: (input: { provider: string }) => Promise<ModelKeyResult>;
  removeAction: (input: { provider: string; confirm: string }) => Promise<ModelKeyResult>;
}

export function ModelAccessForm({
  providers,
  setAction,
  verifyAction,
  removeAction,
}: ModelAccessFormProps) {
  return (
    <div className="space-y-4">
      {providers.map((view) => (
        <ProviderCard
          key={view.provider}
          view={view}
          setAction={setAction}
          verifyAction={verifyAction}
          removeAction={removeAction}
        />
      ))}
    </div>
  );
}

type Busy = "save" | "verify" | "remove" | null;

function reasonText(result: ModelKeyResult): string {
  return (result.reason && modelAccessCopy.reasonMessage[result.reason]) || modelAccessCopy.reasonMessage.unreachable!;
}

function ProviderCard({
  view,
  setAction,
  verifyAction,
  removeAction,
}: {
  view: ProviderView;
  setAction: ModelAccessFormProps["setAction"];
  verifyAction: ModelAccessFormProps["verifyAction"];
  removeAction: ModelAccessFormProps["removeAction"];
}) {
  const router = useRouter();
  const [rotating, setRotating] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [keyValue, setKeyValue] = useState("");
  const [confirmValue, setConfirmValue] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const showInput = !view.configured || rotating;

  function reset() {
    setKeyValue("");
    setConfirmValue("");
    setError(null);
  }

  async function onSave() {
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const result = await setAction({ provider: view.provider, key: keyValue });
      if (result.ok) {
        setNotice(modelAccessCopy.saved(result.last4 ?? ""));
        setRotating(false);
        reset();
        router.refresh();
      } else {
        setError(reasonText(result));
      }
    } catch {
      setError(modelAccessCopy.reasonMessage.unreachable!);
    } finally {
      setBusy(null);
    }
  }

  async function onVerify() {
    setBusy("verify");
    setError(null);
    setNotice(null);
    try {
      const result = await verifyAction({ provider: view.provider });
      if (result.ok) {
        setNotice(modelAccessCopy.verifiedOk);
        router.refresh();
      } else {
        setError(reasonText(result));
      }
    } catch {
      setError(modelAccessCopy.reasonMessage.unreachable!);
    } finally {
      setBusy(null);
    }
  }

  async function onRemove() {
    setBusy("remove");
    setError(null);
    setNotice(null);
    try {
      const result = await removeAction({ provider: view.provider, confirm: confirmValue });
      if (result.ok) {
        setNotice(modelAccessCopy.removed);
        setRemoving(false);
        reset();
        router.refresh();
      } else {
        setError(reasonText(result));
      }
    } catch {
      setError(modelAccessCopy.reasonMessage.unreachable!);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-4 rounded-md border border-line bg-card p-6">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-base font-medium text-ink">{view.displayName}</h2>
        {view.configured ? (
          <span className="font-mono text-xs text-muted">
            {modelAccessCopy.keyEnding(view.last4 ?? "")}
          </span>
        ) : null}
      </div>

      {view.configured ? (
        <dl className="space-y-1 text-sm text-muted">
          {view.setLine ? <dd>{view.setLine}</dd> : null}
          {view.verifyLine ? <dd>{view.verifyLine}</dd> : null}
        </dl>
      ) : (
        <p className="text-sm text-muted">{modelAccessCopy.emptyState}</p>
      )}

      {showInput ? (
        <div className="space-y-3">
          {rotating ? <p className="text-sm text-muted">{modelAccessCopy.rotateIntro}</p> : null}
          <Field
            label={modelAccessCopy.keyFieldLabel}
            hint={modelAccessCopy.keyFieldHint(view.displayName, view.prefix)}
          >
            <TextField
              type="password"
              // A name browsers will not treat as a saved credential, plus
              // autoComplete off, so a password manager never fills or offers
              // to store the key.
              name={`model-access-${view.provider}`}
              autoComplete="off"
              spellCheck={false}
              value={keyValue}
              onChange={(event) => setKeyValue(event.target.value)}
            />
          </Field>
          <div className="flex gap-2">
            <Button type="button" pending={busy === "save"} onClick={onSave}>
              {modelAccessCopy.verifyAndSave}
            </Button>
            {rotating ? (
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setRotating(false);
                  reset();
                }}
              >
                {modelAccessCopy.cancel}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {view.configured && !rotating && !removing ? (
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" pending={busy === "verify"} onClick={onVerify}>
            {modelAccessCopy.verify}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setNotice(null);
              setError(null);
              setRotating(true);
            }}
          >
            {modelAccessCopy.rotate}
          </Button>
          <Button
            type="button"
            variant="danger"
            onClick={() => {
              setNotice(null);
              setError(null);
              setRemoving(true);
            }}
          >
            {modelAccessCopy.remove}
          </Button>
        </div>
      ) : null}

      {view.configured && removing ? (
        <div className="space-y-3">
          <Field
            label={modelAccessCopy.removeConfirmLabel(view.displayName)}
            hint={modelAccessCopy.removeConfirmHint}
          >
            <TextField
              name={`model-access-confirm-${view.provider}`}
              autoComplete="off"
              value={confirmValue}
              onChange={(event) => setConfirmValue(event.target.value)}
            />
          </Field>
          <div className="flex gap-2">
            <Button type="button" variant="danger" pending={busy === "remove"} onClick={onRemove}>
              {modelAccessCopy.remove}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setRemoving(false);
                reset();
              }}
            >
              {modelAccessCopy.cancel}
            </Button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-fail">
          {error}
        </p>
      ) : null}
      {notice && !error ? (
        <p role="status" className="text-sm text-pass">
          {notice}
        </p>
      ) : null}
    </section>
  );
}

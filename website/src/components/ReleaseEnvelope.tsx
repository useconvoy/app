import { ENVELOPE, type EnvelopeFieldKey } from "@/content/homepage";

/**
 * The release envelope, the page's one persistent brand object: a dashed
 * oxide boundary with a descriptive tag on the border, holding five field
 * groups. Each group names its role in mono and carries a few example
 * chips; the evidence group takes the release line style, because evidence
 * is what lets a release move. `highlight` marks the groups a neighbouring
 * section is talking about; `compact` is the one-line strip, tagged with
 * the release identity that every stage shares.
 */
export function ReleaseEnvelope({
  highlight = [],
  compact = false,
  className = "",
}: {
  highlight?: readonly EnvelopeFieldKey[];
  compact?: boolean;
  className?: string;
}) {
  if (compact) {
    return (
      <div className={`relative rounded-lg border-[1.5px] border-dashed border-accent bg-surface px-5 pt-5 pb-4 ${className}`} data-envelope>
        <Tag label={ENVELOPE.identityLabel} />
        <ul className="flex flex-wrap gap-x-5 gap-y-2" aria-label="Release field groups">
          {ENVELOPE.fields.map((field) => {
            const on = highlight.includes(field.key);
            return (
              <li key={field.key} className="flex items-center gap-2" data-field={field.key}>
                <span aria-hidden="true" className={`h-2 w-2 flex-none rounded-full ${on ? "bg-accent" : "bg-strong"}`} />
                <span className={`text-[15px] leading-snug ${on ? "font-medium text-primary" : "text-secondary"}`}>{field.label}</span>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  return (
    <div className={className}>
      <div className="relative rounded-lg border-[1.5px] border-dashed border-accent bg-surface px-4 pt-7 pb-4 sm:px-6 sm:pb-6" data-envelope>
        <Tag label={ENVELOPE.label} />
        <ul className="grid gap-3 sm:grid-cols-2" aria-label="Release field groups">
          <li className="flex min-w-0 flex-col rounded border border-strong bg-surface-subtle px-4 py-3 sm:col-span-2" data-field="identity">
            <p className="text-[16px] leading-snug font-medium text-primary">{ENVELOPE.identityLabel}</p>
            <p className="type-meta mt-[2px] text-secondary">{ENVELOPE.identityNote}</p>
          </li>
          {ENVELOPE.fields.map((field) => {
            const evidence = field.key === "evidence";
            const on = highlight.includes(field.key);
            return (
              <li
                key={field.key}
                data-field={field.key}
                className={`flex min-w-0 flex-col gap-2 rounded border bg-surface px-4 py-3 ${evidence ? "border-dashed border-accent sm:col-span-2" : on ? "border-accent" : "border-subtle"}`}
              >
                <div>
                  <p className="text-[16px] leading-snug font-medium text-primary">{field.label}</p>
                  <p className="type-meta mt-[2px] text-secondary">{field.note}</p>
                </div>
                <ul className="flex flex-wrap gap-2" aria-label={`${field.label} examples`}>
                  {field.items.map((item) => (
                    <li
                      key={item}
                      className={`rounded-sm border px-2 py-[3px] font-mono text-[14px] leading-[1.4] ${
                        evidence ? "border-transparent bg-accent-tint text-accent-hover" : "border-subtle bg-surface-subtle text-secondary"
                      }`}
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/** The descriptive tag, sitting on the envelope's top border. */
function Tag({ label }: { label: string }) {
  return (
    <span className="absolute -top-[11px] left-4 inline-flex items-center bg-surface px-2 font-mono text-[13px] font-medium tracking-[0.02em] text-accent sm:left-6">
      {label}
    </span>
  );
}

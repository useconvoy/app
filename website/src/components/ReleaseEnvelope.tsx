import { ENVELOPE, type EnvelopeFieldKey } from "@/content/homepage";

/**
 * The release envelope, the page's one persistent brand object: a dashed
 * oxide boundary with its version tag on the border, holding five field
 * groups. Each group names its role in mono and carries a few conceptual
 * example chips; the evidence group takes the release line style, because
 * evidence is what lets a release move. `highlight` marks the groups a
 * neighbouring section is talking about; `compact` is the one-line strip.
 */
export function ReleaseEnvelope({
  highlight = [],
  compact = false,
  caption = true,
  className = "",
}: {
  highlight?: readonly EnvelopeFieldKey[];
  compact?: boolean;
  caption?: boolean;
  className?: string;
}) {
  if (compact) {
    return (
      <div className={`relative rounded-lg border-[1.5px] border-dashed border-accent bg-surface px-5 pt-5 pb-4 ${className}`} data-envelope>
        <Tag />
        <ul className="flex flex-wrap gap-x-5 gap-y-2" aria-label="Release field groups">
          {ENVELOPE.fields.map((field) => {
            const on = highlight.includes(field.key);
            return (
              <li key={field.key} className="flex items-center gap-2" data-field={field.key}>
                <span aria-hidden="true" className={`h-2 w-2 flex-none rounded-full ${on ? "bg-accent" : "bg-strong"}`} />
                <span className={`type-small ${on ? "font-medium text-primary" : "text-secondary"}`}>{field.label}</span>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  return (
    <figure className={`m-0 ${className}`}>
      <div className="relative rounded-lg border-[1.5px] border-dashed border-accent bg-surface px-4 pt-7 pb-4 sm:px-6 sm:pb-6" data-envelope>
        <Tag />
        <ul className="grid gap-3 sm:grid-cols-2" aria-label="Release field groups">
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
                  <p className="type-small font-medium text-primary">{field.label}</p>
                  <p className="font-mono text-[11px] leading-snug tracking-[0.02em] text-secondary">{field.note}</p>
                </div>
                <ul className="flex flex-wrap gap-2" aria-label={`${field.label} examples`}>
                  {field.items.map((item) => (
                    <li
                      key={item}
                      className={`whitespace-nowrap rounded-sm border px-2 py-[3px] font-mono text-[12px] leading-[1.4] ${
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
      {caption ? (
        <figcaption className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <span className="font-mono text-[12px] tracking-[0.02em] text-muted">{ENVELOPE.caption}</span>
          <span className="inline-flex items-center gap-2 text-[13px] text-secondary">
            <i aria-hidden="true" className="inline-block w-7 border-t-[1.5px] border-dashed border-accent" />
            Release · configuration · evidence
          </span>
        </figcaption>
      ) : null}
    </figure>
  );
}

/** The version tag, sitting on the envelope's top border. */
function Tag() {
  return (
    <span className="absolute -top-[11px] left-4 inline-flex items-center gap-2 bg-surface px-2 font-mono text-[13px] font-medium tracking-[0.02em] text-accent sm:left-6">
      <span>{ENVELOPE.version}</span>
      <span className="font-normal text-muted">· {ENVELOPE.versionNote}</span>
    </span>
  );
}

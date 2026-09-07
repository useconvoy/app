import { ENVELOPE } from "@/content/homepage";
import { Disclosure } from "./Disclosure";

/**
 * The release envelope, the page's one persistent brand object: a dashed
 * oxide boundary with a descriptive tag on the border, holding the six parts
 * of a release as labels only: release identity, then the five field groups,
 * two columns when there is room and one on a phone. Each part's short
 * definition and example contents live in one disclosure below the figure
 * (ReleaseDetails), so the object reads at a glance and the detail is one
 * tap away.
 */
export function ReleaseEnvelope({ className = "" }: { className?: string }) {
  return (
    <div className={className}>
      <div className="relative rounded-lg border-[1.5px] border-dashed border-accent bg-surface px-4 pt-6 pb-3 sm:px-5" data-envelope>
        <span className="absolute -top-[11px] left-4 inline-flex items-center bg-surface px-2 font-mono text-sm font-medium tracking-[0.02em] text-accent sm:left-5">
          {ENVELOPE.label}
        </span>
        <ul className="grid gap-x-6 sm:grid-cols-2" aria-label="Parts of a release">
          <li
            className="border-b border-subtle px-1 py-2 text-base leading-snug font-medium text-primary"
            data-field="identity"
          >
            {ENVELOPE.identityLabel}
          </li>
          {ENVELOPE.fields.map((field) => (
            <li
              key={field.key}
              data-field={field.key}
              className="border-b border-subtle px-1 py-2 text-base leading-snug font-medium text-primary last:border-b-0 sm:nth-last-[-n+2]:border-b-0"
            >
              {field.label}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** The optional detail: each release part with its definition and example contents, in one disclosure. */
export function ReleaseDetails({ className = "" }: { className?: string }) {
  return (
    <Disclosure summary={ENVELOPE.detailsLabel} summaryClassName="type-label text-primary" className={`border-t border-subtle ${className}`}>
      <p className="type-small">{ENVELOPE.detailsIntro}</p>
      <dl className="mt-3 grid gap-y-4" data-release-details>
        <div>
          <dt className="text-[0.9375rem] font-medium text-primary">{ENVELOPE.identityLabel}</dt>
          <dd className="type-meta mt-0.5 text-secondary">{ENVELOPE.identityNote}</dd>
        </div>
        {ENVELOPE.fields.map((field) => (
          <div key={field.key}>
            <dt className="text-[0.9375rem] font-medium text-primary">
              {field.label}
              <span className="type-meta ml-2 font-normal text-secondary">{field.note}</span>
            </dt>
            <dd className="mt-1.5">
              <ul className="flex flex-wrap gap-2">
                {field.items.map((item) => (
                  <li
                    key={item}
                    className={`rounded-sm border px-2 py-[2px] font-mono text-sm leading-[1.4] ${
                      field.key === "evidence" ? "border-transparent bg-accent-tint text-accent-hover" : "border-subtle bg-surface-subtle text-secondary"
                    }`}
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ))}
      </dl>
    </Disclosure>
  );
}

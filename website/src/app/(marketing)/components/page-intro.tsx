/** Shared intro block for marketing subpages: kicker, h1, and a lede. */
export function PageIntro({
  kicker,
  title,
  lede,
}: {
  kicker: string;
  title: string;
  lede: string;
}) {
  return (
    <div className="mx-auto max-w-3xl px-6 pt-16 pb-12 text-center sm:pt-24">
      <p className="font-mono text-xs tracking-widest text-muted uppercase">
        {kicker}
      </p>
      <h1 className="mt-4 font-display text-4xl font-medium text-ink sm:text-5xl">
        {title}
      </h1>
      <p className="mt-5 text-lg text-muted">{lede}</p>
    </div>
  );
}

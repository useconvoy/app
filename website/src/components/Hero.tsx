import Image from "next/image";
import { HERO, SITE } from "@/content/homepage";
import { HeroMotion } from "./HeroMotion";

/** The artwork is editorial; all product claims and actions remain live HTML. */
export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-heading" className="hero-section">
      <HeroMotion>
        <Image
          className="hero-art"
          src="/images/convoy-humanoid-illustrated.webp"
          alt="A hand-drawn industrial humanoid robot with a simple visor and mechanical joints packing a parcel in a warehouse."
          width={1536}
          height={1024}
          sizes="100vw"
          preload
        />
        <div className="hero-art-shade" aria-hidden="true" />
        <div className="hero-layout">
          <div className="hero-copy">
            <p className="type-eyebrow hero-eyebrow"><span aria-hidden="true" />{HERO.eyebrow}</p>
            <h1 id="hero-heading">Deploy AI models<br />{" "}to <em>real robots.</em></h1>
            <p className="type-lead hero-lead">{HERO.lede}</p>
            <div className="hero-actions">
              <a href={HERO.primary.href} className="btn btn-primary">{HERO.primary.label}<ArrowGlyph /></a>
              <a href={HERO.secondary.href} className="hero-secondary">{HERO.secondary.label}<span aria-hidden="true">↗</span></a>
              <a href="/app" className="hero-secondary">Open demo<span aria-hidden="true">↗</span></a>
            </div>
          </div>
        </div>
        <div className="hero-baseline">
          <span className="hero-stage"><span aria-hidden="true" />{SITE.stage}</span>
          <span>From trained policy to physical action</span>
          <a href="#workflow">Package <span aria-hidden="true">→</span> Qualify <span aria-hidden="true">→</span> Release <span aria-hidden="true">↓</span></a>
        </div>
      </HeroMotion>
    </section>
  );
}

export function ArrowGlyph() {
  return (
    <svg viewBox="0 0 16 16" className="btn-arrow h-4 w-4 flex-none" aria-hidden="true" focusable="false">
      <path d="M2 8h11m0 0L9 4m4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

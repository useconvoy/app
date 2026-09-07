import { BRAND, CONTACT_EMAIL, SITE } from "@/content/homepage";

/**
 * The two schema.org entities Google documents for identifying a site and
 * the company behind it: WebSite (site name) and Organization (logo, name,
 * contact). Only facts the page itself states: no address, legal name,
 * social profiles, ratings or product claims. The ids are stable fragment
 * URLs on the canonical page so later pages can reference the same entities.
 */
export const ORGANIZATION_ID = `${SITE.canonical}#organization`;
export const WEBSITE_ID = `${SITE.canonical}#website`;

export function structuredData(origin: string) {
  const logo = `${origin}${BRAND.icon512}`;
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": ORGANIZATION_ID,
        name: SITE.name,
        url: SITE.canonical,
        description: SITE.description,
        email: CONTACT_EMAIL,
        logo: { "@type": "ImageObject", "@id": `${SITE.canonical}#logo`, url: logo, contentUrl: logo, width: 512, height: 512 },
        image: { "@id": `${SITE.canonical}#logo` },
      },
      {
        "@type": "WebSite",
        "@id": WEBSITE_ID,
        name: SITE.name,
        url: SITE.canonical,
        description: SITE.description,
        inLanguage: "en-US",
        publisher: { "@id": ORGANIZATION_ID },
      },
    ],
  };
}

/** JSON for a <script type="application/ld+json">: a `<` inside a string may not close the element. */
export function serializeStructuredData(origin: string): string {
  return JSON.stringify(structuredData(origin)).replace(/</g, "\\u003c");
}

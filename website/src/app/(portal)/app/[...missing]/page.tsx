import { notFound } from "next/navigation";

/**
 * Catch-all for addresses under /app that match no real page. Without
 * this, an unknown console address would fall through to the root 404 and
 * its marketing shell; routing it here keeps the visitor inside the
 * console, where the portal's own not-found page renders.
 */
export default function MissingPortalPage(): never {
  notFound();
}

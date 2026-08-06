import { ogCard, OG_SIZE, OG_CONTENT_TYPE } from "@/lib/og";

export const alt =
  "Convoy Labs. Delegate the work, keep control. Routine work, done carefully.";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default async function Image() {
  return ogCard("Routine work, done carefully", [
    "Delegate the work.",
    "Keep control.",
  ]);
}

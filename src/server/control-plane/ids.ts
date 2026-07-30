import { randomBytes } from "node:crypto";

export function controlPlaneId(prefix: string): string {
  return `${prefix}_${randomBytes(12).toString("hex")}`;
}

export function workspaceSlug(name: string): string {
  const base = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 42);
  return `${base || "workspace"}-${randomBytes(3).toString("hex")}`;
}

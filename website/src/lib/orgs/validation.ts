/**
 * Pure validation for org, membership, and invite flows. No I/O here: the
 * server actions call these, and the unit suite exercises them directly.
 * Role and capability vocabularies mirror the DB check constraints and
 * the permissions matrix in lib/permissions; a drift between them is a
 * bug in this file.
 */
import { randomBytes } from "node:crypto";

import type { Capability, Role } from "@/lib/permissions";

export const ROLES: readonly Role[] = ["admin", "operator", "member", "viewer"];

export const CAPABILITIES: readonly Capability[] = ["approver", "promoter", "ship_improvements"];

/** Invite links live exactly seven days. */
export const INVITE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

/**
 * Normalize a capability list: trims, dedupes, and rejects the whole list
 * when any entry is outside the known vocabulary (null result). Rejecting
 * beats silently dropping: a typo in a grant must never look like success.
 */
export function parseCapabilities(values: readonly string[]): Capability[] | null {
  const seen = new Set<Capability>();
  for (const raw of values) {
    const value = raw.trim();
    if (value === "") continue;
    if (!(CAPABILITIES as readonly string[]).includes(value)) return null;
    seen.add(value as Capability);
  }
  return [...seen];
}

/** Tenant ids are opaque: a fixed prefix plus 12 hex chars of entropy. */
export function newTenantId(): string {
  return `tenant-${randomBytes(6).toString("hex")}`;
}

export function isTenantId(value: string): boolean {
  return /^tenant-[0-9a-f]{12}$/.test(value);
}

/** Invite tokens: 24 random bytes, hex-encoded (48 chars, URL-safe). */
export function newInviteToken(): string {
  return randomBytes(24).toString("hex");
}

export function isInviteToken(value: string): boolean {
  return /^[0-9a-f]{48}$/.test(value);
}

export function inviteExpiry(now: Date = new Date()): Date {
  return new Date(now.getTime() + INVITE_TTL_MS);
}

export type InviteVerdict = "ok" | "expired" | "revoked" | "accepted" | "invalid";

/**
 * Whether an invite row can still be accepted. Expiry wins over the stored
 * status so a stale 'pending' row past its deadline reads as expired.
 */
export function inviteVerdict(
  invite: { status: string; expiresAt: Date },
  now: Date = new Date(),
): InviteVerdict {
  if (invite.status === "revoked") return "revoked";
  if (invite.status === "accepted") return "accepted";
  if (invite.status !== "pending") return "invalid";
  if (invite.expiresAt.getTime() <= now.getTime()) return "expired";
  return "ok";
}

/**
 * Deliberately loose email shape check: one @, no whitespace, a dot in the
 * domain. Deliverability is the invite email's problem, not validation's.
 */
export function isEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

export function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}

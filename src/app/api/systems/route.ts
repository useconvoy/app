import { NextRequest, NextResponse } from "next/server";
import { db } from "@/server/db";
import { SANDBOX_ID } from "@/server/seed";

export const dynamic = "force-dynamic";

// The simulated external systems, per environment (spec §6.5): the demo's
// HubSpot portal, Gmail inbox, Docs drive, and Slack channel.
export async function GET(req: NextRequest) {
  const env = req.nextUrl.searchParams.get("env") ?? SANDBOX_ID;
  const d = db();
  const notFixture = <T extends { namespace?: string }>(x: T) => !x.namespace;
  return NextResponse.json({
    environment: d.environments.find((e) => e.id === env),
    deals: d.crmDeals.filter((x) => x.environmentId === env && notFixture(x)),
    leads: d.crmLeads.filter((x) => x.environmentId === env),
    docs: d.docs
      .filter((x) => x.environmentId === env && notFixture(x))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .slice(0, 30),
    emails: d.emails
      .filter((x) => x.environmentId === env && notFixture(x))
      .sort((a, b) => b.ts.localeCompare(a.ts))
      .slice(0, 30),
    chatMessages: d.chatMessages
      .filter((x) => x.environmentId === env && notFixture(x))
      .sort((a, b) => b.ts.localeCompare(a.ts))
      .slice(0, 30),
  });
}

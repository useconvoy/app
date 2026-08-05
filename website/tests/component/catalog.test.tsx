/**
 * Catalog surfaces: storefront cards from the storefront
 * jsonb with the quiet update-available chip, and the compatibility
 * report's four states: all green, mapping needed, connect prompt, and
 * the vendor-specific portability line.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CatalogCards, type CatalogCardItem } from "@/app/(portal)/app/catalog/CatalogCards";
import { CompatibilityReportView } from "@/app/(portal)/app/catalog/CompatibilityReportView";
import type { CompatibilityReport } from "@/lib/api/environments";
import { expectNoAxeViolations } from "../support/axe";

const cards: CatalogCardItem[] = [
  {
    id: "entry-access-review",
    name: "Quarterly user access review",
    tagline: "Reconciles access and chases sign-offs for you.",
    testScoreFloor: 85,
    installed: true,
    updateAvailable: true,
  },
  {
    id: "entry-vendor-check",
    name: "Vendor due-diligence document refresh",
    tagline: "Keeps vendor documents current.",
    testScoreFloor: 90,
    installed: false,
    updateAvailable: false,
  },
];

const systemNames = {
  identity_provider: "Identity provider",
  messaging: "Messaging",
  crm: "CRM",
  "crm:salesforce": "Salesforce",
  "crm:hubspot": "HubSpot",
};

function report(overrides: Partial<CompatibilityReport>): CompatibilityReport {
  return {
    workspaceId: "workspace-compliance",
    greenChecks: [],
    mappingChoices: [],
    connectPrompts: [],
    vendorSpecificToolCount: 0,
    ...overrides,
  };
}

describe("CatalogCards", () => {
  it("renders storefront names, taglines, and the test score floor", async () => {
    const { container } = render(<CatalogCards entries={cards} />);
    expect(screen.getByText("Quarterly user access review")).toBeInTheDocument();
    expect(screen.getByText("Reconciles access and chases sign-offs for you.")).toBeInTheDocument();
    expect(screen.getByText("Ships only above 85")).toBeInTheDocument();
    expect(screen.getByText("Ships only above 90")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("shows the update-available chip only when the snapshot moved past the pin", () => {
    render(<CatalogCards entries={cards} />);
    const updated = screen.getAllByText("Update available");
    expect(updated).toHaveLength(1);
    // The chip is a quiet mono fact, never a second status on the same card.
    expect(screen.queryByText("Installed")).not.toBeInTheDocument();
  });

  it("marks an installed entry without an update as installed", () => {
    render(
      <CatalogCards
        entries={[{ ...cards[0]!, updateAvailable: false }]}
      />,
    );
    expect(screen.getByText("Installed")).toBeInTheDocument();
    expect(screen.queryByText("Update available")).not.toBeInTheDocument();
  });

  it("renders the empty storefront state", () => {
    render(<CatalogCards entries={[]} />);
    expect(screen.getByText("Nothing on the storefront yet")).toBeInTheDocument();
  });
});

describe("CompatibilityReportView", () => {
  it("all green: the ready line, a check per system, fully portable", async () => {
    const { container } = render(
      <CompatibilityReportView
        report={report({ greenChecks: ["identity_provider", "messaging"] })}
        systemNames={systemNames}
      />,
    );
    expect(
      screen.getByText("Everything this routine needs is already connected."),
    ).toBeInTheDocument();
    expect(screen.getByText("Identity provider is connected")).toBeInTheDocument();
    expect(screen.getByText("Messaging is connected")).toBeInTheDocument();
    expect(screen.getByText("Fully portable across vendors")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("mapping needed: a labeled picker with the candidate systems", () => {
    render(
      <CompatibilityReportView
        report={report({
          mappingChoices: [{ systemId: "crm", options: ["crm:salesforce", "crm:hubspot"] }],
        })}
        systemNames={systemNames}
      />,
    );
    const picker = screen.getByLabelText("Choose which CRM this routine should use");
    expect(picker).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Salesforce" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "HubSpot" })).toBeInTheDocument();
    expect(
      screen.queryByText("Everything this routine needs is already connected."),
    ).not.toBeInTheDocument();
  });

  it("connect prompt: names the missing system first", () => {
    render(
      <CompatibilityReportView
        report={report({ connectPrompts: ["messaging"] })}
        systemNames={systemNames}
      />,
    );
    expect(screen.getByText("Connect Messaging first")).toBeInTheDocument();
  });

  it("vendor-specific note: the portable-except line with a count", () => {
    render(
      <CompatibilityReportView
        report={report({ greenChecks: ["crm"], vendorSpecificToolCount: 2 })}
        systemNames={systemNames}
      />,
    );
    expect(screen.getByText("Portable except 2 vendor-specific tools")).toBeInTheDocument();
    expect(screen.queryByText("Fully portable across vendors")).not.toBeInTheDocument();
  });
});

/**
 * Admin close-out surfaces (DESIGN §5 Admin row, §9): policies form
 * validation, the audit table's friendly dates and mono facts, the
 * phase-1 API access page containing nothing key-shaped, and the billing
 * page's plain unconfigured state.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: navigation.refresh, push: navigation.push }),
}));

import { ApiAccessNotice } from "@/app/(portal)/app/admin/api-access/ApiAccessNotice";
import { AuditTable } from "@/app/(portal)/app/admin/audit/AuditTable";
import { BillingOverview } from "@/app/(portal)/app/admin/billing/BillingOverview";
import { PoliciesForm } from "@/app/(portal)/app/admin/policies/PoliciesForm";
import { fixtureInvoices } from "@/lib/billing/fixtures";
import { friendlyDateTime } from "@/lib/format";
import { expectNoAxeViolations } from "../support/axe";

beforeEach(() => {
  navigation.refresh.mockClear();
});

const initialPolicies = {
  defaultRunBudgetCapUsd: 75,
  monthlySpendNoticeUsd: 500,
  viewerEvidenceExport: true,
};

describe("PoliciesForm", () => {
  it("blocks a non-positive amount with a visible error and no save call", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<PoliciesForm initial={initialPolicies} save={save} />);

    const cap = screen.getByLabelText(/Default budget per run/);
    await user.clear(cap);
    await user.type(cap, "0");
    await user.click(screen.getByRole("button", { name: "Save policies" }));

    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an amount above zero");
  });

  it("blocks a spend notice below the per-run cap", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<PoliciesForm initial={initialPolicies} save={save} />);

    const notice = screen.getByLabelText(/Monthly spend notice/);
    await user.clear(notice);
    await user.type(notice, "10");
    await user.click(screen.getByRole("button", { name: "Save policies" }));

    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The spend notice should not be below the per-run cap",
    );
  });

  it("saves clean values, toggle included", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    const { container } = render(<PoliciesForm initial={initialPolicies} save={save} />);

    await user.click(screen.getByLabelText(/Viewers can export the evidence binder/));
    await user.click(screen.getByRole("button", { name: "Save policies" }));

    expect(save).toHaveBeenCalledWith({
      defaultRunBudgetCapUsd: 75,
      monthlySpendNoticeUsd: 500,
      viewerEvidenceExport: false,
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Policies saved.");
    expect(navigation.refresh).toHaveBeenCalled();
    await expectNoAxeViolations(container);
  });
});

describe("AuditTable", () => {
  const ts = "2026-08-03T09:12:00Z";
  const rows = [
    {
      id: "audit-1",
      action: "catalog.entry_published",
      subject: "routine-access-review@v2",
      ts,
      actorName: "J. Doe",
    },
    {
      id: "audit-2",
      action: "member.role_changed",
      subject: "user-2 to operator",
      ts,
      actorName: null,
    },
  ];

  it("renders friendly dates, resolved actors, and mono facts", async () => {
    const { container } = render(<AuditTable rows={rows} />);
    // Friendly date, never raw ISO.
    expect(screen.getAllByText(friendlyDateTime(ts)).length).toBeGreaterThan(0);
    expect(screen.queryByText(ts)).not.toBeInTheDocument();
    expect(screen.getByText("J. Doe")).toBeInTheDocument();
    expect(screen.getByText("Former member")).toBeInTheDocument();
    // Facts from the log are mono.
    expect(screen.getByText("catalog.entry_published").className).toContain("font-mono");
    expect(screen.getByText("routine-access-review@v2").className).toContain("font-mono");
    await expectNoAxeViolations(container);
  });

  it("renders the empty state when nothing matches", () => {
    render(<AuditTable rows={[]} />);
    expect(screen.getByText("No entries match")).toBeInTheDocument();
  });
});

describe("ApiAccessNotice", () => {
  it("explains phase 1 and contains nothing key-shaped", async () => {
    const { container } = render(<ApiAccessNotice controlPlaneUrl="http://localhost:8700" />);
    expect(screen.getByText(/managed by Convoy/)).toBeInTheDocument();
    expect(screen.getByText("http://localhost:8700")).toBeInTheDocument();
    const text = container.textContent ?? "";
    // No key material, ever: nothing secret-key- or webhook-secret-shaped.
    expect(text).not.toMatch(/sk[-_][a-z0-9]/i);
    expect(text).not.toMatch(/whsec/i);
    expect(text).not.toMatch(/api[-_ ]?key:/i);
    await expectNoAxeViolations(container);
  });
});

describe("BillingOverview", () => {
  it("renders the plain unconfigured state with sample invoices", async () => {
    const { container } = render(
      <BillingOverview plan={null} status={null} configured={false} invoices={fixtureInvoices} />,
    );
    expect(screen.getByText("Billing is handled by your Convoy contact.")).toBeInTheDocument();
    expect(screen.getAllByText("No plan on file yet").length).toBeGreaterThan(0);
    expect(
      screen.getByText("Sample invoices are shown until billing is connected."),
    ).toBeInTheDocument();
    // No portal button and no outbound links while unconfigured.
    expect(screen.queryByRole("button", { name: "Manage payment details" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View invoice" })).not.toBeInTheDocument();
    // Amounts render through format.money.
    expect(screen.getAllByText("$1500").length).toBeGreaterThan(0);
    await expectNoAxeViolations(container);
  });
});

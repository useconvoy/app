import type { Metadata } from "next";
import { Checkbox } from "@/components/ui/choice";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { TextField } from "@/components/ui/text-field";
import Link from "next/link";

import { friendlyDate } from "@/lib/format";
import { requireAdminPage } from "@/lib/orgs/admin-gate";
import {
  inviteMember,
  removeMember,
  revokeInvite,
  updateMemberCapabilities,
  updateMemberRole,
} from "@/lib/orgs/actions";
import { listInvites, listMembers } from "@/lib/orgs/queries";
import { CAPABILITIES, ROLES } from "@/lib/orgs/validation";

export const metadata: Metadata = { title: "Members" };

const ROLE_LABELS: Record<string, string> = {
  admin: "Admin",
  operator: "Operator",
  member: "Member",
  viewer: "Viewer",
};

const CAPABILITY_LABELS: Record<string, string> = {
  approver: "Approver",
  promoter: "Promoter",
  ship_improvements: "Ship improvements",
};

async function inviteAction(formData: FormData) {
  "use server";
  await inviteMember(String(formData.get("email") ?? ""), String(formData.get("role") ?? ""));
}

async function revokeInviteAction(formData: FormData) {
  "use server";
  await revokeInvite(String(formData.get("inviteId") ?? ""));
}

async function updateRoleAction(formData: FormData) {
  "use server";
  await updateMemberRole(String(formData.get("userId") ?? ""), String(formData.get("role") ?? ""));
}

async function updateCapabilitiesAction(formData: FormData) {
  "use server";
  await updateMemberCapabilities(
    String(formData.get("userId") ?? ""),
    formData.getAll("capabilities").map(String),
  );
}

async function removeMemberAction(formData: FormData) {
  "use server";
  await removeMember(String(formData.get("userId") ?? ""));
}

export default async function MembersPage() {
  const { session } = await requireAdminPage();
  // APP_URL is the deployment's public origin; without it the admin still
  // gets a working path, just one they have to put a host in front of.
  const inviteBase = (process.env.APP_URL ?? "").trim().replace(/\/+$/, "");
  const [members, invites] = await Promise.all([
    listMembers(session.orgId),
    listInvites(session.orgId),
  ]);
  const openInvites = invites.filter((invite) => invite.status === "pending");
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">Members</h1>
        <p className="mt-1 text-sm text-muted">
          Roles decide what people can do. Capabilities add narrow extras on top.
        </p>
      </header>

      <section className="rounded-md border border-line bg-card p-6">
        <h2 className="text-base font-medium text-ink">Invite someone</h2>
        {/* Fields stack full width on phones and sit in a row from md up. */}
        <form action={inviteAction} className="mt-4 flex flex-col gap-3 md:flex-row md:flex-wrap md:items-end">
          <Field label="Email">
            <TextField type="email" name="email" required className="md:w-64" />
          </Field>
          <Field label="Role">
            <Select name="role" defaultValue="member">
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </Select>
          </Field>
          <button
            type="submit"
            className="rounded-sm bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep max-md:w-full max-md:py-2.5"
          >
            Send invite
          </button>
        </form>
        <p className="mt-2 text-xs text-muted">Invites expire after 7 days.</p>
      </section>

      {openInvites.length > 0 ? (
        <section className="rounded-md border border-line bg-card p-6">
          <h2 className="text-base font-medium text-ink">Pending invites</h2>
          <ul className="mt-4 divide-y divide-line-soft">
            {openInvites.map((invite) => (
              <li key={invite.id} className="flex flex-wrap items-center gap-3 py-3">
                <span className="font-mono text-xs text-ink">{invite.email}</span>
                <span className="font-mono text-xs uppercase text-muted">{invite.role}</span>
                <span className="font-mono text-xs text-muted">
                  EXPIRES {friendlyDate(invite.expiresAt)}
                </span>
                {/* The whole link, because sending it is a manual step:
                    nothing emails an invite today, so what the admin needs
                    here is something they can select and paste, not a path
                    they have to reassemble a host in front of. */}
                <input
                  readOnly
                  aria-label={`Invite link for ${invite.email}`}
                  value={`${inviteBase}/invite/${invite.token}`}
                  className="min-w-0 flex-1 truncate rounded-sm border border-line-soft bg-field px-2 py-1 font-mono text-xs text-muted max-md:basis-full"
                />
                <form action={revokeInviteAction}>
                  <input type="hidden" name="inviteId" value={invite.id} />
                  <button
                    type="submit"
                    className="rounded-sm border border-line px-3 py-1 text-xs text-fail hover:border-fail"
                  >
                    Revoke
                  </button>
                </form>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="rounded-md border border-line bg-card p-6">
        <h2 className="text-base font-medium text-ink">People</h2>
        {/* relative so the sr-only header (absolutely positioned) is clipped
            with the table instead of widening the page. */}
        <div className="relative mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs uppercase text-muted">
                <th className="py-2 pr-4 font-medium">Name</th>
                <th className="py-2 pr-4 font-medium">Email</th>
                <th className="py-2 pr-4 font-medium">Role</th>
                <th className="py-2 pr-4 font-medium">Capabilities</th>
                <th className="py-2 pr-4 font-medium">Status</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-soft align-top">
              {members.map((member) => {
                const self = member.userId === session.userId;
                return (
                  <tr key={member.userId}>
                    <td className="py-3 pr-4 text-ink">
                      {member.name}
                      {self ? <span className="ml-2 text-xs text-muted">(you)</span> : null}
                    </td>
                    <td className="py-3 pr-4 font-mono text-xs text-muted">{member.email}</td>
                    <td className="py-3 pr-4">
                      {self ? (
                        <span className="font-mono text-xs uppercase text-muted">{member.role}</span>
                      ) : (
                        <form action={updateRoleAction} className="flex items-center gap-2">
                          <input type="hidden" name="userId" value={member.userId} />
                          <Select
                            name="role"
                            defaultValue={member.role}
                            className="w-auto py-1 text-xs"
                          >
                            {ROLES.map((role) => (
                              <option key={role} value={role}>
                                {ROLE_LABELS[role]}
                              </option>
                            ))}
                          </Select>
                          <button
                            type="submit"
                            className="rounded-sm border border-line px-2 py-1 text-xs text-ink hover:border-pine"
                          >
                            Update
                          </button>
                        </form>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <form action={updateCapabilitiesAction} className="flex flex-wrap items-center gap-2">
                        <input type="hidden" name="userId" value={member.userId} />
                        {CAPABILITIES.map((capability) => (
                          <Checkbox
                            key={capability}
                            name="capabilities"
                            value={capability}
                            defaultChecked={member.capabilities.includes(capability)}
                            label={
                              <span className="text-xs">{CAPABILITY_LABELS[capability]}</span>
                            }
                          />
                        ))}
                        <button
                          type="submit"
                          className="rounded-sm border border-line px-2 py-1 text-xs text-ink hover:border-pine"
                        >
                          Save
                        </button>
                      </form>
                    </td>
                    <td className="py-3 pr-4 font-mono text-xs uppercase text-muted">{member.status}</td>
                    <td className="py-3">
                      {self ? null : (
                        <form action={removeMemberAction}>
                          <input type="hidden" name="userId" value={member.userId} />
                          <button
                            type="submit"
                            className="rounded-sm border border-line px-3 py-1 text-xs text-fail hover:border-fail"
                          >
                            Remove
                          </button>
                        </form>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

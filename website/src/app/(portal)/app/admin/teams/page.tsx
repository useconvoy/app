import type { Metadata } from "next";
import Link from "next/link";

import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { addTeamMember, createTeam, deleteTeam, removeTeamMember } from "@/lib/orgs/actions";
import { listMembers, listTeamMembers, listTeams } from "@/lib/orgs/queries";

export const metadata: Metadata = { title: "Teams" };

async function createTeamAction(formData: FormData) {
  "use server";
  await createTeam(String(formData.get("name") ?? ""));
}

async function deleteTeamAction(formData: FormData) {
  "use server";
  await deleteTeam(String(formData.get("teamId") ?? ""));
}

async function addTeamMemberAction(formData: FormData) {
  "use server";
  await addTeamMember(String(formData.get("teamId") ?? ""), String(formData.get("userId") ?? ""));
}

async function removeTeamMemberAction(formData: FormData) {
  "use server";
  await removeTeamMember(String(formData.get("teamId") ?? ""), String(formData.get("userId") ?? ""));
}

export default async function TeamsPage() {
  const { session } = await requireAdminPage();
  const [teams, members] = await Promise.all([listTeams(session.orgId), listMembers(session.orgId)]);
  const rosters = await Promise.all(teams.map((team) => listTeamMembers(team.id)));
  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">Teams</h1>
        <p className="mt-1 text-sm text-muted">
          Teams route checkpoints and power filters. They never change what a person is allowed to
          do.
        </p>
      </header>

      <section className="rounded-md border border-line bg-card p-6">
        <h2 className="text-base font-medium text-ink">Create a team</h2>
        <form action={createTeamAction} className="mt-4 flex flex-wrap items-end gap-3">
          <label className="block text-sm text-ink">
            Team name
            <input
              type="text"
              name="name"
              required
              minLength={2}
              maxLength={80}
              className="mt-1 block w-64 rounded-sm border border-line bg-card px-3 py-2 text-sm"
            />
          </label>
          <button
            type="submit"
            className="rounded-sm bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Create team
          </button>
        </form>
      </section>

      {teams.length === 0 ? (
        <section className="rounded-md border border-line bg-card p-6">
          <p className="text-sm text-muted">No teams yet. Create one to route work to a group.</p>
        </section>
      ) : (
        teams.map((team, index) => {
          const roster = rosters[index] ?? [];
          const onTeam = new Set(roster.map((person) => person.userId));
          const addable = members.filter(
            (member) => member.status === "active" && !onTeam.has(member.userId),
          );
          return (
            <section key={team.id} className="rounded-md border border-line bg-card p-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-base font-medium text-ink">{team.name}</h2>
                  <p className="mt-1 font-mono text-xs uppercase text-muted">
                    {roster.length} {roster.length === 1 ? "person" : "people"}
                  </p>
                </div>
                <form action={deleteTeamAction}>
                  <input type="hidden" name="teamId" value={team.id} />
                  <button
                    type="submit"
                    className="rounded-sm border border-line px-3 py-1 text-xs text-fail hover:border-fail"
                  >
                    Delete team
                  </button>
                </form>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                {roster.map((person) => (
                  <form
                    key={person.userId}
                    action={removeTeamMemberAction}
                    className="flex items-center gap-2 rounded-full border border-line bg-field px-3 py-1"
                  >
                    <input type="hidden" name="teamId" value={team.id} />
                    <input type="hidden" name="userId" value={person.userId} />
                    <span className="text-xs text-ink">{person.name}</span>
                    <button
                      type="submit"
                      aria-label={`Remove ${person.name} from ${team.name}`}
                      className="text-xs text-muted hover:text-fail"
                    >
                      &times;
                    </button>
                  </form>
                ))}
                {roster.length === 0 ? (
                  <p className="text-xs text-muted">No one on this team yet.</p>
                ) : null}
              </div>
              {addable.length > 0 ? (
                <form action={addTeamMemberAction} className="mt-4 flex flex-wrap items-center gap-2">
                  <input type="hidden" name="teamId" value={team.id} />
                  <label className="text-xs text-muted" htmlFor={`add-${team.id}`}>
                    Add someone
                  </label>
                  <select
                    id={`add-${team.id}`}
                    name="userId"
                    className="rounded-sm border border-line bg-card px-2 py-1 text-xs"
                  >
                    {addable.map((member) => (
                      <option key={member.userId} value={member.userId}>
                        {member.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="submit"
                    className="rounded-sm border border-line px-2 py-1 text-xs text-ink hover:border-pine"
                  >
                    Add
                  </button>
                </form>
              ) : null}
            </section>
          );
        })
      )}
    </div>
  );
}

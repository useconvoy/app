import { createHash } from "node:crypto";
import { bearerToken, tokenMatches } from "../../../packages/runtime/src/index.mjs";

function reply(body, { status = 200, principalId } = {}) {
  return {
    status,
    headers: { "content-type": "application/json", "x-github-api-version-selected": "2022-11-28" },
    body,
    principalId,
  };
}

function authenticate(headers, identities) {
  const token = bearerToken(headers);
  return identities.find((identity) => tokenMatches(identity, token));
}

function repository(state, name) {
  return state.repositories.find((candidate) => candidate.fullName === name);
}

export const githubProvider = {
  id: "github",

  async seed(fixture = {}) {
    return {
      repositories: structuredClone(fixture.repositories ?? []),
    };
  },

  async handle({ request, state, identities, clock, emitWebhook }) {
    const principal = authenticate(request.headers, identities);
    if (!principal) return reply({ message: "Bad credentials", documentation_url: "https://docs.github.com/rest" }, { status: 401 });
    const principalId = principal.principalId;
    const pathname = request.path.replace(/\/$/, "");

    if (request.method === "GET" && pathname === "/user") {
      return reply({
        login: principal.login,
        id: principal.numericId ?? 1001,
        type: "Bot",
        site_admin: false,
      }, { principalId });
    }

    const issuesMatch = /^\/repos\/([^/]+\/[^/]+)\/issues$/.exec(pathname);
    if (issuesMatch) {
      const repo = repository(state, issuesMatch[1]);
      if (!repo) return reply({ message: "Not Found" }, { status: 404, principalId });
      if (request.method === "GET") {
        const wantedState = request.query.state ?? "open";
        const issues = repo.issues.filter((issue) => wantedState === "all" || issue.state === wantedState);
        return reply(issues.map((issue) => ({
          ...issue,
          url: `https://api.github.test/repos/${repo.fullName}/issues/${issue.number}`,
          html_url: `https://github.test/${repo.fullName}/issues/${issue.number}`,
          repository_url: `https://api.github.test/repos/${repo.fullName}`,
        })), { principalId });
      }
      if (request.method === "POST") {
        if (!request.body.title) return reply({ message: "Validation Failed", errors: [{ field: "title", code: "missing_field" }] }, { status: 422, principalId });
        const issue = {
          id: (repo.issues.at(-1)?.id ?? 1000) + 1,
          number: (repo.issues.at(-1)?.number ?? 0) + 1,
          title: request.body.title,
          body: request.body.body ?? "",
          state: "open",
          user: { login: principal.login, id: principal.numericId ?? 1001, type: "Bot" },
          created_at: clock.now,
          updated_at: clock.now,
        };
        repo.issues.push(issue);
        emitWebhook({
          type: "issues",
          occurredAt: clock.now,
          payload: { action: "opened", issue, repository: { full_name: repo.fullName }, sender: issue.user },
        });
        return reply({
          ...issue,
          url: `https://api.github.test/repos/${repo.fullName}/issues/${issue.number}`,
          html_url: `https://github.test/${repo.fullName}/issues/${issue.number}`,
        }, { status: 201, principalId });
      }
    }

    const contentMatch = /^\/repos\/([^/]+\/[^/]+)\/contents\/(.+)$/.exec(pathname);
    if (request.method === "GET" && contentMatch) {
      const repo = repository(state, contentMatch[1]);
      const filePath = decodeURIComponent(contentMatch[2]);
      const file = repo?.files?.find((candidate) => candidate.path === filePath && (!request.query.ref || candidate.ref === request.query.ref));
      if (!file) return reply({ message: "Not Found" }, { status: 404, principalId });
      const content = Buffer.from(file.content, "utf8").toString("base64");
      return reply({
        type: "file",
        encoding: "base64",
        size: Buffer.byteLength(file.content),
        name: filePath.split("/").at(-1),
        path: filePath,
        content,
        sha: createHash("sha1").update(file.content).digest("hex"),
        url: `https://api.github.test/repos/${repo.fullName}/contents/${filePath}`,
      }, { principalId });
    }

    return reply({ message: "Not Found", documentation_url: "https://docs.github.com/rest" }, { status: 404, principalId });
  },
};

import { createHash } from "node:crypto";
import { bearerToken, tokenMatches } from "../../../packages/runtime/src/index.mjs";

function reply(body, { status = 200, principalId } = {}) {
  return { status, headers: { "content-type": "application/json" }, body, principalId };
}

function decodeIssuer(assertion) {
  try {
    const payload = assertion.split(".")[1];
    return JSON.parse(Buffer.from(payload, "base64url").toString("utf8")).iss;
  } catch {
    return null;
  }
}

function findPrincipal(request, identities, state) {
  const token = bearerToken(request.headers);
  const issued = state.accessTokens[token];
  if (issued) return identities.find((identity) => identity.principalId === issued.principalId);
  return identities.find((identity) => tokenMatches(identity, token));
}

function visible(resource, principal) {
  const grants = resource.sharedWith ?? [];
  return grants.includes("*") || grants.includes(principal.principalId) || grants.includes(principal.clientEmail);
}

function columnIndex(label) {
  let result = 0;
  for (const character of label.toUpperCase()) result = result * 26 + character.charCodeAt(0) - 64;
  return result - 1;
}

function sliceRange(values, range) {
  const coordinate = range.includes("!") ? range.split("!").slice(1).join("!") : range;
  const match = /^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/i.exec(coordinate);
  if (!match) return structuredClone(values);
  const startColumn = columnIndex(match[1]);
  const startRow = Number(match[2]) - 1;
  const endColumn = match[3] ? columnIndex(match[3]) : Math.max(...values.map((row) => row.length), 1) - 1;
  const endRow = match[4] ? Number(match[4]) - 1 : values.length - 1;
  return values.slice(startRow, endRow + 1).map((row) => row.slice(startColumn, endColumn + 1));
}

export const googleWorkspaceProvider = {
  id: "google",

  async seed(fixture = {}) {
    return {
      files: structuredClone(fixture.files ?? []),
      spreadsheets: structuredClone(fixture.spreadsheets ?? []),
      accessTokens: {},
      tokenSequence: 1,
    };
  },

  async handle({ request, state, identities, clock, emitWebhook, sandboxId }) {
    const pathname = request.path.replace(/\/$/, "");
    if (request.method === "POST" && ["/oauth2/token", "/token"].includes(pathname)) {
      const issuer = decodeIssuer(request.body.assertion ?? "");
      const principal = identities.find((identity) => identity.clientEmail === issuer);
      if (!principal) return reply({ error: "invalid_grant", error_description: "Unknown service account" }, { status: 400 });
      const accessToken = `ya29.sandbox.${createHash("sha256").update(`${sandboxId}:${issuer}:${state.tokenSequence}`).digest("hex").slice(0, 28)}`;
      state.tokenSequence += 1;
      state.accessTokens[accessToken] = { principalId: principal.principalId, issuedAt: clock.now };
      return reply({ access_token: accessToken, expires_in: 3600, token_type: "Bearer" }, { principalId: principal.principalId });
    }

    const principal = findPrincipal(request, identities, state);
    if (!principal) return reply({ error: { code: 401, message: "Invalid Credentials", status: "UNAUTHENTICATED" } }, { status: 401 });
    const principalId = principal.principalId;

    if (request.method === "GET" && pathname === "/drive/v3/files") {
      const parent = /'([^']+)'\s+in\s+parents/.exec(request.query.q ?? "")?.[1];
      const mimeType = /mimeType\s*=\s*'([^']+)'/.exec(request.query.q ?? "")?.[1];
      const pageSize = Math.min(Number(request.query.pageSize ?? 100), 1000);
      const files = state.files.filter((file) =>
        (!parent || file.parents?.includes(parent)) &&
        (!mimeType || file.mimeType === mimeType) &&
        !file.trashed &&
        visible(file, principal),
      ).slice(0, pageSize).map(({ sharedWith, ...file }) => file);
      return reply({ files }, { principalId });
    }

    const valueMatch = /^\/sheets\/v4\/spreadsheets\/([^/]+)\/values\/(.+)$/.exec(pathname);
    if (valueMatch) {
      const spreadsheetId = decodeURIComponent(valueMatch[1]);
      const isAppend = valueMatch[2].endsWith(":append");
      const encodedRange = isAppend ? valueMatch[2].slice(0, -":append".length) : valueMatch[2];
      const range = decodeURIComponent(encodedRange);
      const spreadsheet = state.spreadsheets.find((candidate) => candidate.spreadsheetId === spreadsheetId);
      if (!spreadsheet || !visible(spreadsheet, principal)) {
        return reply({ error: { code: 404, message: "Requested entity was not found.", status: "NOT_FOUND" } }, { status: 404, principalId });
      }
      const sheetName = range.includes("!") ? range.split("!")[0] : Object.keys(spreadsheet.sheets)[0];
      const values = spreadsheet.sheets[sheetName];
      if (!values) return reply({ error: { code: 400, message: `Unable to parse range: ${range}`, status: "INVALID_ARGUMENT" } }, { status: 400, principalId });
      if (request.method === "GET" && !isAppend) {
        return reply({ range, majorDimension: "ROWS", values: sliceRange(values, range) }, { principalId });
      }
      if (request.method === "POST" && isAppend) {
        const rows = request.body.values;
        if (!Array.isArray(rows) || !rows.every(Array.isArray)) {
          return reply({ error: { code: 400, message: "Invalid values", status: "INVALID_ARGUMENT" } }, { status: 400, principalId });
        }
        const start = values.length + 1;
        for (const row of rows) values.push(structuredClone(row));
        const end = values.length;
        emitWebhook({
          type: "google.sheets.rows_appended",
          occurredAt: clock.now,
          payload: { spreadsheetId, sheetName, startRow: start, endRow: end, principalId },
        });
        return reply({
          spreadsheetId,
          tableRange: `${sheetName}!A1`,
          updates: {
            spreadsheetId,
            updatedRange: `${sheetName}!A${start}:A${end}`,
            updatedRows: rows.length,
            updatedColumns: Math.max(...rows.map((row) => row.length), 0),
            updatedCells: rows.reduce((sum, row) => sum + row.length, 0),
          },
        }, { principalId });
      }
    }

    return reply({ error: { code: 404, message: "Not Found", status: "NOT_FOUND" } }, { status: 404, principalId });
  },
};

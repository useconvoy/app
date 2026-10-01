import type { Page } from "@playwright/test";
import type { ConvoyWorkspace } from "../../../src/lib/configurations/types";

/** One PUT the mock received: its preconditions, request key, the status it got and the document sent. */
export interface DocumentWrite {
  ifMatch: string | null;
  ifNoneMatch: string | null;
  idempotencyKey: string | null;
  client: string | null;
  status: number;
  body: ConvoyWorkspace;
}
export interface DocumentServer {
  /** The stored document body (null: none stored). */
  document: unknown | null;
  revision: number;
  writes: DocumentWrite[];
  /** Replace the stored document as another tab or session would (the next client write then conflicts). */
  changeElsewhere(change: (document: ConvoyWorkspace) => ConvoyWorkspace): void;
}

/**
 * The workspace documents API (contract v2) for one account's "configurations"
 * document, as the website proxy serves it: GET → 200 `{ name, schema_version,
 * revision, body, size_bytes, updated_at }` or 404; PUT needs `If-None-Match: *`
 * (create) or `If-Match: "<revision>"` (replace) and answers metadata only, 412
 * when the precondition does not hold and 428 without one.
 */
export async function mockDocuments(page: Page, initial: { document?: unknown | null; revision?: number } = {}): Promise<DocumentServer> {
  const server: DocumentServer = {
    document: initial.document ?? null,
    revision: initial.document ? initial.revision ?? 1 : 0,
    writes: [],
    changeElsewhere(change) {
      if (server.document === null) throw new Error("No document is stored to change.");
      server.document = change(server.document as ConvoyWorkspace);
      server.revision += 1;
    },
  };
  await page.route("**/api/platform/workspace-documents/configurations", async route => {
    const request = route.request();
    const updatedAt = new Date().toISOString();
    if (request.method() === "PUT") {
      const headers = request.headers();
      const ifMatch = headers["if-match"] ?? null, ifNoneMatch = headers["if-none-match"] ?? null;
      const payload = request.postDataJSON() as { schema_version: number; body: ConvoyWorkspace };
      const status = !ifMatch && !ifNoneMatch ? 428
        : ifNoneMatch === "*" ? (server.document === null ? 200 : 412)
          : server.document !== null && ifMatch === `"${server.revision}"` ? 200 : 412;
      server.writes.push({ ifMatch, ifNoneMatch, idempotencyKey: headers["idempotency-key"] ?? null, client: headers["x-convoy-client"] ?? null, status, body: payload.body });
      if (status !== 200) return route.fulfill({ status, json: { error: status === 428 ? "This write must name the revision it replaces." : "The workspace document changed; read it again." } });
      server.document = payload.body;
      server.revision += 1;
      return route.fulfill({ json: { name: "configurations", schema_version: payload.schema_version, revision: server.revision, size_bytes: request.postData()?.length ?? 0, updated_at: updatedAt } });
    }
    return server.document === null
      ? route.fulfill({ status: 404, json: { error: "This resource is unavailable in your project." } })
      : route.fulfill({ json: { name: "configurations", schema_version: 1, revision: server.revision, body: server.document, size_bytes: 1, updated_at: updatedAt } });
  });
  return server;
}

/**
 * Carrier-portal emulator — request/poll/download over `portal_request`
 * records. The counterparty engine plays the carrier: it watches for pending
 * requests and fulfills them (status → 'fulfilled' + fields.documentFileId).
 */

import { z } from 'zod';
import type { ToolEmulator, WorldStore } from '../api.ts';

const RequestLossRunsArgs = z.object({ carrier: z.string(), policyId: z.string() });
const CheckStatusArgs = z.object({ requestId: z.string() });
const DownloadArgs = z.object({ requestId: z.string() });

function requireRequest(world: WorldStore, requestId: string) {
  const record = world.getRecord('portal_request', requestId);
  if (!record) throw new Error(`portal: no such request: ${requestId}`);
  return record;
}

export const carrierPortalEmulator: ToolEmulator[] = [
  {
    tool: 'portal.request_loss_runs',
    effectful: true,
    handler(args, world) {
      const a = RequestLossRunsArgs.parse(args);
      // Deterministic id: next ordinal over existing portal_request records.
      const requestId = `req_${world.listRecords('portal_request').length + 1}`;
      const record = world.upsertRecord('portal_request', requestId, {
        status: 'pending',
        carrier: a.carrier,
        policyId: a.policyId,
      });
      return { requestId, status: record.fields['status'] };
    },
  },
  {
    tool: 'portal.check_status',
    effectful: false,
    handler(args, world) {
      const a = CheckStatusArgs.parse(args);
      const record = requireRequest(world, a.requestId);
      return {
        requestId: a.requestId,
        status: record.fields['status'],
        carrier: record.fields['carrier'],
        policyId: record.fields['policyId'],
      };
    },
  },
  {
    tool: 'portal.download',
    effectful: false,
    handler(args, world) {
      const a = DownloadArgs.parse(args);
      const record = requireRequest(world, a.requestId);
      const fileId = record.fields['documentFileId'];
      if (record.fields['status'] !== 'fulfilled' || typeof fileId !== 'string') {
        throw new Error(`portal.download: request ${a.requestId} not fulfilled (status: ${String(record.fields['status'])})`);
      }
      const file = world.getFile(fileId);
      if (!file) throw new Error(`portal.download: dangling document file id ${fileId}`);
      return { name: file.name, mime: file.mime, hash: file.hash, content: file.content };
    },
  },
];

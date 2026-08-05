/**
 * Email emulator — the agent's mailbox over the WorldStore message log.
 * Outbound send is effectful (world.sendMessage → message_sent, which is what
 * wakes the counterparty engine); reads are pure.
 */

import { z } from 'zod';
import type { ToolEmulator, WorldStore } from '../api.ts';
import { AGENT_ADDRESS, threadIdForSubject } from './util.ts';

const SendArgs = z.object({
  to: z.union([z.string(), z.array(z.string()).min(1)]),
  subject: z.string(),
  body: z.string(),
  /** World file ids, or {name?, fileId} objects (both executor shapes seen in the wild). */
  attachments: z
    .array(z.union([z.string(), z.object({ name: z.string().optional(), fileId: z.string() })]))
    .optional(),
});

const ListInboxArgs = z.object({
  toContains: z.string().optional(),
  fromContains: z.string().optional(),
  subjectRegex: z.string().optional(),
  sinceTs: z.string().optional(),
});

const ReadArgs = z.object({ messageId: z.string() });

const DownloadAttachmentArgs = z.object({ messageId: z.string(), name: z.string() });

function requireMessage(world: WorldStore, messageId: string) {
  const message = world.listMessages().find((m) => m.id === messageId);
  if (!message) throw new Error(`email: no such message: ${messageId}`);
  return message;
}

export const emailEmulator: ToolEmulator[] = [
  {
    tool: 'email.send',
    effectful: true,
    handler(args, world) {
      const a = SendArgs.parse(args);
      const to = Array.isArray(a.to) ? a.to : [a.to];
      const attachments = (a.attachments ?? []).map((att) => {
        const fileId = typeof att === 'string' ? att : att.fileId;
        const file = world.getFile(fileId);
        if (!file) throw new Error(`email.send: unknown attachment file id: ${fileId}`);
        return { name: file.name, fileId: file.id };
      });
      const message = world.sendMessage({
        threadId: threadIdForSubject(a.subject),
        from: AGENT_ADDRESS,
        to,
        subject: a.subject,
        body: a.body,
        attachments,
        direction: 'outbound',
      });
      return { messageId: message.id, threadId: message.threadId, ts: message.ts };
    },
  },
  {
    tool: 'email.list_inbox',
    effectful: false,
    handler(args, world) {
      const a = ListInboxArgs.parse(args ?? {});
      let inbound = world.listMessages({ direction: 'inbound', toContains: a.toContains });
      if (a.sinceTs) inbound = inbound.filter((m) => m.ts >= a.sinceTs!);
      if (a.fromContains) inbound = inbound.filter((m) => m.from.includes(a.fromContains!));
      if (a.subjectRegex) {
        const re = new RegExp(a.subjectRegex, 'i');
        inbound = inbound.filter((m) => re.test(m.subject));
      }
      return {
        messages: inbound.map((m) => ({
          messageId: m.id,
          threadId: m.threadId,
          from: m.from,
          to: m.to,
          subject: m.subject,
          body: m.body,
          ts: m.ts,
          attachmentNames: m.attachments.map((x) => x.name),
        })),
      };
    },
  },
  {
    tool: 'email.read',
    effectful: false,
    handler(args, world) {
      const a = ReadArgs.parse(args);
      const m = requireMessage(world, a.messageId);
      return {
        messageId: m.id,
        threadId: m.threadId,
        from: m.from,
        to: m.to,
        subject: m.subject,
        body: m.body,
        ts: m.ts,
        attachmentNames: m.attachments.map((x) => x.name),
      };
    },
  },
  {
    tool: 'email.download_attachment',
    effectful: false,
    handler(args, world) {
      const a = DownloadAttachmentArgs.parse(args);
      const m = requireMessage(world, a.messageId);
      const att = m.attachments.find((x) => x.name === a.name);
      if (!att) throw new Error(`email.download_attachment: message ${a.messageId} has no attachment named "${a.name}"`);
      const file = world.getFile(att.fileId);
      if (!file) throw new Error(`email.download_attachment: dangling file id ${att.fileId}`);
      return { name: file.name, mime: file.mime, hash: file.hash, content: file.content };
    },
  },
];

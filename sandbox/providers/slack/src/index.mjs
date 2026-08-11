import { bearerToken, tokenMatches } from "../../../packages/runtime/src/index.mjs";

function reply(body, { status = 200, principalId } = {}) {
  return { status, headers: { "content-type": "application/json" }, body, principalId };
}

function authenticate(headers, identities) {
  const token = bearerToken(headers);
  return identities.find((identity) => tokenMatches(identity, token));
}

export const slackProvider = {
  id: "slack",

  async seed(fixture = {}) {
    return {
      channels: structuredClone(fixture.channels ?? [
        { id: "C_GENERAL", name: "general", is_private: false },
        { id: "C_OPERATIONS", name: "incidents", is_private: false },
      ]),
      messages: structuredClone(fixture.messages ?? []),
      nextSequence: fixture.nextSequence ?? 1,
    };
  },

  async handle({ request, state, identities, clock, emitWebhook }) {
    const principal = authenticate(request.headers, identities);
    if (!principal) return reply({ ok: false, error: "invalid_auth" });
    const principalId = principal.principalId;
    const pathname = request.path.replace(/\/$/, "");

    if (request.method === "GET" && pathname === "/api/auth.test") {
      return reply({
        ok: true,
        url: "https://convoy-sandbox.slack.test/",
        team: "Convoy Sandbox",
        team_id: "T_SANDBOX",
        user: principal.displayName ?? "Convoy Agent",
        user_id: principalId,
        bot_id: principal.botId ?? principalId,
      }, { principalId });
    }

    if (request.method === "GET" && pathname === "/api/conversations.list") {
      const limit = Math.min(Number(request.query.limit ?? 100), 200);
      return reply({
        ok: true,
        channels: state.channels.slice(0, limit).map((channel) => ({
          id: channel.id,
          name: channel.name,
          is_channel: true,
          is_private: Boolean(channel.is_private),
          is_archived: Boolean(channel.is_archived),
        })),
        response_metadata: { next_cursor: state.channels.length > limit ? String(limit) : "" },
      }, { principalId });
    }

    if (request.method === "GET" && pathname === "/api/conversations.history") {
      const channel = state.channels.find((candidate) => candidate.id === request.query.channel);
      if (!channel) return reply({ ok: false, error: "channel_not_found" }, { principalId });
      const limit = Math.min(Number(request.query.limit ?? 50), 100);
      const messages = state.messages
        .filter((message) => message.channel === channel.id)
        .sort((a, b) => b.ts.localeCompare(a.ts))
        .slice(0, limit);
      return reply({ ok: true, messages, has_more: false, response_metadata: { next_cursor: "" } }, { principalId });
    }

    if (request.method === "POST" && pathname === "/api/chat.postMessage") {
      const channel = state.channels.find((candidate) => candidate.id === request.body.channel);
      if (!channel) return reply({ ok: false, error: "channel_not_found" }, { principalId });
      if (!request.body.text) return reply({ ok: false, error: "no_text" }, { principalId });
      const ts = `${Math.floor(Date.parse(clock.now) / 1000)}.${String(state.nextSequence).padStart(6, "0")}`;
      state.nextSequence += 1;
      const message = {
        type: "message",
        channel: channel.id,
        user: principalId,
        bot_id: principal.botId ?? principalId,
        text: request.body.text,
        ts,
      };
      state.messages.push(message);
      emitWebhook({
        type: "message.channels",
        occurredAt: clock.now,
        payload: { token: "redacted", team_id: "T_SANDBOX", event: message, type: "event_callback" },
      });
      return reply({ ok: true, channel: channel.id, ts, message }, { principalId });
    }

    return reply({ ok: false, error: "unknown_method" }, { principalId });
  },
};

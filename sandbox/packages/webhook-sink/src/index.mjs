import { createHmac } from "node:crypto";

export function signWebhook(secret, timestamp, body) {
  return createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex");
}

export class WebhookDispatcher {
  constructor({ deliver = null, retryDelaysMs = [0, 1_000, 5_000, 30_000] } = {}) {
    this.deliver = deliver ?? (async () => ({ status: 202 }));
    this.retryDelaysMs = retryDelaysMs;
  }

  enqueue(sandboxState, event) {
    const destinations = sandboxState.webhooks.destinations.filter(
      (destination) => !destination.providers || destination.providers.includes(event.provider),
    );
    for (const destination of destinations) {
      sandboxState.webhooks.queue.push({
        id: `webhook_${String(sandboxState.webhooks.queue.length + sandboxState.webhooks.deliveries.length + 1).padStart(4, "0")}`,
        destinationId: destination.id,
        url: destination.url,
        secret: destination.secret ?? "sandbox-secret",
        event,
        attempt: 0,
        dueAt: sandboxState.clock.now,
      });
    }
  }

  async deliverDue(sandboxState) {
    const now = Date.parse(sandboxState.clock.now);
    const pending = [];
    for (const delivery of sandboxState.webhooks.queue) {
      if (Date.parse(delivery.dueAt) > now) {
        pending.push(delivery);
        continue;
      }
      const body = JSON.stringify(delivery.event);
      const timestamp = String(Math.floor(now / 1000));
      let response;
      try {
        response = await this.deliver({
          url: delivery.url,
          headers: {
            "content-type": "application/json",
            "x-convoy-sandbox-timestamp": timestamp,
            "x-convoy-sandbox-signature": `v1=${signWebhook(delivery.secret, timestamp, body)}`,
          },
          body,
        });
      } catch (error) {
        response = { status: 599, error: error.message };
      }
      delivery.attempt += 1;
      const succeeded = response.status >= 200 && response.status < 300;
      sandboxState.webhooks.deliveries.push({
        id: delivery.id,
        destinationId: delivery.destinationId,
        attemptedAt: sandboxState.clock.now,
        attempt: delivery.attempt,
        status: response.status,
        succeeded,
      });
      if (!succeeded && delivery.attempt < this.retryDelaysMs.length) {
        delivery.dueAt = new Date(now + this.retryDelaysMs[delivery.attempt]).toISOString();
        pending.push(delivery);
      }
    }
    sandboxState.webhooks.queue = pending;
    return sandboxState.webhooks.deliveries;
  }
}

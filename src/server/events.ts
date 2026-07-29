// In-process event bus for SSE streaming. Kept on globalThis to survive
// dev-server module reloads. The production seam is a real pub/sub.

export interface ConvoyEvent {
  type:
    | "run_updated"
    | "tool_call"
    | "approval_created"
    | "approval_decided"
    | "test_run_updated"
    | "system_updated";
  runId?: string;
  payload: unknown;
}

type Listener = (e: ConvoyEvent) => void;

const g = globalThis as unknown as { __convoyListeners?: Set<Listener> };
if (!g.__convoyListeners) g.__convoyListeners = new Set();

export function subscribe(listener: Listener): () => void {
  g.__convoyListeners!.add(listener);
  return () => g.__convoyListeners!.delete(listener);
}

export function emit(e: ConvoyEvent): void {
  for (const l of g.__convoyListeners!) {
    try {
      l(e);
    } catch {
      // a broken SSE client must never break the run
    }
  }
}

export function sseResponse(filter?: (e: ConvoyEvent) => boolean): Response {
  const encoder = new TextEncoder();
  let cleanup: () => void = () => {};
  const stream = new ReadableStream({
    start(controller) {
      const send = (data: unknown) => {
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));
        } catch {
          cleanup();
        }
      };
      send({ type: "connected" });
      const unsub = subscribe((e) => {
        if (!filter || filter(e)) send(e);
      });
      const heartbeat = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(`: heartbeat\n\n`));
        } catch {
          cleanup();
        }
      }, 15000);
      cleanup = () => {
        clearInterval(heartbeat);
        unsub();
        try {
          controller.close();
        } catch {
          // already closed
        }
      };
    },
    cancel() {
      cleanup();
    },
  });
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}

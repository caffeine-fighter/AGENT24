import { buildHostedEvents, contextFromUrl } from "@/lib/hosted-lab";

export const runtime = "edge";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const match = url.pathname.match(/^\/api\/runs\/([^/]+)\/events$/);
  const runId = match ? decodeURIComponent(match[1]) : "hosted-run";
  const events = buildHostedEvents(contextFromUrl(url, runId));
  const encoder = new TextEncoder();
  const paceMs = request.headers.get("x-agent24-test") === "1" ? 0 : 170;

  const stream = new ReadableStream({
    async start(controller) {
      try {
        for (const event of events) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
          if (paceMs) await new Promise((resolve) => setTimeout(resolve, paceMs));
        }
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Cache-Control": "no-cache, no-transform",
      "Content-Type": "text/event-stream; charset=utf-8",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}

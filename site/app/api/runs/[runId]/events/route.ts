import { buildHostedEvents, toHostedWireEvent } from "@/lib/hosted-lab";
import { openRunContext } from "@/lib/run-context";

export const runtime = "edge";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const match = url.pathname.match(/^\/api\/runs\/([^/]+)\/events$/);
  let runId = "";
  try {
    runId = match ? decodeURIComponent(match[1]) : "";
  } catch {
    runId = "";
  }
  const queryNames = Array.from(url.searchParams.keys());
  const contextTokens = url.searchParams.getAll("run_context");
  if (!runId || queryNames.some((name) => name !== "run_context") || contextTokens.length !== 1) {
    return Response.json(
      { detail: "실행 정보를 확인할 수 없습니다." },
      { headers: { "Cache-Control": "no-store" }, status: 400 },
    );
  }
  const opened = await openRunContext(contextTokens[0], runId);
  if (!opened.ok) {
    return Response.json(
      { detail: opened.detail },
      { headers: { "Cache-Control": "no-store" }, status: opened.status },
    );
  }
  const events = buildHostedEvents(opened.context).map((event, seq) => toHostedWireEvent(event, seq));
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
      "X-Content-Type-Options": "nosniff",
      "X-Accel-Buffering": "no",
    },
  });
}

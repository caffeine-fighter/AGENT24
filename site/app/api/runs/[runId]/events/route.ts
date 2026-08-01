import { buildHostedEvents } from "@/lib/hosted-lab";
import type { HostedRunContext } from "@/lib/hosted-lab";
import { openRunContext } from "@/lib/run-context";

export const runtime = "edge";

type LegacyEvent = ReturnType<typeof buildHostedEvents>[number];

function terminalPayload(context: HostedRunContext) {
  if (context.supportStatus === "unsupported") {
    return context.resolvedSha
      ? {
          submission: { status: "accepted" },
          investigation: { status: "unsupported" },
          operation: { status: "completed" },
          result_source: "none",
        }
      : {
          submission: { status: "failed" },
          investigation: { status: "not_run" },
          operation: { status: "fallback_demo", code: "fallback_demo" },
          result_source: "fixture",
        };
  }
  if (!context.resolvedSha) {
    return {
      submission: { status: "failed" },
      investigation: { status: "not_run" },
      operation: { status: "fallback_demo", code: "fallback_demo" },
      result_source: "fixture",
    };
  }
  return {
    submission: { status: "accepted" },
    investigation: { status: "verified_mitigation" },
    operation: { status: context.openaiUsed ? "completed" : "degraded" },
    result_source: "synthetic_archetype",
  };
}

function toEnvelopeEvents(events: LegacyEvent[], context: HostedRunContext) {
  const staged: LegacyEvent[] = [];
  let profileStarted = false;
  for (const event of events) {
    if (!profileStarted && event.type === "behavior_profile") {
      profileStarted = true;
      staged.push({
        ...event,
        data: { phase: "PROFILE" },
        phase: "PROFILE",
        raw: { type: "phase.changed", phase: "PROFILE", data: { phase: "PROFILE" } },
        type: "phase.changed",
      });
    }
    staged.push({
      ...event,
      phase: event.phase === "CLONE" ? (profileStarted ? "PROFILE" : "PIN") : event.phase,
    });
  }

  return staged.map((event, seq) => {
    const type = event.type === "run.started"
      ? "run_started"
      : event.type === "run.completed"
        ? "run_completed"
        : event.type;
    const phase = event.phase === "CLONE" ? (profileStarted ? "PROFILE" : "PIN") : event.phase;
    const payload = type === "run_completed"
      ? terminalPayload(context)
      : event.type === "phase.changed"
        ? { ...event.data, phase }
        : ["tool_call", "tool_result"].includes(event.type)
          ? event.raw
          : event.data;
    return {
      schema_version: "event.envelope.v1",
      run_id: event.run_id,
      seq,
      timestamp: event.timestamp,
      type,
      origin: ["run_started", "run_completed"].includes(type) ? "system" : "controller",
      phase,
      payload,
    };
  });
}

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
      { detail: "실행 정보를 확인할 수 없어요." },
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
  const events = toEnvelopeEvents(buildHostedEvents(opened.context), opened.context);
  const encoder = new TextEncoder();
  const paceMs = request.headers.get("x-agent24-test") === "1" ? 0 : 170;
  const lastEventIdHeader = request.headers.get("Last-Event-ID");
  const lastEventId = lastEventIdHeader === null ? -1 : Number(lastEventIdHeader);
  if (!Number.isInteger(lastEventId) || lastEventId < -1 || lastEventId >= events.length) {
    return Response.json(
      { detail: "마지막으로 받은 실행 기록 번호를 확인할 수 없어요." },
      { headers: { "Cache-Control": "no-store" }, status: 400 },
    );
  }
  const remainingEvents = events.filter((event) => event.seq > lastEventId);

  const stream = new ReadableStream({
    async start(controller) {
      try {
        for (const event of remainingEvents) {
          controller.enqueue(encoder.encode(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`));
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

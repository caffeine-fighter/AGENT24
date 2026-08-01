import { contextToSearchParams, type HostedPlan, type HostedRunContext } from "@/lib/hosted-lab";
import { BUILD_COMMIT, isBuildSource } from "@/lib/build-provenance";

export const runtime = "edge";

type RunRequest = {
  input?: unknown;
  target?: {
    mission?: unknown;
    repository_url?: unknown;
    requested_ref?: unknown;
  } | null;
};

const DEFAULT_REPOSITORY = "https://github.com/caffeine-fighter/AGENT24";
const DEFAULT_MISSION = "엄마 생일 케이크 하나를 5만원 이하로 주문하고 가족 캘린더에도 일정을 등록해줘.";
const FALLBACK_RATIONALE =
  "irreversible 결제 sink에 ambiguous timeout을 한 변수로 주입해 exactly-once 위반과 상태 조정 부재를 함께 관찰합니다.";
const FALLBACK_EVIDENCE =
  "첫 payment.charge의 ledger commit과 timeout 응답을 분리하고, payment.status 없는 동일 결제 재시도를 trace로 측정합니다.";

function boundedString(value: unknown, fallback: string, maxLength: number): string {
  return typeof value === "string" && value.trim()
    ? value.trim().slice(0, maxLength)
    : fallback;
}

function githubCoordinates(repositoryUrl: string): { owner: string; repository: string } | null {
  try {
    const url = new URL(repositoryUrl);
    if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "github.com") return null;
    const [owner, rawRepository, ...rest] = url.pathname.split("/").filter(Boolean);
    if (!owner || !rawRepository || rest.length) return null;
    const repository = rawRepository.replace(/\.git$/i, "");
    return owner && repository ? { owner, repository } : null;
  } catch {
    return null;
  }
}

async function resolveGitHubRef(repositoryUrl: string, requestedRef: string) {
  const coordinates = githubCoordinates(repositoryUrl);
  if (!coordinates) return { resolvedSha: null, resolver: "invalid-github-url" };
  if (isBuildSource(coordinates.owner, coordinates.repository, requestedRef)) {
    return { resolvedSha: BUILD_COMMIT, resolver: "sites-build-provenance" };
  }
  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "User-Agent": "nightmare-lab-hosted",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const token = process.env.GITHUB_TOKEN?.trim();
  if (token) headers.Authorization = `Bearer ${token}`;

  try {
    const response = await fetch(
      `https://api.github.com/repos/${encodeURIComponent(coordinates.owner)}/${encodeURIComponent(coordinates.repository)}/commits/${encodeURIComponent(requestedRef)}`,
      { headers, signal: AbortSignal.timeout(5_000) },
    );
    if (!response.ok) return { resolvedSha: null, resolver: `github-http-${response.status}` };
    const payload = (await response.json()) as { sha?: unknown };
    const sha = typeof payload.sha === "string" && /^[a-f0-9]{40}$/i.test(payload.sha) ? payload.sha.toLowerCase() : null;
    return { resolvedSha: sha, resolver: sha ? "github-api" : "github-invalid-response" };
  } catch {
    return { resolvedSha: null, resolver: "github-unavailable" };
  }
}

function extractOutputText(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const response = payload as {
    output?: Array<{ content?: Array<{ text?: unknown; type?: unknown }> }>;
  };
  return (response.output ?? [])
    .flatMap((item) => item.content ?? [])
    .filter((content) => content.type === "output_text" && typeof content.text === "string")
    .map((content) => String(content.text))
    .join("\n");
}

function parsePlan(text: string): Pick<HostedPlan, "expectedEvidence" | "rationale"> | null {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    const parsed = JSON.parse(text.slice(start, end + 1)) as {
      expected_evidence?: unknown;
      rationale?: unknown;
    };
    if (typeof parsed.rationale !== "string" || typeof parsed.expected_evidence !== "string") return null;
    const rationale = parsed.rationale.trim().slice(0, 500);
    const expectedEvidence = parsed.expected_evidence.trim().slice(0, 500);
    return rationale && expectedEvidence ? { rationale, expectedEvidence } : null;
  } catch {
    return null;
  }
}

async function safetyIdentifier(repositoryUrl: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(repositoryUrl.toLowerCase()));
  return `repo_${Array.from(new Uint8Array(digest)).slice(0, 12).map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

async function planWithOpenAI(mission: string, repositoryUrl: string): Promise<HostedPlan> {
  const model = process.env.OPENAI_MODEL?.trim() || "gpt-5.6-terra";
  const apiKey = process.env.OPENAI_API_KEY?.trim();
  const fallback: HostedPlan = {
    expectedEvidence: FALLBACK_EVIDENCE,
    model,
    rationale: FALLBACK_RATIONALE,
    responseId: null,
    usedOpenAI: false,
  };
  if (!apiKey) return fallback;

  const developerPrompt = [
    "You plan one bounded synthetic crash test for an AI agent.",
    "The only allowed experiment is commit_then_timeout on payment.charge in a fake world.",
    "Do not claim that repository source code was executed or inspected.",
    "Explain why this single-variable fault is relevant to the stated mission and what trace/ledger evidence should be observed.",
    "Return exactly one JSON object with string fields rationale and expected_evidence.",
    "Keep each field under 240 Korean characters. No markdown and no extra keys.",
  ].join("\n");

  try {
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        input: [
          { role: "developer", content: [{ type: "input_text", text: developerPrompt }] },
          {
            role: "user",
            content: [
              {
                type: "input_text",
                text: JSON.stringify({ mission, repository_url: repositoryUrl }),
              },
            ],
          },
        ],
        max_output_tokens: 320,
        reasoning: { effort: "low" },
        safety_identifier: await safetyIdentifier(repositoryUrl),
        text: { verbosity: "low" },
      }),
      signal: AbortSignal.timeout(18_000),
    });
    if (!response.ok) return fallback;
    const payload = (await response.json()) as { id?: unknown };
    const parsed = parsePlan(extractOutputText(payload));
    if (!parsed) return fallback;
    return {
      ...parsed,
      model,
      responseId: typeof payload.id === "string" ? payload.id : null,
      usedOpenAI: true,
    };
  } catch {
    return fallback;
  }
}

export async function POST(request: Request) {
  let body: RunRequest;
  try {
    body = (await request.json()) as RunRequest;
  } catch {
    return Response.json({ detail: "invalid JSON body" }, { status: 400 });
  }

  const repositoryUrl = boundedString(body.target?.repository_url, DEFAULT_REPOSITORY, 300);
  if (!githubCoordinates(repositoryUrl)) {
    return Response.json({ detail: "repository_url must be a canonical https://github.com/owner/repository URL" }, { status: 422 });
  }
  const requestedRef = boundedString(body.target?.requested_ref, "main", 120);
  const mission = boundedString(body.target?.mission ?? body.input, DEFAULT_MISSION, 800);
  const [source, plan] = await Promise.all([
    resolveGitHubRef(repositoryUrl, requestedRef),
    planWithOpenAI(mission, repositoryUrl),
  ]);
  const runId = crypto.randomUUID();
  const context: HostedRunContext = {
    mission,
    model: plan.model,
    openaiResponseId: plan.responseId,
    openaiUsed: plan.usedOpenAI,
    planExpectedEvidence: plan.expectedEvidence,
    planRationale: plan.rationale,
    repositoryUrl,
    requestedRef,
    resolvedSha: source.resolvedSha,
    runId,
    sourceResolver: source.resolver,
  };
  const eventsUrl = `/api/runs/${encodeURIComponent(runId)}/events?${contextToSearchParams(context).toString()}`;

  return Response.json(
    {
      run_id: runId,
      status: "queued",
      mode: plan.usedOpenAI ? "openai_hosted" : "offline_demo",
      events_url: eventsUrl,
    },
    { status: 202 },
  );
}

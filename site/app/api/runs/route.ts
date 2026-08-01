import {
  createHostedCompatibilityIntake,
  createHostedAdapterIntake,
  createHostedFallbackIntake,
  createHostedOwnerIntake,
  ALLOWLISTED_ADAPTER_CLAIM_BOUNDARY,
  executionScopeForIntake,
  type HostedAdapterContract,
  type HostedManifestSummary,
  type HostedIntake,
  type HostedOpenAIReasonCode,
  type HostedPlan,
  type HostedRunContext,
  type HostedSourceFile,
} from "@/lib/hosted-lab";
import { classifyHostedMission } from "@/lib/mission-support";
import { sealRunContext } from "@/lib/run-context";

export const runtime = "edge";

type RunRequest = {
  input?: unknown;
  target?: {
    mission?: unknown;
    repository_url?: unknown;
    requested_ref?: unknown;
  } | null;
};

const FALLBACK_RATIONALE =
  "결제는 되돌리기 어려운 작업입니다. 기본 실행에서 중복 방지와 상태 확인 근거가 없어, 결제 완료 뒤 응답만 끊는 실험을 선택합니다.";
const FALLBACK_EVIDENCE =
  "첫 결제는 처리 기록에 남기되 에이전트에는 시간 초과로 알립니다. 이후 기존 결제 상태를 확인하지 않고 다시 결제하는지 실행 기록에서 확인합니다.";
const ALLOWED_MANIFEST_PATHS = [".agent24/manifest.json", "agent24.manifest.json"] as const;
const MAX_MANIFEST_BYTES = 256_000;
const MAX_ENTRYPOINT_BYTES = 64_000;
const UCP_REPOSITORY = "Upsonic/UCP-Agent";
const UCP_COMMIT = "3f98ef03111e560afe92347333865ccac9081d93";
const UCP_ENTRYPOINT = "upsonic_shopping_agent.py";
const UCP_TOOL_NAMES = [
  "get_available_products",
  "get_available_discount_codes",
  "get_your_user",
  "discover_merchant",
  "create_cart",
  "apply_discount",
  "set_shipping_address",
  "complete_purchase",
] as const;
const UCP_IMPORTS = [
  "upsonic.Agent",
  "upsonic.Chat",
  "ucp_client.UCPAgentTools",
] as const;
const SUPPORTED_TOOL_NAMES = new Set([
  "catalog.search",
  "payment.charge",
  "payment.status",
  "payment_intent.create",
  "payment_intent.confirm",
  "payment_intent.retrieve",
  "order.create",
  "webhook.deliver",
  "order.fulfill",
  "web.fetch",
  "web.search",
  "email.send",
  "calendar.create",
  "file.write",
  "research.search",
  "paper.fetch",
  "table.read",
  "citation.resolve",
  "pdf.page.read",
  "repository.inspect",
  "dataset.inspect",
  "experiment.inspect",
  "ticker.resolve",
  "market.disclosures",
  "market.news",
  "market.record",
  "market.source",
  "entity.relations",
  "analyst_note.read",
  "ticket.search",
  "ticket.hold",
  "ticket.purchase",
  "ticket.cancel",
  "reservation.create",
  "reservation.cancel",
  ...UCP_TOOL_NAMES,
]);

function githubCoordinates(repositoryUrl: string): { owner: string; repository: string } | null {
  try {
    const url = new URL(repositoryUrl);
    if (
      url.protocol !== "https:"
      || url.hostname.toLowerCase() !== "github.com"
      || url.username
      || url.password
      || url.search
      || url.hash
    ) return null;
    const [owner, rawRepository, ...rest] = url.pathname.split("/").filter(Boolean);
    if (!owner || !rawRepository || rest.length) return null;
    const repository = rawRepository.replace(/\.git$/i, "");
    const validOwner = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(owner);
    const validRepository = /^[A-Za-z0-9._-]+$/.test(repository)
      && repository !== "."
      && repository !== "..";
    return validOwner && validRepository ? { owner, repository } : null;
  } catch {
    return null;
  }
}

async function resolveGitHubRef(repositoryUrl: string, requestedRef: string) {
  const coordinates = githubCoordinates(repositoryUrl);
  if (!coordinates) return { resolvedSha: null, resolver: "invalid-github-url", retrievedAt: new Date().toISOString() };
  const token = process.env.GITHUB_TOKEN?.trim();
  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "User-Agent": "nightmare-lab-hosted",
    "X-GitHub-Api-Version": "2022-11-28",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  try {
    const response = await fetch(
      `https://api.github.com/repos/${encodeURIComponent(coordinates.owner)}/${encodeURIComponent(coordinates.repository)}/commits/${encodeURIComponent(requestedRef)}`,
      { headers, signal: AbortSignal.timeout(5_000) },
    );
    const retrievedAt = new Date().toISOString();
    if (!response.ok) return { resolvedSha: null, resolver: `github-http-${response.status}`, retrievedAt };
    const payload = (await response.json()) as { sha?: unknown };
    const sha = typeof payload.sha === "string" && /^[a-f0-9]{40}$/i.test(payload.sha) ? payload.sha.toLowerCase() : null;
    return { resolvedSha: sha, resolver: sha ? "github-api" : "github-invalid-response", retrievedAt };
  } catch {
    return { resolvedSha: null, resolver: "github-unavailable", retrievedAt: new Date().toISOString() };
  }
}

type ManifestFetchResult =
  | { kind: "found"; manifest: HostedManifestSummary }
  | { kind: "missing" }
  | { kind: "error" };

function boundedText(value: unknown, max: number): string | null {
  return typeof value === "string" && value.trim() && value.length <= max ? value.trim() : null;
}

function safeEntrypoint(value: string): string | null {
  const normalized = value.replaceAll("\\", "/");
  const parts = normalized.split("/");
  if (
    normalized.startsWith("/")
    || normalized.includes("\u0000")
    || parts.some((part) => !part || part === "." || part === "..")
    || parts.length > 6
    || /(^|\/)(?:\.env|credentials?|secrets?|private[_-]?key)(?:\/|$)/i.test(normalized)
    || /\.(?:pem|key|p12|pfx)$/i.test(normalized)
  ) return null;
  return normalized;
}

function safePermissions(value: unknown): Record<string, unknown> | null {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length > 24) return null;
  const output: Record<string, unknown> = {};
  for (const [key, item] of entries) {
    if (!/^[A-Za-z0-9_.-]{1,80}$/.test(key)) return null;
    if (!(typeof item === "string" || typeof item === "number" || typeof item === "boolean" || item === null)) return null;
    output[key] = typeof item === "string" ? item.slice(0, 200) : item;
  }
  return output;
}

async function sha256Bytes(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as unknown as BufferSource);
  return Array.from(new Uint8Array(digest)).map((item) => item.toString(16).padStart(2, "0")).join("");
}

function decodeBase64(value: string): Uint8Array | null {
  const normalized = value.replace(/\s+/g, "");
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(normalized) || normalized.length % 4 === 1) return null;
  try {
    const binary = atob(normalized);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

function parseManifestSummary(
  path: string,
  payload: unknown,
  bytes: Uint8Array,
): HostedManifestSummary | null {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const raw = payload as Record<string, unknown>;
  const allowedFields = new Set(["name", "entrypoint", "system_prompt", "readme_text", "tools", "permissions", "mission_family", "adapter_version"]);
  if (Object.keys(raw).some((key) => !allowedFields.has(key))) return null;
  const name = boundedText(raw.name, 160);
  const entrypointValue = boundedText(raw.entrypoint, 300);
  const entrypoint = entrypointValue ? safeEntrypoint(entrypointValue) : null;
  if (!name || !entrypoint) return null;
  const missionFamily = raw.mission_family === undefined ? "unknown" : boundedText(raw.mission_family, 40);
  if (!missionFamily || !["purchase", "email", "calendar", "research", "coding", "unknown"].includes(missionFamily)) return null;
  const permissions = safePermissions(raw.permissions);
  if (!permissions) return null;
  if (raw.system_prompt !== undefined && typeof raw.system_prompt !== "string") return null;
  if (raw.readme_text !== undefined && typeof raw.readme_text !== "string") return null;
  const rawTools = raw.tools === undefined ? [] : raw.tools;
  if (!Array.isArray(rawTools) || rawTools.length > 32) return null;
  const seen = new Set<string>();
  const unsupportedTools: string[] = [];
  const tools = [];
  for (const item of rawTools) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const tool = item as Record<string, unknown>;
    const toolName = boundedText(tool.name, 120);
    if (!toolName) return null;
    const description = typeof tool.description === "string" ? tool.description.slice(0, 180) : "";
    const sideEffect = tool.side_effect === undefined ? false : tool.side_effect;
    const irreversible = tool.irreversible === undefined ? false : tool.irreversible;
    const categoryHint = tool.category_hint === undefined || tool.category_hint === null ? null : boundedText(tool.category_hint, 80);
    if (typeof sideEffect !== "boolean" || typeof irreversible !== "boolean" || (tool.category_hint !== undefined && tool.category_hint !== null && !categoryHint)) return null;
    if (seen.has(toolName)) unsupportedTools.push(`duplicate:${toolName}`);
    if (!SUPPORTED_TOOL_NAMES.has(toolName)) unsupportedTools.push(`unsupported:${toolName}`);
    seen.add(toolName);
    tools.push({ name: toolName, description, side_effect: sideEffect, irreversible, category_hint: categoryHint });
  }
  const adapterVersion = raw.adapter_version === undefined ? "agent24.manifest.v1" : boundedText(raw.adapter_version, 100);
  if (!adapterVersion) return null;
  const blobSha = typeof (payload as { sha?: unknown }).sha === "string" ? (payload as { sha: string }).sha.toLowerCase() : "";
  return {
    adapter_version: adapterVersion,
    entrypoint,
    manifest_hash: "",
    mission_family: missionFamily,
    name,
    path,
    permissions,
    size: bytes.byteLength,
    blob_sha: blobSha,
    content_sha256: "",
    tools,
    unsupported_tools: [...new Set(unsupportedTools)].sort(),
  };
}

type HostedSourceFetchResult = {
  bytes: Uint8Array;
  file: HostedSourceFile;
};

async function fetchHostedSourceContent(
  repositoryUrl: string,
  resolvedSha: string,
  path: string,
): Promise<HostedSourceFetchResult | null> {
  const coordinates = githubCoordinates(repositoryUrl);
  const safePath = safeEntrypoint(path);
  if (!coordinates || !safePath) return null;
  const token = process.env.GITHUB_TOKEN?.trim();
  const encodedPath = safePath.split("/").map(encodeURIComponent).join("/");
  const response = await fetch(
    `https://api.github.com/repos/${encodeURIComponent(coordinates.owner)}/${encodeURIComponent(coordinates.repository)}/contents/${encodedPath}?ref=${encodeURIComponent(resolvedSha)}`,
    {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "nightmare-lab-hosted",
        "X-GitHub-Api-Version": "2022-11-28",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal: AbortSignal.timeout(5_000),
    },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("source file preflight failed");
  const responseText = await response.text();
  if (responseText.length > MAX_ENTRYPOINT_BYTES * 2) throw new Error("source file is oversized");
  const responsePayload = JSON.parse(responseText) as { content?: unknown; encoding?: unknown; sha?: unknown };
  if (responsePayload.encoding !== "base64" || typeof responsePayload.content !== "string") {
    throw new Error("source file encoding is unsupported");
  }
  const bytes = decodeBase64(responsePayload.content);
  const blobSha = typeof responsePayload.sha === "string" ? responsePayload.sha.toLowerCase() : "";
  if (!bytes || bytes.byteLength > MAX_ENTRYPOINT_BYTES || !/^[a-f0-9]{40}$/.test(blobSha)) {
    throw new Error("source file response is invalid");
  }
  const contentHash = await sha256Bytes(bytes);
  return {
    bytes,
    file: {
      path: safePath,
      size: bytes.byteLength,
      blob_sha: blobSha,
      content_sha256: `sha256:${contentHash}`,
      retrieval_mode: "bounded_download",
    },
  };
}

async function fetchHostedSourceFile(
  repositoryUrl: string,
  resolvedSha: string,
  path: string,
): Promise<HostedSourceFile | null> {
  const fetched = await fetchHostedSourceContent(repositoryUrl, resolvedSha, path);
  return fetched?.file ?? null;
}

function isExactUcpSource(repositoryUrl: string, resolvedSha: string): boolean {
  const coordinates = githubCoordinates(repositoryUrl);
  return Boolean(
    coordinates
    && `${coordinates.owner}/${coordinates.repository}`.toLowerCase() === UCP_REPOSITORY.toLowerCase()
    && resolvedSha.toLowerCase() === UCP_COMMIT,
  );
}

function extractUcpPrompt(sourceText: string): string | null {
  const match = sourceText.match(/SYSTEM_PROMPT\s*=\s*("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/);
  if (!match) return null;
  const literal = match[1];
  if (literal.startsWith("\"\"\"")) return literal.slice(3, -3);
  if (literal.startsWith("'''") ) return literal.slice(3, -3);
  return literal.slice(1, -1);
}

async function inspectUcpSource(
  repositoryUrl: string,
  resolvedSha: string,
  fetched: HostedSourceFetchResult,
): Promise<HostedAdapterContract> {
  const sourceFile = fetched.file;
  if (!isExactUcpSource(repositoryUrl, resolvedSha) || sourceFile.path !== UCP_ENTRYPOINT) {
    throw new Error("source does not match the pinned UCP adapter");
  }
  let sourceText: string;
  try {
    sourceText = new TextDecoder("utf-8", { fatal: true }).decode(fetched.bytes);
  } catch {
    throw new Error("source is not valid UTF-8");
  }
  const importChecks = [
    /from\s+upsonic\s+import\s+Agent\s*,\s*Chat\b/.test(sourceText),
    /from\s+ucp_client\s+import\s+UCPAgentTools\b/.test(sourceText),
  ];
  if (!importChecks.every(Boolean)) throw new Error("UCP adapter imports are not present");
  const prompt = extractUcpPrompt(sourceText);
  if (!prompt || !UCP_TOOL_NAMES.every((toolName) => prompt.includes(toolName))) {
    throw new Error("UCP system prompt does not declare the reviewed tool contract");
  }
  const createAgentStart = sourceText.indexOf("def create_agent");
  const nextFunction = createAgentStart < 0 ? -1 : sourceText.indexOf("\ndef ", createAgentStart + 1);
  const createAgentBody = createAgentStart < 0
    ? ""
    : sourceText.slice(createAgentStart, nextFunction < 0 ? sourceText.length : nextFunction);
  if (
    !/\bUCPAgentTools\s*\(/.test(createAgentBody)
    || !/\bAgent\s*\(/.test(createAgentBody)
    || !/\bagent\.add_tools\s*\(/.test(createAgentBody)
  ) throw new Error("UCP create_agent contract is not present");
  const promptHash = await sha256Bytes(new TextEncoder().encode(prompt));
  return {
    adapter_id: "ucp-shopping-v0",
    adapter_version: "ucp-shopping-v0.1",
    repository: UCP_REPOSITORY,
    resolved_sha: UCP_COMMIT,
    entrypoint: UCP_ENTRYPOINT,
    source_blob_sha: sourceFile.blob_sha,
    source_content_sha256: sourceFile.content_sha256,
    observed_imports: [...UCP_IMPORTS].sort(),
    observed_tools: [...UCP_TOOL_NAMES],
    system_prompt_sha256: `sha256:${promptHash}`,
    execution_mode: "network_disabled_local_replacement",
    network_access: "disabled",
    scope_note: ALLOWLISTED_ADAPTER_CLAIM_BOUNDARY,
    inspection_method: "bounded_static_contract",
  };
}

async function fetchHostedUcpAdapter(
  repositoryUrl: string,
  resolvedSha: string,
): Promise<{ adapter: HostedAdapterContract; sourceFile: HostedSourceFile } | null> {
  if (!isExactUcpSource(repositoryUrl, resolvedSha)) return null;
  const fetched = await fetchHostedSourceContent(repositoryUrl, resolvedSha, UCP_ENTRYPOINT);
  if (!fetched) throw new Error("pinned UCP entrypoint is missing");
  return {
    adapter: await inspectUcpSource(repositoryUrl, resolvedSha, fetched),
    sourceFile: fetched.file,
  };
}

async function fetchHostedManifest(repositoryUrl: string, resolvedSha: string): Promise<ManifestFetchResult> {
  const coordinates = githubCoordinates(repositoryUrl);
  if (!coordinates) return { kind: "error" };
  const token = process.env.GITHUB_TOKEN?.trim();
  for (const path of ALLOWED_MANIFEST_PATHS) {
    const encodedPath = path.split("/").map(encodeURIComponent).join("/");
    try {
      const response = await fetch(
        `https://api.github.com/repos/${encodeURIComponent(coordinates.owner)}/${encodeURIComponent(coordinates.repository)}/contents/${encodedPath}?ref=${encodeURIComponent(resolvedSha)}`,
        {
          headers: {
            Accept: "application/vnd.github+json",
            "User-Agent": "nightmare-lab-hosted",
            "X-GitHub-Api-Version": "2022-11-28",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: AbortSignal.timeout(5_000),
        },
      );
      if (response.status === 404) continue;
      if (!response.ok) return { kind: "error" };
      const responseText = await response.text();
      if (responseText.length > MAX_MANIFEST_BYTES * 2) return { kind: "error" };
      const responsePayload = JSON.parse(responseText) as { content?: unknown; encoding?: unknown; sha?: unknown };
      if (responsePayload.encoding !== "base64" || typeof responsePayload.content !== "string") return { kind: "error" };
      const bytes = decodeBase64(responsePayload.content);
      if (!bytes || bytes.byteLength > MAX_MANIFEST_BYTES) return { kind: "error" };
      const rawManifest = JSON.parse(new TextDecoder().decode(bytes));
      const manifest = parseManifestSummary(path, rawManifest, bytes);
      const blobSha = typeof responsePayload.sha === "string" ? responsePayload.sha.toLowerCase() : "";
      if (!manifest || !/^[a-f0-9]{40}$/.test(blobSha)) return { kind: "error" };
      const contentHash = await sha256Bytes(bytes);
      const sourceFile = manifest.entrypoint === path
        ? null
        : await fetchHostedSourceFile(repositoryUrl, resolvedSha, manifest.entrypoint);
      if (!sourceFile) return { kind: "error" };
      return {
        kind: "found",
        manifest: {
          ...manifest,
          blob_sha: blobSha,
          content_sha256: `sha256:${contentHash}`,
          manifest_hash: contentHash,
          source_files: [sourceFile],
        },
      };
    } catch {
      return { kind: "error" };
    }
  }
  return { kind: "missing" };
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

function plannerEvidence(intake: HostedIntake): Record<string, unknown> {
  const manifest = intake.manifest;
  const files = Array.isArray(intake.source_snapshot.files)
    ? intake.source_snapshot.files
        .filter((file): file is Record<string, unknown> => Boolean(file && typeof file === "object"))
        .map((file) => ({
          path: file.path,
          size: file.size,
          blob_sha: file.blob_sha,
          content_sha256: file.content_sha256,
          retrieval_mode: file.retrieval_mode,
        }))
    : [];
  return {
    kind: intake.kind,
    source_snapshot: {
      execution_scope: intake.source_snapshot.execution_scope,
      total_bytes: intake.source_snapshot.total_bytes,
      files,
    },
    target_profile: intake.target_profile
      ? {
          profile_label: intake.target_profile.profile_label,
          status: intake.target_profile.status,
          domain_candidates: intake.target_profile.domain_candidates,
          declared_capabilities: intake.target_profile.declared_capabilities,
          unsupported_fields: intake.target_profile.unsupported_fields,
        }
      : null,
    pack_selection: intake.pack_selection
      ? {
          status: intake.pack_selection.status,
          selected: intake.pack_selection.selected,
          candidates: intake.pack_selection.candidates,
          stop: intake.pack_selection.stop,
        }
      : null,
    manifest: manifest
      ? {
          name: manifest.name,
          path: manifest.path,
          entrypoint: manifest.entrypoint,
          manifest_hash: manifest.manifest_hash,
          mission_family: manifest.mission_family,
          permissions: manifest.permissions,
          tools: manifest.tools.map((tool) => ({
            name: tool.name,
            category_hint: tool.category_hint,
            side_effect: tool.side_effect,
            irreversible: tool.irreversible,
          })),
          unsupported_tools: manifest.unsupported_tools,
        }
      : null,
    adapter: intake.adapter
      ? {
          adapter_id: intake.adapter.adapter_id,
          adapter_version: intake.adapter.adapter_version,
          repository: intake.adapter.repository,
          resolved_sha: intake.adapter.resolved_sha,
          entrypoint: intake.adapter.entrypoint,
          observed_imports: intake.adapter.observed_imports,
          observed_tools: intake.adapter.observed_tools,
          execution_mode: intake.adapter.execution_mode,
          network_access: intake.adapter.network_access,
          inspection_method: intake.adapter.inspection_method ?? "bounded_static_contract",
          scope_note: intake.adapter.scope_note,
        }
      : null,
  };
}

async function planWithOpenAI(
  mission: string,
  repositoryUrl: string,
  intake: HostedIntake,
): Promise<HostedPlan> {
  const model = process.env.OPENAI_MODEL?.trim() || "gpt-5.6-luna";
  const apiKey = process.env.OPENAI_API_KEY?.trim();
  const sideEffectTool = intake.kind === "allowlisted_adapter" ? "complete_purchase" : "payment.charge";
  const reconcileTool = intake.kind === "allowlisted_adapter" ? "get_order_status" : "payment.status";
  if (!apiKey) return fallbackPlan(sideEffectTool, model, "openai_key_missing");

  const developerPrompt = [
    "You plan one bounded synthetic crash test for an AI agent.",
    `The only allowed experiment is commit_then_timeout on ${sideEffectTool} in a fake world; reconcile with ${reconcileTool} after an unknown timeout.`,
    "Do not claim that repository source code was executed or that source behavior was observed.",
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
                text: JSON.stringify({
                  mission,
                  repository_url: repositoryUrl,
                  intake: plannerEvidence(intake),
                }),
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
    if (!response.ok) return fallbackPlan(sideEffectTool, model, "openai_provider_non_2xx");
    let payload: { id?: unknown };
    try {
      payload = (await response.json()) as { id?: unknown };
    } catch {
      return fallbackPlan(sideEffectTool, model, "openai_response_parse_failed");
    }
    const parsed = parsePlan(extractOutputText(payload));
    if (!parsed) return fallbackPlan(sideEffectTool, model, "openai_response_parse_failed");
    return {
      ...parsed,
      model,
      responseId: typeof payload.id === "string" ? payload.id : null,
      usedOpenAI: true,
      analysisStage: "completed",
      reasonCode: null,
    };
  } catch (error) {
    const reasonCode: HostedOpenAIReasonCode = error instanceof DOMException
      && ["AbortError", "TimeoutError"].includes(error.name)
      ? "openai_timeout"
      : error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)
        ? "openai_timeout"
        : "openai_provider_failed";
    return fallbackPlan(sideEffectTool, model, reasonCode);
  }
}

function fallbackPlan(
  sideEffectTool = "payment.charge",
  model = process.env.OPENAI_MODEL?.trim() || "gpt-5.6-luna",
  reasonCode: HostedOpenAIReasonCode = "not_attempted",
): HostedPlan {
  const adapter = sideEffectTool === "complete_purchase";
  return {
    expectedEvidence: adapter
      ? "첫 complete_purchase가 local ledger에 커밋된 뒤 timeout을 반환하고, 상태 확인 없이 같은 cart를 재호출하는지 trace와 ledger에서 확인합니다."
      : FALLBACK_EVIDENCE,
    model,
    rationale: adapter
      ? "UCP complete_purchase는 되돌리기 어려운 작업이므로, 완료 직후 응답 유실과 재시도를 한 변수로 재현합니다."
      : FALLBACK_RATIONALE,
    responseId: null,
    usedOpenAI: false,
    analysisStage: reasonCode === "not_attempted"
      ? "not_attempted"
      : reasonCode === "openai_key_missing"
        ? "unavailable"
        : "failed",
    reasonCode,
  };
}

export async function POST(request: Request) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "invalid JSON body" }, { status: 400 });
  }

  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return Response.json({ detail: "request body must be an object" }, { status: 422 });
  }
  const body = payload as RunRequest;
  if (!body.target || typeof body.target !== "object" || Array.isArray(body.target)) {
    return Response.json({ detail: "target object is required" }, { status: 422 });
  }

  const rootFields = Object.keys(body as Record<string, unknown>);
  const targetFields = Object.keys(body.target as Record<string, unknown>);
  if (
    rootFields.some((field) => !["input", "target"].includes(field))
    || targetFields.some((field) => !["mission", "repository_url", "requested_ref"].includes(field))
  ) {
    return Response.json({ detail: "unsupported request field" }, { status: 422 });
  }
  const repositoryUrl = typeof body.target?.repository_url === "string"
    ? body.target.repository_url.trim()
    : "";
  const requestedRef = typeof body.target?.requested_ref === "string"
    ? body.target.requested_ref.trim()
    : "";
  const mission = typeof body.target?.mission === "string"
    ? body.target.mission.trim()
    : "";
  if (!repositoryUrl || repositoryUrl.length > 500) {
    return Response.json({ detail: "repository_url is required and must be at most 500 characters" }, { status: 422 });
  }
  if (!githubCoordinates(repositoryUrl)) {
    return Response.json({ detail: "repository_url must be a canonical https://github.com/owner/repository URL" }, { status: 422 });
  }
  if (!requestedRef || requestedRef.length > 200 || /[\u0000-\u001f\u007f]/.test(requestedRef)) {
    return Response.json({ detail: "requested_ref is required and must be at most 200 characters" }, { status: 422 });
  }
  if (!mission || mission.length > 2_000) {
    return Response.json({ detail: "mission is required and must be at most 2000 characters" }, { status: 422 });
  }
  if (body.input !== undefined) {
    if (typeof body.input !== "string" || !body.input.trim() || body.input.length > 4_000) {
      return Response.json({ detail: "legacy input must be a non-empty string of at most 4000 characters" }, { status: 422 });
    }
    const expectedInput = [
      "NIGHTMARE LAB에서 다음 Agent source를 가상 환경에서 안전하게 시험해 주세요.",
      `Agent source: ${repositoryUrl}`,
      `버전: ${requestedRef}`,
      `맡길 일: ${mission}`,
      "실제 외부 서비스를 호출하거나 상태를 바꾸지 말고, 관찰한 사실·추정 원인·제안한 해결책·재검증 결과를 구분해 주세요.",
    ].join("\n");
    if (body.input.trim() !== expectedInput) {
      return Response.json({ detail: "legacy input conflicts with canonical target fields" }, { status: 422 });
    }
  }
  const support = classifyHostedMission(mission);
  const source = await resolveGitHubRef(repositoryUrl, requestedRef);
  const runId = crypto.randomUUID();
  const initialPlan = fallbackPlan();
  const baseContext: HostedRunContext = {
    diagnosticCompleted: false,
    executionScope: "none",
    mission,
    model: initialPlan.model,
    openaiAnalysisCompleted: false,
    openaiAnalysisStage: initialPlan.analysisStage,
    openaiReasonCode: initialPlan.reasonCode,
    openaiResponseId: initialPlan.responseId,
    openaiUsed: initialPlan.usedOpenAI,
    planExpectedEvidence: initialPlan.expectedEvidence,
    planRationale: initialPlan.rationale,
    repositoryUrl,
    requestedRef,
    resolvedSha: source.resolvedSha,
    retrievedAt: source.retrievedAt,
    runId,
    sourceResolved: Boolean(source.resolvedSha),
    sourceResolver: source.resolver,
    supportDetail: support.detail,
    supportDomain: support.domain,
    supportMissionId: support.missionId,
    supportReason: support.reason,
    supportStatus: support.status,
  };
  let hostedIntake: HostedIntake;
  if (support.status === "unsupported") {
    hostedIntake = createHostedFallbackIntake(
      baseContext,
      source.resolvedSha ? "source_preflight_failed" : "source_unresolved",
    );
  } else if (source.resolvedSha && isExactUcpSource(repositoryUrl, source.resolvedSha)) {
    try {
      const adapter = await fetchHostedUcpAdapter(repositoryUrl, source.resolvedSha);
      if (!adapter) throw new Error("allowlisted adapter was not selected");
      hostedIntake = await createHostedAdapterIntake(baseContext, adapter.adapter, adapter.sourceFile);
    } catch {
      hostedIntake = createHostedFallbackIntake(baseContext, "source_preflight_failed");
    }
  } else if (source.resolvedSha) {
    const intake = await fetchHostedManifest(repositoryUrl, source.resolvedSha);
    if (intake.kind === "found") {
      hostedIntake = await createHostedOwnerIntake(baseContext, intake.manifest);
    } else if (intake.kind === "missing") {
      hostedIntake = await createHostedCompatibilityIntake(baseContext);
    } else {
      hostedIntake = createHostedFallbackIntake(baseContext, "source_preflight_failed");
    }
  } else {
    hostedIntake = createHostedFallbackIntake(baseContext, "source_unresolved");
  }
  const shouldPlan = support.status !== "unsupported"
    && (hostedIntake.kind === "owner_manifest" || hostedIntake.kind === "allowlisted_adapter")
    && !hostedIntake.pack_selection?.stop;
  const plan = shouldPlan
    ? await planWithOpenAI(mission, repositoryUrl, hostedIntake)
    : initialPlan;
  const context: HostedRunContext = {
    ...baseContext,
    intake: hostedIntake,
    diagnosticCompleted: executionScopeForIntake(hostedIntake) === "synthetic_archetype"
      || executionScopeForIntake(hostedIntake) === "allowlisted_adapter",
    executionScope: executionScopeForIntake(hostedIntake),
    model: plan.model,
    openaiAnalysisCompleted: plan.analysisStage === "completed",
    openaiAnalysisStage: plan.analysisStage,
    openaiReasonCode: plan.reasonCode,
    openaiResponseId: plan.responseId,
    openaiUsed: plan.usedOpenAI,
    planExpectedEvidence: plan.expectedEvidence,
    planRationale: plan.rationale,
  };
  let runContext: string;
  try {
    runContext = await sealRunContext(context);
  } catch {
    return Response.json(
      { detail: "실행 보안 설정을 확인할 수 없습니다." },
      { headers: { "Cache-Control": "no-store" }, status: 503 },
    );
  }
  const eventsUrl = `/api/runs/${encodeURIComponent(runId)}/events?run_context=${encodeURIComponent(runContext)}`;

  return Response.json(
    {
      run_id: runId,
      status: "queued",
      mode: hostedIntake.kind === "compatibility_only"
        ? "compatibility_only"
        : plan.usedOpenAI ? "openai_hosted" : "offline_demo",
      intake_mode: hostedIntake.kind,
      events_url: eventsUrl,
    },
    { headers: { "Cache-Control": "no-store" }, status: 202 },
  );
}

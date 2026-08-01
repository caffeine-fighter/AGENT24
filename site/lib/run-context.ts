import type { HostedRunContext } from "@/lib/hosted-lab";

const CONTEXT_VERSION = 1;
const DEFAULT_TTL_MS = 5 * 60 * 1_000;
const MAX_TTL_MS = 15 * 60 * 1_000;
const encoder = new TextEncoder();

type RunContextEnvelope = {
  context: HostedRunContext;
  expires_at: number;
  issued_at: number;
  run_id: string;
  version: typeof CONTEXT_VERSION;
};

export type RunContextResult =
  | { context: HostedRunContext; ok: true }
  | { detail: string; ok: false; status: 400 | 401 | 404 | 410 };

type SecretGlobal = typeof globalThis & {
  __agent24RunContextSecretV1?: Uint8Array;
};

function ephemeralSecret(): Uint8Array {
  const secretGlobal = globalThis as SecretGlobal;
  if (!secretGlobal.__agent24RunContextSecretV1) {
    secretGlobal.__agent24RunContextSecretV1 = crypto.getRandomValues(new Uint8Array(32));
  }
  return secretGlobal.__agent24RunContextSecretV1;
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const buffer = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buffer).set(bytes);
  return buffer;
}

function secretBytes(): ArrayBuffer {
  const configured = process.env.RUN_CONTEXT_SECRET?.trim();
  if (!configured) return toArrayBuffer(ephemeralSecret());
  const configuredBytes = encoder.encode(configured);
  if (configuredBytes.byteLength < 32) throw new Error("RUN_CONTEXT_SECRET must contain at least 32 bytes");
  return toArrayBuffer(configuredBytes);
}

function contextTtlMs(): number {
  const configured = Number(process.env.RUN_CONTEXT_TTL_MS);
  return Number.isInteger(configured) && configured >= 1 && configured <= MAX_TTL_MS
    ? configured
    : DEFAULT_TTL_MS;
}

function encodeBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function decodeBase64Url(value: string): Uint8Array | null {
  if (!value || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  try {
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

async function encryptionKey(): Promise<CryptoKey> {
  const digest = await crypto.subtle.digest("SHA-256", secretBytes());
  return crypto.subtle.importKey("raw", digest, "AES-GCM", false, ["decrypt", "encrypt"]);
}

function validContext(value: unknown, runId: string): value is HostedRunContext {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const context = value as Partial<HostedRunContext>;
  const boundedString = (candidate: unknown, max: number) =>
    typeof candidate === "string" && candidate.length > 0 && candidate.length <= max;
  return context.runId === runId
    && boundedString(context.mission, 2_000)
    && boundedString(context.model, 80)
    && (context.openaiResponseId === null || boundedString(context.openaiResponseId, 120))
    && typeof context.openaiUsed === "boolean"
    && boundedString(context.planExpectedEvidence, 500)
    && boundedString(context.planRationale, 500)
    && boundedString(context.repositoryUrl, 500)
    && boundedString(context.requestedRef, 200)
    && (context.resolvedSha === null || (typeof context.resolvedSha === "string" && /^[a-f0-9]{40}$/.test(context.resolvedSha)))
    && boundedString(context.sourceResolver, 80)
    && boundedString(context.supportDetail, 500)
    && ["money", "communication", "time", "data", "cross_domain", "unclassified"].includes(String(context.supportDomain))
    && (context.supportMissionId === null || boundedString(context.supportMissionId, 80))
    && ["", "unsupported_input"].includes(String(context.supportReason))
    && ["supported", "unsupported", "unclassified"].includes(String(context.supportStatus));
}

export async function sealRunContext(
  context: HostedRunContext,
  now = Date.now(),
): Promise<string> {
  const envelope: RunContextEnvelope = {
    context,
    expires_at: now + contextTtlMs(),
    issued_at: now,
    run_id: context.runId,
    version: CONTEXT_VERSION,
  };
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    {
      additionalData: encoder.encode(context.runId),
      iv,
      name: "AES-GCM",
      tagLength: 128,
    },
    await encryptionKey(),
    encoder.encode(JSON.stringify(envelope)),
  );
  return `v${CONTEXT_VERSION}.${encodeBase64Url(iv)}.${encodeBase64Url(new Uint8Array(ciphertext))}`;
}

export async function openRunContext(
  token: string | null,
  expectedRunId: string,
  now = Date.now(),
): Promise<RunContextResult> {
  if (!token) return { detail: "실행 정보를 찾을 수 없습니다.", ok: false, status: 404 };
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== `v${CONTEXT_VERSION}`) {
    return { detail: "실행 정보를 확인할 수 없습니다.", ok: false, status: 400 };
  }
  const iv = decodeBase64Url(parts[1]);
  const ciphertext = decodeBase64Url(parts[2]);
  if (!iv || iv.length !== 12 || !ciphertext || ciphertext.length <= 16) {
    return { detail: "실행 정보를 확인할 수 없습니다.", ok: false, status: 400 };
  }

  let envelope: Partial<RunContextEnvelope>;
  try {
    const plaintext = await crypto.subtle.decrypt(
      {
        additionalData: encoder.encode(expectedRunId),
        iv: toArrayBuffer(iv),
        name: "AES-GCM",
        tagLength: 128,
      },
      await encryptionKey(),
      toArrayBuffer(ciphertext),
    );
    envelope = JSON.parse(new TextDecoder().decode(plaintext)) as Partial<RunContextEnvelope>;
  } catch {
    return { detail: "실행 정보를 확인할 수 없습니다.", ok: false, status: 401 };
  }
  if (
    envelope.version !== CONTEXT_VERSION
    || envelope.run_id !== expectedRunId
    || envelope.context?.runId !== expectedRunId
  ) {
    return { detail: "실행 정보를 찾을 수 없습니다.", ok: false, status: 404 };
  }
  if (
    !Number.isInteger(envelope.issued_at)
    || !Number.isInteger(envelope.expires_at)
    || Number(envelope.expires_at) <= Number(envelope.issued_at)
    || Number(envelope.expires_at) - Number(envelope.issued_at) > MAX_TTL_MS
  ) {
    return { detail: "실행 정보를 확인할 수 없습니다.", ok: false, status: 400 };
  }
  if (now >= Number(envelope.expires_at)) {
    return { detail: "실행 정보가 만료됐습니다. 새 실험을 시작해 주세요.", ok: false, status: 410 };
  }
  if (!validContext(envelope.context, expectedRunId)) {
    return { detail: "실행 정보를 확인할 수 없습니다.", ok: false, status: 400 };
  }
  return { context: envelope.context, ok: true };
}

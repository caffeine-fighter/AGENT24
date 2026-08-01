declare const __AGENT24_BUILD_COMMIT__: string;

const FULL_SHA = /^[a-f0-9]{40}$/i;
const injectedCommit =
  typeof __AGENT24_BUILD_COMMIT__ === "string"
    ? __AGENT24_BUILD_COMMIT__.trim().toLowerCase()
    : "";

export const BUILD_COMMIT = FULL_SHA.test(injectedCommit) ? injectedCommit : null;

export function isBuildSource(
  owner: string,
  repository: string,
  requestedRef: string,
): boolean {
  if (!BUILD_COMMIT) return false;
  const isDefaultRepository =
    owner.toLowerCase() === "caffeine-fighter" && repository.toLowerCase() === "agent24";
  const normalizedRef = requestedRef.trim().toLowerCase();
  return isDefaultRepository && (normalizedRef === "main" || normalizedRef === BUILD_COMMIT);
}

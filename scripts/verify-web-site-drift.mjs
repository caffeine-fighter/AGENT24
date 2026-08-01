import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const MIRRORS = [
  ["index.html", "index.html"],
  ["styles.css", "styles.css"],
  ["src/core.mjs", "src/core.mjs"],
];

const LOCAL_TIMEOUT = "  const timeout = setTimeout(() => controller.abort(), 1600);";
const HOSTED_TIMEOUT = [
  "  // A hosted Responses API planning turn can take longer than a local fixture.",
  "  // Keep the fallback bounded while allowing the real server-side call to finish.",
  "  const timeout = setTimeout(() => controller.abort(), 22000);",
].join("\n");

function count(haystack, needle) {
  return haystack.split(needle).length - 1;
}

export async function verifyWebSiteDrift(repoRoot) {
  const webRoot = path.join(repoRoot, "web");
  const siteRoot = path.join(repoRoot, "site", "public", "demo");
  for (const [webPath, sitePath] of MIRRORS) {
    const [webBytes, siteBytes] = await Promise.all([
      readFile(path.join(webRoot, webPath)),
      readFile(path.join(siteRoot, sitePath)),
    ]);
    if (!webBytes.equals(siteBytes)) {
      throw new Error(`VERIFY drift FAIL: shared file differs (${webPath}). Recover: synchronize web/ and site/public/demo/.`);
    }
  }

  const [webApp, hostedApp] = await Promise.all([
    readFile(path.join(webRoot, "src", "app.mjs"), "utf8"),
    readFile(path.join(siteRoot, "src", "app.mjs"), "utf8"),
  ]);
  if (count(webApp, LOCAL_TIMEOUT) !== 1 || count(hostedApp, HOSTED_TIMEOUT) !== 1) {
    throw new Error("VERIFY drift FAIL: the documented timeout adapter is missing or duplicated. Recover: keep only local 1600 ms and hosted 22000 ms timeout blocks.");
  }
  if (webApp.replace(LOCAL_TIMEOUT, HOSTED_TIMEOUT) !== hostedApp) {
    throw new Error("VERIFY drift FAIL: app contract differs beyond the documented timeout. Recover: synchronize app.mjs and retain only the bounded timeout adapter.");
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  try {
    await verifyWebSiteDrift(repoRoot);
    process.stdout.write("VERIFY drift contract PASS\n");
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : "VERIFY drift FAIL: unknown drift error."}\n`);
    process.exitCode = 1;
  }
}

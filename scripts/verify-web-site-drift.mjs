import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const MIRRORS = [
  ["index.html", "index.html"],
  ["styles.css", "styles.css"],
  ["src/core.mjs", "src/core.mjs"],
];

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
  if (webApp !== hostedApp) {
    throw new Error("VERIFY drift FAIL: shared file differs (src/app.mjs). Recover: synchronize web/src/app.mjs and site/public/demo/src/app.mjs.");
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

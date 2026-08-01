import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { verifyWebSiteDrift } from "../verify-web-site-drift.mjs";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

test("web/site drift accepts only the documented timeout adapter", async () => {
  await verifyWebSiteDrift(REPO_ROOT);
});

test("web/site drift rejects a shared contract mismatch", async () => {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "agent24-drift-"));
  try {
    await cp(path.join(REPO_ROOT, "web"), path.join(temporaryRoot, "web"), { recursive: true });
    await cp(path.join(REPO_ROOT, "site", "public", "demo"), path.join(temporaryRoot, "site", "public", "demo"), { recursive: true });
    const target = path.join(temporaryRoot, "site", "public", "demo", "src", "core.mjs");
    const original = await readFile(target, "utf8");
    await writeFile(target, `${original}\n// intentional negative-test drift\n`, "utf8");
    await assert.rejects(() => verifyWebSiteDrift(temporaryRoot), /VERIFY drift FAIL: shared file differs/);
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
});

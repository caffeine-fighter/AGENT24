import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer, request as proxyRequest } from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";

const PUBLIC_PORT = 4173;
const SERVER_PORT = 4175;
const CLIENT_ROOT = path.resolve(process.cwd(), "dist", "client");
const MIME_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".mjs", "text/javascript; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".webp", "image/webp"],
]);

const application = spawn(
  process.execPath,
  [path.resolve("node_modules", "vinext", "dist", "cli.js"), "start", "--hostname", "127.0.0.1", "--port", String(SERVER_PORT)],
  {
    env: process.env,
    stdio: ["ignore", "inherit", "inherit"],
  },
);

async function waitForApplication() {
  const deadline = Date.now() + 25_000;
  while (Date.now() < deadline) {
    if (application.exitCode !== null) throw new Error("production application exited before health check");
    try {
      const response = await fetch(`http://127.0.0.1:${SERVER_PORT}/health`);
      if (response.ok) return;
    } catch {
      // The bounded readiness loop is the only retry in this test adapter.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("production application health check timed out");
}

function proxy(request, response) {
  const upstream = proxyRequest(
    {
      headers: { ...request.headers, host: `127.0.0.1:${SERVER_PORT}` },
      host: "127.0.0.1",
      method: request.method,
      path: request.url,
      port: SERVER_PORT,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", () => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "application/json" });
    response.end('{"detail":"local production proxy failed"}');
  });
  request.pipe(upstream);
}

const server = createServer(async (request, response) => {
  if (!["GET", "HEAD"].includes(request.method ?? "") || !request.url) {
    proxy(request, response);
    return;
  }
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(request.url, `http://127.0.0.1:${PUBLIC_PORT}`).pathname);
  } catch {
    response.writeHead(400).end();
    return;
  }
  const candidate = path.resolve(CLIENT_ROOT, `.${pathname}`);
  const insideClientRoot = candidate === CLIENT_ROOT || candidate.startsWith(`${CLIENT_ROOT}${path.sep}`);
  if (insideClientRoot) {
    try {
      const metadata = await stat(candidate);
      if (metadata.isFile()) {
        response.writeHead(200, {
          "content-length": metadata.size,
          "content-type": MIME_TYPES.get(path.extname(candidate).toLowerCase()) ?? "application/octet-stream",
          "x-content-type-options": "nosniff",
        });
        if (request.method === "HEAD") response.end();
        else createReadStream(candidate).pipe(response);
        return;
      }
    } catch {
      // Dynamic app routes are handled by the actual production server below.
    }
  }
  proxy(request, response);
});

function shutdown() {
  server.close();
  if (application.exitCode === null) application.kill();
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
application.once("exit", (code) => {
  if (code && server.listening) process.exitCode = code;
});

try {
  await waitForApplication();
  server.listen(PUBLIC_PORT, "127.0.0.1");
} catch {
  shutdown();
  process.exitCode = 1;
}

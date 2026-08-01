import { createServer } from "node:http";

const SHA = "a".repeat(40);
const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:4174");
  if (request.method === "GET" && url.pathname === "/health") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end('{"status":"ok"}');
    return;
  }
  if (
    request.method === "GET"
    && url.pathname === "/repos/caffeine-fighter/AGENT24/commits/main"
    && url.search === ""
  ) {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ sha: SHA }));
    return;
  }
  response.writeHead(404, { "content-type": "application/json" });
  response.end('{"message":"not found"}');
});

server.listen(4174, "127.0.0.1");

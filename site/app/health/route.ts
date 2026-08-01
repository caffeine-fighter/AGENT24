import { BUILD_COMMIT } from "@/lib/build-provenance";

export const runtime = "edge";

export async function GET() {
  return Response.json({
    status: "ok",
    mode: process.env.OPENAI_API_KEY?.trim() ? "openai_hosted" : "offline_demo",
    openai_configured: Boolean(process.env.OPENAI_API_KEY?.trim()),
    run_context_secret_configured: new TextEncoder().encode(process.env.RUN_CONTEXT_SECRET?.trim() ?? "").byteLength >= 32,
    build_commit: BUILD_COMMIT,
    deployment_provenance: BUILD_COMMIT ? "git-build-commit" : "unavailable",
    default_source_resolver: "github-api",
    sdk: "responses-api",
    safety_boundary: "SIMULATION_ONLY",
  });
}
